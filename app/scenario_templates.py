"""Versioned Traditional Chinese scenario records for scenario RAG."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import db, scenario_library, scenario_projection, scenario_rag
from app.config import IMPORT_DIR, SCENARIO_RAG_EMBEDDING_MODEL

_LOCALE = "zh-TW"
_VERSION = 3
_COMPILER_VERSION = scenario_projection.VERSION
_RULE_FIELDS = ("trigger", "check", "success", "failure", "exceptions")
_SAFE_VARIANT = re.compile(r"zh-TW-[a-f0-9]{12}")
_NUMBER = re.compile(r"(?i)\b\d+d\d+(?:[+-]\d+)?\b|\b\d+(?:\.\d+)?%?\b")
_NEGATIVE = re.compile(r"\b(?:not|never|without|cannot|no)\b", re.IGNORECASE)


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
    lines.append(json.dumps({"schema_version": _VERSION, "source_hash": source_hash, "chapter_hash": chapter_hash,
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
                    "compiler_version": _COMPILER_VERSION,
                    "origin": origin, "review_status": "review_required",
                    "record_count": len(records), "issues": issues,
                    "records_hash": hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (temporary / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        source_ids = sorted({str(record["source_id"]) for record in records})
        (temporary / "coverage.json").write_text(json.dumps({
            "source_block_count": len(_blocks(*_source(scenario_id))),
            "covered_source_ids": source_ids, "unresolved": issues,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        glossary = [{"record_id": record["id"], "name": record["name"],
                     "aliases": record["aliases"]} for record in records]
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


def _source_parts(record: dict[str, Any], source: str) -> list[str]:
    spans = record.get("source_spans")
    if not isinstance(spans, list) or not spans or len(spans) > 100:
        raise ValueError("每筆記錄須提供 source_spans")
    parts = []
    for span in spans:
        if (not isinstance(span, list) or len(span) != 2
                or any(type(x) is not int for x in span)
                or not 0 <= span[0] < span[1] <= len(source)):
            raise ValueError("來源範圍無效；使用原文 Unicode 字元的 [start,end)")
        parts.append(source[span[0]:span[1]])
    return parts


def _record_source(record: dict[str, Any], source: str) -> str:
    return "\n".join(_source_parts(record, source))


def _validate(scenario_id: str, records: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    manifest, text = _source(scenario_id)
    blocks = {b["id"]: b for b in _blocks(manifest, text)}
    if not isinstance(records, list) or not records or len(records) > 5000:
        raise ValueError("模板須有 1 至 5000 筆完整記錄")
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    issues = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("模板記錄格式錯誤")  # noqa: TRY004
        record_id, source_id = record.get("id"), record.get("source_id")
        if (not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", record_id)
                or record_id in seen or not isinstance(source_id, str) or source_id not in blocks):
            raise ValueError("模板 ID 重複、格式錯誤或來源不存在")
        seen.add(record_id)
        block = blocks[source_id]
        if (record.get("page") != block["page"] or record.get("chapter_id") != block["chapter_id"]
                or record.get("source_pages") != block["pages"]):
            raise ValueError("模板頁碼或章節與來源不符")
        parts = _source_parts(record, block["text"])
        original = "\n".join(parts)
        if record.get("visibility") not in ("public", "kp_only"):
            raise ValueError("模板可見範圍無效")
        for key in ("name", "type"):
            if not isinstance(record.get(key), str) or not record[key].strip() or len(record[key]) > 200:
                raise ValueError(f"模板缺少有效 {key}")
        for key in ("aliases", "keywords", "related_record_ids"):
            values = record.get(key)
            if not isinstance(values, list) or len(values) > 100 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in values):
                raise ValueError(f"模板 {key} 格式錯誤")
        for key in ("public_text", "kp_text", "rule_text", "uncertainty"):
            if not isinstance(record.get(key), str):
                raise ValueError(f"模板 {key} 必須為文字")  # noqa: TRY004
        if record["visibility"] == "kp_only" and record["public_text"].strip():
            raise ValueError("KP 專用記錄不可含公開內容")
        rules = record.get("rules")
        if not isinstance(rules, list) or len(rules) > 100:
            raise ValueError("模板規則格式錯誤")
        if record["rule_text"].strip() and not rules:
            raise ValueError("規則摘要必須附結構化規則；摘要不進入遊戲輸入")
        for rule in rules:
            if not isinstance(rule, dict) or not rule or set(rule) - set(_RULE_FIELDS):
                raise ValueError("規則欄位無效")
            for evidence in rule.values():
                if (not isinstance(evidence, dict) or not isinstance(evidence.get("text"), str)
                        or not evidence["text"].strip()):
                    raise ValueError("規則缺少中文文字")
                quote = evidence.get("source_quote")
                if not isinstance(quote, str) or not quote.strip() or not any(quote in part for part in parts):
                    raise ValueError("規則引述不在此記錄的來源範圍")
                if Counter(x.casefold() for x in _NUMBER.findall(quote)) != Counter(x.casefold() for x in _NUMBER.findall(evidence["text"])):
                    raise ValueError("規則欄位數值與原文不符")
        translated = scenario_projection.body(record, "public") + "\n" + scenario_projection.body(record, "kp_only")
        if not translated.strip():
            raise ValueError("模板記錄缺少中文內容")
        absent = {x.casefold() for x in _NUMBER.findall(original)} - {x.casefold() for x in _NUMBER.findall(translated)}
        if absent:
            issues.append(f"{record_id} 數值未對齊：{', '.join(sorted(absent)[:10])}")
        if _NEGATIVE.search(original) and not re.search(r"不|無|未|非|禁止|不能", translated):
            issues.append(f"{record_id} 否定條件待校對")
        if record["uncertainty"].strip():
            issues.append(f"{record_id} 有待釐清翻譯")
        grouped.setdefault(source_id, []).append(record)
    for record in records:
        if any(ref not in seen for ref in record["related_record_ids"]):
            raise ValueError("關聯記錄不存在")
    for source_id, block in blocks.items():
        intervals = sorted(span for record in grouped.get(source_id, []) for span in record["source_spans"])
        covered = 0
        for start, end in intervals:
            if start > covered:
                raise ValueError(f"{source_id} 來源覆蓋有缺口")
            covered = max(covered, end)
        if covered != len(block["text"]):
            raise ValueError(f"{source_id} 來源覆蓋不完整")
    scenario_projection.bundles(records)
    return manifest["content_hash"], _chapter_hash(manifest), issues


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
            "source_excerpt": block["text"], "source_spans": [[0, len(block["text"])]], "type": "source_unit",
            "name": block["heading"], "aliases": [], "keywords": [],
            "visibility": "kp_only", "public_text": "", "kp_text": "", "rule_text": "",
            "rules": [], "related_record_ids": [], "uncertainty": "尚未翻譯與校對",
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
            "Keep hashes, chapter and source page fields unchanged. Split complete semantic records with unique IDs and source_spans [start,end) covering the full source text; offsets are Unicode code points. Translate complete units; "
            "do not infer missing rules. Preserve triggers, success/failure, Push, costs, limits and exceptions. "
            "Put rule translations and exact original quotes in rules fields "
            "trigger/check/success/failure/exceptions. Mark public versus KP content explicitly. "
            "Keep unresolved text in uncertainty; clear it only after review. "
            "source_pages are PDF positions, not printed page labels. "
            "Use related_record_ids for dependencies; links do not unlock other chapters.\n\n"
        )
        stream.write(_records_text(records, manifest["content_hash"], _chapter_hash(manifest)))
    return Path(filename)


def status(scenario_id: str) -> dict[str, Any]:
    manifest, _ = _source(scenario_id)
    return {
            "variants": [{**v, "current": v.get("source_hash") == manifest["content_hash"]
                          and v.get("chapter_hash") == _chapter_hash(manifest)
                          and v.get("schema_version") == _VERSION
                          and v.get("compiler_version") == _COMPILER_VERSION}
                         for v in _all_variants(scenario_id)]}


def _read_variant(scenario_id: str, variant_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, _ = _source(scenario_id)
    path = _variant_dir(scenario_id, manifest["content_hash"], variant_id)
    variant = scenario_library._read_json(path / "manifest.json", None)
    records = scenario_library._read_json(path / "records.json", None)
    if not isinstance(variant, dict) or not isinstance(records, list):
        raise FileNotFoundError(variant_id)
    if variant.get("schema_version") != _VERSION or variant.get("compiler_version") != _COMPILER_VERSION:
        raise ValueError("模板結構版本已過期，請重新產生或匯入校對")
    if variant.get("records_hash") != hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest():
        raise ValueError("模板內容已變更，請重新匯入校對")
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
    if not isinstance(payload, dict) or payload.get("schema_version") != _VERSION:
        raise ValueError("模板版本已更新，請重新匯出 schema v3")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("模板缺少 records")  # noqa: TRY004 - user input validation
    source_hash, chapter_hash, issues = _validate(scenario_id, records)
    if payload.get("source_hash") != source_hash or payload.get("chapter_hash") != chapter_hash:
        raise ValueError("匯入模板的來源或章節版本已過期")
    source_blocks = {b["id"]: b for b in _blocks(*_source(scenario_id))}
    for record in records:
        record["source_excerpt"] = _record_source(record, source_blocks[record["source_id"]]["text"])
    return _save_variant(scenario_id, source_hash, chapter_hash, records, issues, origin="manual")


def _preview_content(scenario_id: str, variant_id: str) -> str:
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
    return "\n".join(lines)


def preview_page_count(scenario_id: str, variant_id: str, limit: int = 1700) -> int:
    return max(1, (len(_preview_content(scenario_id, variant_id)) + limit - 1) // limit)


def preview(scenario_id: str, variant_id: str, limit: int = 1700, page: int = 1) -> str:
    content = _preview_content(scenario_id, variant_id)
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


# Small bounded hot-path cache: avoid reading/serializing the full bilingual audit
# file on every turn. Source, record and approval file changes invalidate it.
_selection_cache: dict[tuple, tuple[tuple, scenario_rag.ScenarioIndex]] = {}


def _selection_stamp(scenario_id: str, variant_id: str) -> tuple:
    root = scenario_library._path(scenario_id)
    manifest = scenario_library._read_json(root / "manifest.json", {})
    if not isinstance(manifest, dict):
        raise ValueError("劇本 manifest 格式錯誤")  # noqa: TRY004 - invalid persisted document
    variant = _variant_dir(scenario_id, manifest.get("content_hash", ""), variant_id)
    paths = [root / "manifest.json", root / "scenario.txt", variant / "manifest.json", variant / "records.json"]
    return tuple((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                 for path in paths for stat in [path.stat()])


def index_for_state(state: Any, metrics: dict[str, Any] | None = None) -> scenario_rag.ScenarioIndex:
    scenario_id = state.scenario_library_id
    variant_id = state.scenario_variant_id
    diagnostics = metrics if metrics is not None else {}
    diagnostics.update(requested_variant=variant_id, effective_variant="original",
                       projection_version=scenario_projection.VERSION, variant_fallback="not_selected")
    if scenario_id and variant_id and variant_id != "original":
        try:
            key = (scenario_id, variant_id, tuple(state.context_chapter_ids), SCENARIO_RAG_EMBEDDING_MODEL, scenario_projection.VERSION)
            stamp = _selection_stamp(scenario_id, variant_id)
            cached = _selection_cache.get(key)
            if cached is not None and cached[0] == stamp:
                diagnostics.update(effective_variant=variant_id, variant_fallback="none", template_cache="memory")
                cached[1].index_cache = "memory"
                return cached[1]
            diagnostics["template_cache"] = "miss"
            variant, records = _read_variant(scenario_id, variant_id)
            diagnostics["variant_fallback"] = "unapproved"
            if variant.get("review_status") == "approved":
                diagnostics["variant_fallback"] = "empty_window"
                window = tuple(state.context_chapter_ids)
                selected = [r for r in records if r.get("chapter_id") in window]
                if selected:
                    digest = hashlib.sha256(json.dumps(
                        [scenario_id, variant["source_hash"], variant["chapter_hash"],
                         variant_id, window, SCENARIO_RAG_EMBEDDING_MODEL, scenario_projection.VERSION],
                        ensure_ascii=False).encode("utf-8")).hexdigest()
                    index = scenario_rag.get_record_index(f"template:{scenario_id}:{digest}", selected)
                    if _selection_stamp(scenario_id, variant_id) != stamp:
                        raise ValueError("模板來源在建立索引時改變")
                    if len(_selection_cache) >= 32:
                        _selection_cache.pop(next(iter(_selection_cache)))
                    _selection_cache[key] = (stamp, index)
                    diagnostics.update(effective_variant=variant_id, variant_fallback="none")
                    return index
        except (FileNotFoundError, ValueError):
            diagnostics["variant_fallback"] = "invalid_or_stale"
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
    for selection_key in list(_selection_cache):
        if selection_key[0] == scenario_id:
            _selection_cache.pop(selection_key, None)
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


def search_for_state(state: Any, query: str, top_k: int = 5,
                     metrics: dict[str, Any] | None = None) -> tuple[scenario_rag.ScenarioIndex, list[dict]]:
    """Internal Keeper search: Chinese first, then one current-window source search."""
    diagnostics = metrics if metrics is not None else {}
    index = index_for_state(state, diagnostics)
    results = scenario_rag.search(index, query, top_k=top_k, metrics=diagnostics)
    diagnostics['query_fallback'] = 'none'
    if (results or not query.strip()
            or diagnostics.get('effective_variant', 'original') == 'original'):
        return index, results
    diagnostics['chinese_result_count'] = 0
    diagnostics['chinese_query_embedding_status'] = diagnostics.get('query_embedding_status', 'unknown')
    # scenario_text is the same authorized chapter window used by original mode;
    # never load the entire library PDF or expand access because a query missed.
    original_index = scenario_rag.get_index(state.group_id, state.scenario_text)
    results = scenario_rag.search(original_index, query, top_k=top_k, metrics=diagnostics)
    diagnostics.update(query_fallback='chinese_no_match', effective_variant='original')
    return original_index, [dict(row, retrieval_source='original_fallback') for row in results]
