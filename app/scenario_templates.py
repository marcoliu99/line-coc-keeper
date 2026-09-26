"""Versioned Traditional Chinese scenario records for scenario RAG."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import db, scenario_library, scenario_rag
from app.config import IMPORT_DIR, LLM_PROVIDER, SCENARIO_RAG_EMBEDDING_MODEL
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}
_LOCALE = "zh-TW"
_VERSION = 1
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
            }, "required": ["type", "name", "aliases", "public_text",
                            "kp_text", "rule_text", "keywords", "uncertainty"],
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
    chapters = [c for c in manifest.get("chapters", []) if c.get("kind") == "playable"]
    pages = scenario_rag.split_pages(text) or [(1, text)]
    result: list[dict[str, Any]] = []
    for page, page_text in pages:
        chapter = next((c["id"] for c in chapters
                        if c["start_page"] <= page <= c["end_page"]), "")
        if not chapter:
            continue
        # Paragraph boundaries keep a scene/rule together when possible.
        paragraphs = re.split(r"\n\s*\n", page_text)
        number = 0
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            for start in range(0, len(paragraph), 3000):
                number += 1
                result.append({"id": f"p{page}-b{number}", "page": page,
                               "chapter_id": chapter, "text": paragraph[start:start + 3000]})
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


def _validate(scenario_id: str, records: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
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
        grouped.setdefault(source_id, []).append(record)
    missing = set(source_blocks) - set(grouped)
    if missing:
        raise ValueError(f"模板漏掉 {len(missing)} 個來源區塊")
    issues: list[str] = []
    for source_id, block in source_blocks.items():
        translated = " ".join(str(r.get(k, "")) for r in grouped[source_id]
                              for k in ("public_text", "kp_text", "rule_text"))
        absent = set(_NUMBER.findall(block["text"])) - set(_NUMBER.findall(translated))
        if absent:
            issues.append(f"{source_id} 數值未對齊：{', '.join(sorted(absent)[:10])}")
        if _NEGATIVE.search(block["text"]) and not re.search(r"不|無|未|非|禁止|不能", translated):
            issues.append(f"{source_id} 否定條件待核對")
        if any(str(r.get("uncertainty", "")).strip() for r in grouped[source_id]):
            issues.append(f"{source_id} 有待釐清翻譯")
    return manifest["content_hash"], _chapter_hash(manifest), issues


def _generate_sync(scenario_id: str, source_hash: str, chapter_hash: str) -> str:
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        raise RuntimeError("未設定可用的 LLM_PROVIDER")
    manifest, text = _source(scenario_id)
    if manifest["content_hash"] != source_hash or _chapter_hash(manifest) != chapter_hash:
        raise ValueError("劇本已重新解析，請重新排程")
    records: list[dict[str, Any]] = []
    glossary: dict[str, str] = {}
    for block in _blocks(manifest, text):
        response = provider.analyze_text(
            block["text"], _TOOL,
            "把這一個劇本來源區塊完整翻成繁體中文。可拆成多筆有意義的場景、線索、規則、NPC 或事件記錄，"
            "但不可略過內容、補造規則或改寫骰式/數值/否定條件。公開描述與 KP 秘密分欄；"
            "所有輸出欄位用繁體中文，原文姓名可保留在 aliases。"
            f"來源 ID：{block['id']}；頁碼：{block['page']}。"
            f"已確認術語對照：{json.dumps(glossary, ensure_ascii=False)[:1500]}。",
        )
        items = (response or {}).get("records")
        if not isinstance(items, list) or not items:
            raise RuntimeError(f"{block['id']} 翻譯未產生記錄")
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise RuntimeError(f"{block['id']} 記錄格式錯誤")  # noqa: TRY004 - provider output
            visibility = "kp_only" if str(item.get("kp_text", "")).strip() and not str(item.get("public_text", "")).strip() else "public"
            records.append({"id": f"{block['id']}-r{index}", "source_id": block["id"],
                            "page": block["page"], "chapter_id": block["chapter_id"],
                            "visibility": visibility, "type": str(item.get("type", "scene")),
                            "name": str(item.get("name", "")), "aliases": item.get("aliases") or [],
                            "public_text": str(item.get("public_text", "")),
                            "kp_text": str(item.get("kp_text", "")),
                            "rule_text": str(item.get("rule_text", "")),
                            "keywords": item.get("keywords") or [],
                            "uncertainty": str(item.get("uncertainty", "")),
                            "source_excerpt": block["text"][:900]})
            for alias in records[-1]["aliases"]:
                if isinstance(alias, str) and alias.strip():
                    glossary.setdefault(alias, records[-1]["name"])
    _, _, issues = _validate(scenario_id, records)
    return _save_variant(scenario_id, source_hash, chapter_hash, records, issues, origin="generated")


async def _run_job(scenario_id: str, source_hash: str, chapter_hash: str) -> None:
    try:
        async with _gate:
            db.set_json("scenario_template_jobs", scenario_id,
                        {"status": "processing", "source_hash": source_hash, "chapter_hash": chapter_hash})
            try:
                variant_id = await asyncio.to_thread(_generate_sync, scenario_id, source_hash, chapter_hash)
            except Exception as exc:  # noqa: BLE001 - background job must record provider failures
                db.set_json("scenario_template_jobs", scenario_id,
                            {"status": "failed", "source_hash": source_hash,
                             "chapter_hash": chapter_hash, "error": str(exc)[:300]})
            else:
                db.set_json("scenario_template_jobs", scenario_id,
                            {"status": "review_required", "source_hash": source_hash,
                             "chapter_hash": chapter_hash, "variant_id": variant_id})
    finally:
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
           and v.get("review_status") in ("review_required", "approved")
           for v in _all_variants(scenario_id)):
        return False
    db.set_json("scenario_template_jobs", scenario_id,
                {"status": "queued", "source_hash": source_hash, "chapter_hash": chapter_hash})
    _tasks[scenario_id] = asyncio.create_task(_run_job(scenario_id, source_hash, chapter_hash))
    return True


def resume_pending_jobs() -> None:
    for scenario_id in db.list_keys("scenario_template_jobs"):
        job = db.get_json("scenario_template_jobs", scenario_id) or {}
        if job.get("status") in ("queued", "processing"):
            try:
                saved = next((v for v in _all_variants(scenario_id)
                              if v.get("source_hash") == job.get("source_hash")
                              and v.get("chapter_hash") == job.get("chapter_hash")
                              and v.get("review_status") in ("review_required", "approved")), None)
                if saved is not None:
                    db.set_json("scenario_template_jobs", scenario_id,
                                {**job, "status": saved["review_status"],
                                 "variant_id": saved["variant_id"]})
                    continue
                queue_generation(scenario_id)
            except (FileNotFoundError, ValueError):
                db.delete_json("scenario_template_jobs", scenario_id)


def status(scenario_id: str) -> dict[str, Any]:
    manifest, _ = _source(scenario_id)
    job = db.get_json("scenario_template_jobs", scenario_id) or {}
    if job and (job.get("source_hash") != manifest["content_hash"] or
                job.get("chapter_hash") != _chapter_hash(manifest)):
        job = {**job, "status": "stale"}
    return {"job": job,
            "variants": [{**v, "current": v.get("source_hash") == manifest["content_hash"]
                          and v.get("chapter_hash") == _chapter_hash(manifest)}
                         for v in _all_variants(scenario_id)]}


def _read_variant(scenario_id: str, variant_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, _ = _source(scenario_id)
    path = _variant_dir(scenario_id, manifest["content_hash"], variant_id)
    variant = scenario_library._read_json(path / "manifest.json", None)
    records = scenario_library._read_json(path / "records.json", None)
    if not isinstance(variant, dict) or not isinstance(records, list):
        raise FileNotFoundError(variant_id)
    if variant.get("chapter_hash") != _chapter_hash(manifest):
        raise ValueError("中文模板章節版本已過期")
    return variant, records


def approve(scenario_id: str, variant_id: str) -> None:
    variant, records = _read_variant(scenario_id, variant_id)
    _, _, issues = _validate(scenario_id, records)
    if issues:
        raise ValueError("尚有翻譯疑點：" + "；".join(issues[:3]))
    variant["review_status"] = "approved"
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
        record["source_excerpt"] = source_blocks[record["source_id"]]["text"][:900]
    return _save_variant(scenario_id, source_hash, chapter_hash, records, issues, origin="manual")


def preview(scenario_id: str, variant_id: str, limit: int = 1800) -> str:
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
                     f"[{record['visibility']}]\n{record.get('public_text') or record.get('kp_text') or record.get('rule_text')}")
        if record.get("uncertainty"):
            lines.append(f"待釐清：{record['uncertainty']}")
        if sum(map(len, lines)) > limit:
            lines.append("…其餘內容請開啟劇本庫中的 template.md 校對。")
            break
    return "\n".join(lines)[:limit]


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
                         variant_id, window, SCENARIO_RAG_EMBEDDING_MODEL, "record-v1"],
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
