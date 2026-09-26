"""Versioned Traditional Chinese scenario records for scenario RAG."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import db, scenario_library, scenario_rag
from app.config import IMPORT_DIR, LLM_PROVIDER, SCENARIO_RAG_EMBEDDING_MODEL
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}
_LOCALE = "zh-TW"
_VERSION = 2
_GENERATOR_VERSION = "source-units-v2"
_RULE_FIELDS = ("trigger", "check", "success", "failure", "exceptions")
_SAFE_VARIANT = re.compile(r"zh-TW-[a-f0-9]{12}")
_NUMBER = re.compile(r"(?i)\b\d+d\d+(?:[+-]\d+)?\b|\b\d+(?:\.\d+)?%?\b")
_NEGATIVE = re.compile(r"\b(?:not|never|without|cannot|no)\b", re.IGNORECASE)
_TOOL = {
    "name": "report_chinese_scenario_records",
    "description": "將來源劇本區塊忠實翻成繁體中文結構化記錄，所有規則、條件與數值原樣保留。",
    "input_schema": {
        "type": "object",
        "properties": {"records": {"type": "array", "items": {
            "type": "object", "properties": {
                "type": {"type": "string"},
                "name": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "public_text": {"type": "string"},
                "kp_text": {"type": "string"},
                "rule_text": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "uncertainty": {"type": "string"},
                "rules": {"type": "array", "items": {"type": "object", "properties": {
                    field: {"type": "object", "properties": {"text": {"type": "string"}, "source_quote": {"type": "string"}}, "required": ["text", "source_quote"]}
                    for field in _RULE_FIELDS
                }}},
                "related_source_ids": {"type": "array", "items": {"type": "string"}},
            }, "required": ["type", "name", "aliases", "public_text",
                            "kp_text", "rule_text", "keywords", "uncertainty", "rules", "related_source_ids"],
        }}},
        "required": ["records"],
    },
}
_tasks: dict[str, asyncio.Task[None]] = {}
_gate = asyncio.Semaphore(1)
_logger = logging.getLogger(__name__)


def _root() -> Path:
    return scenario_library.SCENARIO_LIBRARY_DIR / ".variants"


def _source(scenario_id: str) -> tuple[dict[str, Any], str]:
    root = scenario_library._path(scenario_id)
    manifest = scenario_library._read_json(root / "manifest.json", None)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(scenario_id)
    text = (root / "scenario.txt").read_text(encoding="utf-8")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != manifest.get("content_hash"):
        raise ValueError("劇本來源與 manifest 不一致")
    return manifest, text


def _chapter_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(manifest.get("chapters", []), sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _blocks(manifest: dict[str, Any], text: str) -> list[dict[str, Any]]:
    """Keep heading-based scene/rule units intact across paragraphs and pages.

    Level 1/2 headings start units; subordinate headings stay with their parent.
    Unstructured chapters remain whole and require manual preparation if too big.
    Never silently cut a trigger away from its consequence to fit a request.
    """
    chapters = [c for c in manifest.get("chapters", []) if c.get("kind") == "playable"]
    pages = scenario_rag.split_pages(text) or [(1, text)]
    result: list[dict[str, Any]] = []
    def flush(lines: list[str], source_pages: list[int], heading: str, chapter_id: str) -> None:
        value = "\n".join(lines).strip()
        if value:
            unit_id = f"{chapter_id}-u{len(result) + 1}"
            result.append({"id": unit_id, "page": source_pages[0], "pages": list(source_pages),
                           "chapter_id": chapter_id, "heading": heading, "text": value})
        lines.clear()
        source_pages.clear()

    for chapter in chapters:
        lines: list[str] = []
        source_pages: list[int] = []
        heading = chapter.get("title", chapter["id"])


        for page, body in pages:
            if not chapter["start_page"] <= page <= chapter["end_page"]:
                continue
            for line in body.splitlines():
                if re.match(r"^#{1,2}\s+\S", line):
                    flush(lines, source_pages, heading, chapter["id"])
                    heading = line.lstrip("# ")
                if line.strip() or lines:
                    lines.append(line)
                    if page not in source_pages:
                        source_pages.append(page)
        flush(lines, source_pages, heading, chapter["id"])
    if not result:
        raise ValueError("沒有可翻譯的遊玩章節")
    return result


def _variant_dir(scenario_id: str, source_hash: str, variant_id: str) -> Path:
    scenario_library._path(scenario_id)
    if not re.fullmatch(r"[a-f0-9]{64}", source_hash) or not _SAFE_VARIANT.fullmatch(variant_id):
        raise ValueError("無效的模板版本")
    return _root() / scenario_id / source_hash / _LOCALE / variant_id


def _all_variants(scenario_id: str) -> list[dict[str, Any]]:
    scenario_library._path(scenario_id)
    root = _root() / scenario_id
    return [data for path in root.glob("*/zh-TW/zh-TW-*/manifest.json")
            if isinstance((data := scenario_library._read_json(path, None)), dict)]


def _records_text(records: list[dict[str, Any]], source_hash: str, chapter_hash: str) -> str:
    lines = ["# 中文劇本模板", "", "請編輯下方 JSON 區塊後用 /coc scenario template import 匯入。", "", "```json"]
    lines.append(json.dumps({"source_hash": source_hash, "chapter_hash": chapter_hash,
                             "records": records}, ensure_ascii=False, indent=2))
    lines.extend(["```", ""])
    return "\n".join(lines)


def _save_variant(scenario_id: str, source_hash: str, chapter_hash: str,
                  records: list[dict[str, Any]], issues: list[str], *, origin: str) -> str:
    variant_id = f"zh-TW-{uuid4().hex[:12]}"
    target = _variant_dir(scenario_id, source_hash, variant_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".building-", dir=target.parent))
    try:
        manifest = {"scenario_id": scenario_id, "variant_id": variant_id,
                    "source_hash": source_hash, "chapter_hash": chapter_hash,
                    "locale": _LOCALE, "schema_version": _VERSION,
                    "generator_version": _GENERATOR_VERSION,
                    "origin": origin, "review_status": "review_required",
                    "record_count": len(records), "issues": issues}
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (temporary / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        source_ids = sorted({str(record["source_id"]) for record in records})
        (temporary / "coverage.json").write_text(json.dumps({
            "source_block_count": len(_blocks(*_source(scenario_id))),
            "covered_source_ids": source_ids, "unresolved": issues,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        glossary = {alias: record["name"] for record in records
                    for alias in record.get("aliases", []) if isinstance(alias, str)}
        (temporary / "glossary.json").write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")
        (temporary / "template.md").write_text(
            _records_text(records, source_hash, chapter_hash), encoding="utf-8")
        current, _ = _source(scenario_id)
        if current["content_hash"] != source_hash or _chapter_hash(current) != chapter_hash:
            raise ValueError("劇本已重新解析，模板已過期")
        temporary.replace(target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return variant_id


def _validate(scenario_id: str, records: list[dict[str, Any]], *, partial: bool = False) -> tuple[str, str, list[str]]:
    manifest, text = _source(scenario_id)
    source_blocks = {b["id"]: b for b in _blocks(manifest, text)}
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("模板記錄格式錯誤")  # noqa: TRY004 - user input validation
        record_id = record.get("id")
        source_id = record.get("source_id")
        if not isinstance(record_id, str) or record_id in seen or not isinstance(source_id, str) or source_id not in source_blocks:
            raise ValueError("模板記錄 ID 重複或來源不存在")
        seen.add(record_id)
        block = source_blocks[source_id]
        if record.get("page") != block["page"] or record.get("chapter_id") != block["chapter_id"]:
            raise ValueError("模板頁碼或章節與來源不符")
        if record.get("visibility") not in ("public", "kp_only"):
            raise ValueError("模板可見範圍無效")
        if record.get("visibility") == "kp_only" and str(record.get("public_text", "")).strip():
            raise ValueError("KP 專用記錄不可含公開內容")
        if not str(record.get("name", "")).strip():
            raise ValueError("模板記錄缺少名稱")
        if not isinstance(record.get("aliases"), list) or not all(isinstance(x, str) for x in record["aliases"]):
            raise ValueError("模板別名格式錯誤")
        if not isinstance(record.get("keywords"), list) or not all(isinstance(x, str) for x in record["keywords"]):
            raise ValueError("模板關鍵字格式錯誤")
        if not isinstance(record.get("type"), str) or not record["type"].strip():
            raise ValueError("模板記錄缺少類型")
        for field in ("public_text", "kp_text", "rule_text", "uncertainty"):
            if not isinstance(record.get(field), str):
                raise ValueError(f"模板 {field} 格式錯誤")  # noqa: TRY004 - user input validation
        if not any(str(record.get(k, "")).strip() for k in ("public_text", "kp_text", "rule_text")):
            raise ValueError("模板記錄缺少中文內容")
        if not isinstance(record.get("related_source_ids"), list) or any(
            ref not in source_blocks for ref in record["related_source_ids"]
        ):
            raise ValueError("模板關聯來源不存在")
        rules = record.get("rules")
        if not isinstance(rules, list):
            raise ValueError("模板缺少結構化規則")  # noqa: TRY004 - user import validation
        if record["rule_text"].strip() and not rules:
            raise ValueError("規則摘要必須附上欄位與逐項原文引述")
        for rule in rules:
            if not isinstance(rule, dict) or not rule or set(rule) - set(_RULE_FIELDS):
                raise ValueError("規則欄位無效")
            for evidence in rule.values():
                if not isinstance(evidence, dict) or not isinstance(evidence.get("text"), str) or not evidence["text"].strip():
                    raise ValueError("規則缺少翻譯文字")
                quote = evidence.get("source_quote")
                if not isinstance(quote, str) or not quote.strip() or quote not in block["text"]:
                    raise ValueError("規則原文引述無法定位")
                numbers = lambda text: Counter(x.casefold() for x in _NUMBER.findall(text))
                if numbers(quote) != numbers(evidence["text"]):
                    raise ValueError("規則欄位數值與其原文引述不符")
        grouped.setdefault(source_id, []).append(record)
    missing = set(source_blocks) - set(grouped)
    if missing and not partial:
        raise ValueError(f"模板漏掉 {len(missing)} 個來源區塊")
    if any(len(items) != 1 for items in grouped.values()):
        raise ValueError("每個來源單元必須完整對應一筆記錄")
    issues: list[str] = []
    for source_id, block in source_blocks.items():
        if source_id not in grouped:
            continue
        translated = " ".join(str(r.get(k, "")) for r in grouped[source_id]
                              for k in ("public_text", "kp_text", "rule_text"))
        absent = {n.casefold() for n in _NUMBER.findall(block["text"])} - {n.casefold() for n in _NUMBER.findall(translated)}
        if absent:
            issues.append(f"{source_id} 數值未對齊：{', '.join(sorted(absent)[:10])}")
        if _NEGATIVE.search(block["text"]) and not re.search(r"不|無|未|非|禁止|不能", translated):
            issues.append(f"{source_id} 否定條件待核對")
        if any(str(r.get("uncertainty", "")).strip() for r in grouped[source_id]):
            issues.append(f"{source_id} 有待釐清翻譯")
    return manifest["content_hash"], _chapter_hash(manifest), issues


async def _generate(scenario_id: str, source_hash: str, chapter_hash: str) -> str:
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        raise RuntimeError("未設定可用的 LLM_PROVIDER")
    manifest, text = _source(scenario_id)
    if manifest["content_hash"] != source_hash or _chapter_hash(manifest) != chapter_hash:
        raise ValueError("劇本已重新解析，請重新排程")
    records: list[dict[str, Any]] = []
    units = _blocks(manifest, text)
    glossary: dict[str, str] = {}
    for block in units:
        current, _ = _source(scenario_id)
        if current["content_hash"] != source_hash or _chapter_hash(current) != chapter_hash:
            raise ValueError("來源已變更；停止舊版生成")
        if len(block["text"]) > 16000:
            raise ValueError(f"{block['id']} 超出完整單元預算；請 KP 整理標題或手動匯入，不自動切斷規則")
        checkpoint_key = json.dumps([scenario_id, source_hash, chapter_hash, _GENERATOR_VERSION, block["id"]])
        saved = db.get_json("scenario_template_checkpoints", checkpoint_key)
        if saved is not None:
            _validate(scenario_id, [saved], partial=True)
            records.append(saved)
            for alias in saved.get("aliases", []):
                glossary.setdefault(alias, saved["name"])
            continue
        prompt = (
            "把整個來源單元忠實翻為繁體中文，records 必須恰好一筆。保留所有段落、條件、否定、數值與例外。"
            "public_text 僅含可公開內容；kp_text 含完整秘密翻譯；rule_text 為規則摘要。"
            "rules 每個非空 trigger/check/success/failure/exceptions 欄位都需 text 翻譯及逐字 source_quote。"
            "原文沒寫的欄位省略，不推導規則。related_source_ids 只填已知的來源 ID，未知則留空。"
            f"來源 {block['id']}，標題 {block['heading']}，頁碼 {block['pages']}。"
            f"同章來源目錄：{json.dumps([{'id': u['id'], 'heading': u['heading']} for u in units if u['chapter_id'] == block['chapter_id']], ensure_ascii=False)}"
            f"已確認術語：{json.dumps(glossary, ensure_ascii=False)[:1500]}"
        )
        if LLM_PROVIDER == "openai":
            response = await openai_provider.analyze_text_background(block["text"], _TOOL, prompt)
        else:
            response = await asyncio.to_thread(provider.analyze_text, block["text"], _TOOL, prompt)
        items = (response or {}).get("records")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError(f"{block['id']} 必須產生一筆完整記錄")
        item = items[0]
        record = {**item, "id": f"{block['id']}-r1", "source_id": block["id"],
                  "page": block["page"], "source_pages": block["pages"], "chapter_id": block["chapter_id"],
                  "visibility": "kp_only" if not str(item.get("public_text", "")).strip() else "public",
                  "source_excerpt": block["text"], "source_heading": block["heading"]}
        _validate(scenario_id, [record], partial=True)
        current, _ = _source(scenario_id)
        if current["content_hash"] != source_hash or _chapter_hash(current) != chapter_hash:
            raise ValueError("來源已變更；不保存舊版 checkpoint")
        records.append(record)
        db.set_json("scenario_template_checkpoints", checkpoint_key, record)
        for alias in record.get("aliases", []):
            if isinstance(alias, str):
                glossary.setdefault(alias, str(record.get("name", "")))
    _, _, issues = _validate(scenario_id, records)
    return _save_variant(scenario_id, source_hash, chapter_hash, records, issues, origin="generated")


async def _run_job(scenario_id: str, source_hash: str, chapter_hash: str) -> None:
    current_task = asyncio.current_task()
    try:
        async with _gate:
            if _tasks.get(scenario_id) is not current_task:
                return
            db.set_json("scenario_template_jobs", scenario_id,
                        {"status": "processing", "source_hash": source_hash, "chapter_hash": chapter_hash})
            try:
                started = time.monotonic()
                variant_id = await _generate(scenario_id, source_hash, chapter_hash)
            except Exception as exc:  # noqa: BLE001 - background job must record provider failures
                if _tasks.get(scenario_id) is current_task:
                    db.set_json("scenario_template_jobs", scenario_id,
                                {"status": "failed", "source_hash": source_hash,
                                 "chapter_hash": chapter_hash, "error": str(exc)[:300]})
            else:
                if _tasks.get(scenario_id) is current_task:
                    db.set_json("scenario_template_jobs", scenario_id,
                                {"status": "review_required", "source_hash": source_hash,
                                 "chapter_hash": chapter_hash, "variant_id": variant_id,
                                 "duration_seconds": time.monotonic() - started})
    finally:
        if _tasks.get(scenario_id) is current_task:
            _tasks.pop(scenario_id, None)


def queue_generation(scenario_id: str) -> bool:
    manifest, _ = _source(scenario_id)
    source_hash, chapter_hash = manifest["content_hash"], _chapter_hash(manifest)
    existing = db.get_json("scenario_template_jobs", scenario_id) or {}
    if (existing.get("source_hash") == source_hash and
            existing.get("chapter_hash") == chapter_hash and
            existing.get("status") in ("queued", "processing", "review_required") and
            scenario_id in _tasks and not _tasks[scenario_id].done()):
        return False
    if any(v.get("source_hash") == source_hash and v.get("chapter_hash") == chapter_hash
           and v.get("schema_version") == _VERSION
           and v.get("generator_version") == _GENERATOR_VERSION
           and v.get("review_status") in ("review_required", "approved")
           for v in _all_variants(scenario_id)):
        return False
    db.set_json("scenario_template_jobs", scenario_id,
                {"status": "queued", "source_hash": source_hash, "chapter_hash": chapter_hash})
    _tasks[scenario_id] = asyncio.create_task(_run_job(scenario_id, source_hash, chapter_hash))
    return True


def pause_pending_jobs() -> None:
    """Startup must not restart paid translation, including old automatic jobs."""
    for scenario_id in db.list_keys("scenario_template_jobs"):
        active = _tasks.get(scenario_id)
        if active is not None and not active.done():
            continue  # Discord reconnect must not relabel a manually started live job.
        job = db.get_json("scenario_template_jobs", scenario_id) or {}
        if job.get("status") in ("queued", "processing"):
            db.set_json("scenario_template_jobs", scenario_id, {**job, "status": "paused"})


def export_template(scenario_id: str) -> Path:
    """Export a source-bound blank workbook without any model or embedding call.

    Full original units stay in this private preparation file. Empty translated
    fields and uncertainty markers deliberately prevent approval before editing.
    """
    manifest, text = _source(scenario_id)
    records = []
    for block in _blocks(manifest, text):
        records.append({
            "id": f"{block['id']}-r1", "source_id": block["id"],
            "page": block["page"], "source_pages": block["pages"],
            "chapter_id": block["chapter_id"], "source_heading": block["heading"],
            "source_excerpt": block["text"], "type": "source_unit",
            "name": block["heading"], "aliases": [], "keywords": [],
            "visibility": "kp_only", "public_text": "", "kp_text": "", "rule_text": "",
            "rules": [], "related_source_ids": [], "uncertainty": "尚未翻譯與校對",
        })
    root = IMPORT_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Unique file avoids overwriting the KP's partly translated workbook. Do not
    # derive a path component from the supplied scenario ID or title.
    fd, filename = tempfile.mkstemp(prefix="scenario-template-", suffix=".md", dir=root)
    import os
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(
            "# External Chinese scenario preparation\n\n"
            "Private Keeper workbook: contains unredacted source material.\n"
            "Keep IDs, hashes, chapter and source page fields unchanged. Translate complete units; "
            "do not infer missing rules. Preserve triggers, success/failure, Push, costs, limits and exceptions. "
            "Put rule translations and exact original quotes in rules fields "
            "trigger/check/success/failure/exceptions. Mark public versus KP content explicitly. "
            "Keep unresolved text in uncertainty; clear it only after review. "
            "source_pages are PDF positions, not printed page labels. "
            "Use related_source_ids for dependencies; links do not unlock other chapters.\n\n"
        )
        stream.write(_records_text(records, manifest["content_hash"], _chapter_hash(manifest)))
    return Path(filename)


def status(scenario_id: str) -> dict[str, Any]:
    manifest, _ = _source(scenario_id)
    job = db.get_json("scenario_template_jobs", scenario_id) or {}
    if job and (job.get("source_hash") != manifest["content_hash"] or
                job.get("chapter_hash") != _chapter_hash(manifest)):
        job = {**job, "status": "stale"}
    return {"job": job,
            "variants": [{**v, "current": v.get("source_hash") == manifest["content_hash"]
                          and v.get("chapter_hash") == _chapter_hash(manifest)
                          and v.get("schema_version") == _VERSION
                          and v.get("generator_version") == _GENERATOR_VERSION}
                         for v in _all_variants(scenario_id)]}


def _read_variant(scenario_id: str, variant_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, _ = _source(scenario_id)
    path = _variant_dir(scenario_id, manifest["content_hash"], variant_id)
    variant = scenario_library._read_json(path / "manifest.json", None)
    records = scenario_library._read_json(path / "records.json", None)
    if not isinstance(variant, dict) or not isinstance(records, list):
        raise FileNotFoundError(variant_id)
    if variant.get("schema_version") != _VERSION or variant.get("generator_version") != _GENERATOR_VERSION:
        raise ValueError("模板結構版本已過期，請重新產生或匯入校對")
    if variant.get("chapter_hash") != _chapter_hash(manifest):
        raise ValueError("中文模板章節版本已過期")
    return variant, records


def approve(scenario_id: str, variant_id: str, *, reviewer_id: str) -> None:
    variant, records = _read_variant(scenario_id, variant_id)
    _, _, issues = _validate(scenario_id, records)
    if issues:
        raise ValueError("尚有翻譯疑點：" + "；".join(issues[:3]))
    if not reviewer_id:
        raise ValueError("核准必須記錄校對者")
    variant["review_status"] = "approved"
    variant["reviewed_by"] = reviewer_id
    variant["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    path = _variant_dir(scenario_id, variant["source_hash"], variant_id) / "manifest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(variant, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    job = db.get_json("scenario_template_jobs", scenario_id) or {}
    if job.get("variant_id") == variant_id:
        db.set_json("scenario_template_jobs", scenario_id, {**job, "status": "approved"})


def import_markdown(scenario_id: str, filename: str) -> str:
    name = Path(filename).name
    if name != filename or not name.lower().endswith(".md"):
        raise ValueError("檔名必須是匯入目錄中的 .md")
    root = IMPORT_DIR.resolve()
    raw_path = root / name
    path = raw_path.resolve()
    if raw_path.is_symlink() or path.parent != root or not path.is_file():
        raise FileNotFoundError(name)
    content = path.read_text(encoding="utf-8")
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not match:
        raise ValueError("Markdown 需要包含 records JSON 區塊")
    payload = json.loads(match.group(1))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("模板缺少 records")  # noqa: TRY004 - user input validation
    source_hash, chapter_hash, issues = _validate(scenario_id, records)
    if payload.get("source_hash") != source_hash or payload.get("chapter_hash") != chapter_hash:
        raise ValueError("匯入模板的來源或章節版本已過期")
    source_blocks = {b["id"]: b for b in _blocks(*_source(scenario_id))}
    for record in records:
        record["source_excerpt"] = source_blocks[record["source_id"]]["text"]
    return _save_variant(scenario_id, source_hash, chapter_hash, records, issues, origin="manual")


def preview(scenario_id: str, variant_id: str, limit: int = 1700, page: int = 1) -> str:
    variant, records = _read_variant(scenario_id, variant_id)
    lines = [f"模板 {variant_id}（{variant['review_status']}）"]
    if variant.get("issues"):
        lines.append("待核對：" + "；".join(variant["issues"][:10]))
    chapter = None
    for record in records:
        if chapter != record["chapter_id"]:
            chapter = record["chapter_id"]
            lines.append(f"【{chapter}】")
        lines.append(f"{record['type']}｜第 {record['page']} 頁｜{record['source_id']}：{record['name']} "
                     f"[{record['visibility']}]\n" + json.dumps({k: record.get(k) for k in ('public_text', 'kp_text', 'rules', 'rule_text', 'source_excerpt')}, ensure_ascii=False))
        if record.get("uncertainty"):
            lines.append(f"待釐清：{record['uncertainty']}")
    content = "\n".join(lines)
    pages = max(1, (len(content) + limit - 1) // limit)
    if page < 1 or page > pages:
        raise ValueError(f"預覽頁碼應為 1–{pages}")
    return f"校對預覽 {page}/{pages}；preview 可加頁碼查看完整原文與翻譯。\n" + content[(page - 1) * limit:page * limit]


def _pref_key(group_id: str, scenario_id: str) -> str:
    return json.dumps([group_id, scenario_id], ensure_ascii=False)


def select_variant(group_id: str, scenario_id: str, variant_id: str) -> None:
    require_approved(scenario_id, variant_id)
    db.set_json("scenario_template_preferences", _pref_key(group_id, scenario_id),
                {"variant_id": variant_id})


def require_approved(scenario_id: str, variant_id: str) -> None:
    if variant_id == "original":
        return
    variant, _ = _read_variant(scenario_id, variant_id)
    if variant.get("review_status") != "approved":
        raise ValueError("中文模板尚未通過 KP 校對")


def preferred_variant(group_id: str, scenario_id: str) -> str:
    data = db.get_json("scenario_template_preferences", _pref_key(group_id, scenario_id)) or {}
    variant_id = data.get("variant_id", "original")
    if variant_id != "original":
        try:
            variant, _ = _read_variant(scenario_id, variant_id)
            if variant.get("review_status") != "approved":
                return "original"
        except (FileNotFoundError, ValueError):
            return "original"
    return variant_id


def preference_notice(group_id: str, scenario_id: str) -> str:
    data = db.get_json("scenario_template_preferences", _pref_key(group_id, scenario_id)) or {}
    if data.get("variant_id", "original") != "original" and preferred_variant(group_id, scenario_id) == "original":
        return "原選用的中文模板與目前劇本來源不相容，已改用原文檢索；請查看模板狀態並重新校對。"
    return ""


def index_for_state(state: Any) -> scenario_rag.ScenarioIndex:
    scenario_id = state.scenario_library_id
    variant_id = state.scenario_variant_id
    if scenario_id and variant_id and variant_id != "original":
        try:
            variant, records = _read_variant(scenario_id, variant_id)
            if variant.get("review_status") == "approved":
                window = tuple(state.context_chapter_ids)
                selected = [r for r in records if r.get("chapter_id") in window]
                if selected:
                    digest = hashlib.sha256(json.dumps(
                        [scenario_id, variant["source_hash"], variant["chapter_hash"],
                         variant_id, window, SCENARIO_RAG_EMBEDDING_MODEL, "record-v2"],
                        ensure_ascii=False).encode("utf-8")).hexdigest()
                    return scenario_rag.get_record_index(f"template:{scenario_id}:{digest}", selected)
        except (FileNotFoundError, ValueError):
            pass
    return scenario_rag.get_index(state.group_id, state.scenario_text)


def schedule_index_prewarm(state: Any) -> asyncio.Task[None] | None:
    if not state.scenario_text:
        return None
    if state.scenario_variant_id == "original":
        return scenario_rag.schedule_index_prewarm(state.group_id, state.scenario_text)

    async def _warm() -> None:
        await asyncio.to_thread(index_for_state, state)

    task = asyncio.create_task(_warm())
    task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
    return task


def clean_scenario(scenario_id: str) -> None:
    scenario_library._path(scenario_id)
    task = _tasks.pop(scenario_id, None)
    if task is not None:
        task.cancel()
    shutil.rmtree(_root() / scenario_id, ignore_errors=True)
    db.delete_json("scenario_template_jobs", scenario_id)
    for key in db.list_keys("scenario_template_checkpoints"):
        if json.loads(key)[0] == scenario_id:
            db.delete_json("scenario_template_checkpoints", key)
    for key in db.list_keys("scenario_indexes"):
        if key.startswith(f"template:{scenario_id}:"):
            db.delete_json("scenario_indexes", key)
            scenario_rag._index_cache.pop(key, None)
    for key in db.list_keys("scenario_template_preferences"):
        try:
            if json.loads(key)[1] == scenario_id:
                db.delete_json("scenario_template_preferences", key)
        except (ValueError, IndexError, TypeError):
            continue
