"""Versioned Traditional Chinese scenario records for scenario RAG."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import (
    db,
    scenario_authoring,
    scenario_library,
    scenario_numbers,
    scenario_projection,
    scenario_rag,
)
from app.config import IMPORT_DIR, SCENARIO_RAG_EMBEDDING_MODEL

_LOCALE = "zh-TW"
_VERSION = 3
_COMPILER_VERSION = scenario_projection.VERSION
_V4_COMPILER = "zh-gameplay-v4"
BUDGET_VERSION = "token-v1"
_RULE_FIELDS = ("trigger", "check", "success", "failure", "exceptions")
_SAFE_VARIANT = re.compile(r"zh-TW-[a-f0-9]{12}")
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
            # Reviewed sources have verified physical pages. Keep those boundaries
            # so newly exported units do not assign an entire chapter's page range
            # to a single card/paragraph. Existing exports remain unchanged.
            if manifest.get("source_review"):
                flush(lines, source_pages, heading, chapter["id"])
                heading = chapter.get("title", chapter["id"])
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


def _records_text(records: list[dict[str, Any]], source_hash: str, chapter_hash: str, *, version: int = 3) -> str:
    lines = ["# 中文劇本模板", "", "請編輯下方 JSON 區塊後用 /coc scenario template import 匯入。", "", "```json"]
    lines.append(json.dumps({"schema_version": version, "source_hash": source_hash, "chapter_hash": chapter_hash,
                             "records": records}, ensure_ascii=False, indent=2))
    lines.extend(["```", ""])
    return "\n".join(lines)


def _save_variant(scenario_id: str, source_hash: str, chapter_hash: str,
                  records: list[dict[str, Any]], issues: list[str], *, origin: str, version: int = 3, variant_id: str | None = None) -> str:
    variant_id = variant_id or f"zh-TW-{uuid4().hex[:12]}"
    target = _variant_dir(scenario_id, source_hash, variant_id)
    if target.exists():
        previous = scenario_library._read_json(target / "manifest.json", {})
        saved = scenario_library._read_json(target / "records.json", None)
        if (saved == records and previous.get("records_hash") == scenario_authoring.digest(records)
                and previous.get("source_hash") == source_hash and previous.get("chapter_hash") == chapter_hash
                and previous.get("schema_version") == version):
            return variant_id
        raise ValueError("候選版本識別衝突或已被修改，請重新匯出校閱")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".building-", dir=target.parent))
    try:
        manifest = {"scenario_id": scenario_id, "variant_id": variant_id,
                    "source_hash": source_hash, "chapter_hash": chapter_hash,
                    "locale": _LOCALE, "schema_version": version,
                    "compiler_version": _V4_COMPILER if version == 4 else _COMPILER_VERSION,
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
            _records_text(records, source_hash, chapter_hash, version=version), encoding="utf-8")
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


def _percentage_format_only(expected: dict[str, int], actual: dict[str, int]) -> bool:
    # Preserve an unresolved draft when ONLY percent notation differs, so legacy
    # records in separate packages can be corrected incrementally. Approval still
    # rejects this discrepancy; no value or occurrence difference is deferred.
    def without_percent(values: dict[str, int]) -> dict[str, int]:
        result: dict[str, int] = {}
        for token, count in values.items():
            key = token.removesuffix('%')
            result[key] = result.get(key, 0) + count
        return result
    return without_percent(expected) == without_percent(actual)


def _rule_numeric_issue(record_id: str, index: int, field: str, value: dict,
                        expected: Any, actual: Any) -> dict:
    item = scenario_authoring.issue('RULE_NUMERIC_MISMATCH', record_id, f'rules[{index}].{field}', expected, actual)
    item.update(expected_counts=dict(expected), actual_counts=dict(actual),
                missing_counts=dict(expected-actual), extra_counts=dict(actual-expected),
                source_quote=value['source_quote'], translated_text=value['text'],
                message=f'{record_id} rules[{index}].{field} 數值或百分號表示待校對')
    return item


def _review_issues(record: dict, original: str) -> list[dict]:
    translated = scenario_projection.body(record, "public") + "\n" + scenario_projection.body(record, "kp_only")
    rid = record['id']
    issues = []
    for index, rule in enumerate(record['rules']):
        for field, value in rule.items():
            expected = scenario_numbers.counts(value['source_quote'])
            actual = scenario_numbers.counts(value['text'])
            if expected != actual and _percentage_format_only(expected, actual):
                issues.append(_rule_numeric_issue(rid, index, field, value, expected, actual))
    absent = scenario_numbers.missing(original, translated)
    if absent:
        item = scenario_authoring.issue('SOURCE_NUMERIC_COVERAGE', rid, 'translation',
                                        'all meaningful source numbers in this record', absent)
        item.update(message=f"{rid} 數值未對齊：{', '.join(absent)}", missing_tokens=absent,
                    source_contexts=scenario_numbers.contexts(original, absent),
                    source_id=record['source_id'], source_spans=record['source_spans'],
                    source_pages=record['source_pages'],
                    suggestion='Translate meaningful passages; inspect PDF for layout/OCR artifacts. '
                               'Never pad gameplay or self-authorize exclusions. Source repair requires a new export.')
        issues.append(item)
    if _NEGATIVE.search(original) and not re.search(r"不|無|未|非|禁止|不能", translated):
        item = scenario_authoring.issue('NEGATION_REVIEW', rid, 'translation', 'preserve negative conditions', '')
        item['message'] = f'{rid} 否定條件待校對'
        issues.append(item)
    if record['uncertainty'].strip():
        item = scenario_authoring.issue('TRANSLATION_UNCERTAINTY', rid, 'uncertainty', 'resolved source review', record['uncertainty'])
        item['message'] = f'{rid} 有待釐清翻譯'
        issues.append(item)
    return issues


def _write_diagnostics(exc: scenario_authoring.Diagnostics, **context: Any) -> None:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = IMPORT_DIR / ("template-report-" + uuid4().hex + ".md")
    fd = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("# KP-only template diagnostics / 私密模板校對報告\n\n")
        stream.write("Source repair worksheet / 來源修復工作表：record source hash, source ID/span, "
                     "exact extracted quote, physical PDF page/crop, proposed correction, reason, reviewer and date. "
                     "保留原文與來源 hash；記錄 PDF 實體頁／裁圖、修正前後、理由、校對者與日期。 "
                     "This report grants no numeric waiver. 不可把雜訊填進劇情，也不能自行豁免檢查。\n\n")
        stream.write(json.dumps({**context, "total": exc.total, "omitted": exc.total-len(exc.full_issues),
                                 "issues": exc.full_issues}, ensure_ascii=False, indent=2))
    exc.report_path = report


def _validate(scenario_id: str, records: list[dict[str, Any]], *, version: int = 3, partial: bool = False,
              source_data: tuple[dict, dict] | None = None) -> tuple[str, str, list[str]]:
    if source_data is None:
        manifest, text = _source(scenario_id)
        blocks = {b["id"]: b for b in _blocks(manifest, text)}
    else:
        manifest, blocks = source_data
    if not isinstance(records, list) or not records or len(records) > (scenario_authoring.MAX_RECORDS if version == 4 else 5000):
        raise ValueError(f"模板須有 1 至 {scenario_authoring.MAX_RECORDS if version == 4 else 5000} 筆完整記錄")
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    issues: list[str] = []
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
                expected = scenario_numbers.counts(quote)
                actual = scenario_numbers.counts(evidence["text"])
                if expected != actual and not _percentage_format_only(expected, actual):
                    raise ValueError("規則欄位數值與原文不符")
        translated = scenario_projection.body(record, "public") + "\n" + scenario_projection.body(record, "kp_only")
        if not translated.strip():
            raise ValueError("模板記錄缺少中文內容")
        issues.extend(item['message'] for item in _review_issues(record, original))
        grouped.setdefault(source_id, []).append(record)
    for record in ([] if partial else records):
        if any(ref not in seen for ref in record["related_record_ids"]):
            raise ValueError("關聯記錄不存在")
    for source_id, block in ([] if partial else blocks.items()):
        intervals = sorted(span for record in grouped.get(source_id, []) for span in record["source_spans"])
        covered = 0
        for start, end in intervals:
            if start > covered:
                raise ValueError(f"{source_id} 來源覆蓋有缺口")
            covered = max(covered, end)
        if covered != len(block["text"]):
            raise ValueError(f"{source_id} 來源覆蓋不完整")
    if version == 3 and not partial:
        scenario_projection.bundles(records)
    return manifest["content_hash"], _chapter_hash(manifest), issues


def export_legacy_template(scenario_id: str) -> Path:
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


def export_template(scenario_id: str) -> Path:
    manifest, text = _source(scenario_id)
    return scenario_authoring.export(_root() / scenario_id / "exports", IMPORT_DIR.resolve(),
                                     manifest["content_hash"], _chapter_hash(manifest), _blocks(manifest, text),
                                     title=manifest.get("title") or scenario_id)


def export_message(scenario_id: str, exported: Path) -> str:
    payload = scenario_authoring.parse_markdown(exported.read_text(encoding="utf-8"))
    directory = _root() / scenario_id / "exports" / payload["export_id"]
    files = scenario_authoring.read_json(directory / "files.json")
    registry = scenario_authoring.read_json(directory / "registry.json")
    prefix = registry.get('filename_prefix', 'scenario')
    return ("已匯出外部中文整備工作檔（含 KP 原文，請勿公開）。\n"
            + "\n".join(str(IMPORT_DIR / f) for f in files)
            + "\n將 MD 上傳至網頁版 Gemini／ChatGPT，並貼上以下提示詞：\n```text\n"
            + scenario_authoring.PROMPT + "\n```\n"
            + f"共 {len(files)} 個來源檔、{len(registry['batches'])} 個邏輯批次、{len(registry['units'])} 個單元；進度 0/{len(registry['units'])}。"
            + f"成果檔名：{prefix}_01.md、{prefix}_02.md，後續依序累加；所有來源檔共用成果流水號。\n"
            + f"下載成果請放入 {IMPORT_DIR / payload['export_id'] / 'results'}，再透過 Help 選檔匯入。"
            + "可分次提交完整單元；來源最多三檔，成果檔數不限。所有單元完整且校閱核准後才能啟用。"
            + "更正已存記錄時，在該批 JSON 加入 replace_record_ids 並附完整替換筆。")


def import_path(filename: str) -> Path:
    """Only flat legacy uploads or export-ID/results/name.md, never source workbooks."""
    parts = filename.split('/')
    valid = (len(parts) == 1 or (len(parts) == 3
             and re.fullmatch(r'export-[a-f0-9]{32}', parts[0]) and parts[1] == 'results'))
    if (not valid or any(p in ('', '.', '..') for p in parts) or '\\' in filename
            or any(c in filename for c in '\r\n\t') or not parts[-1].lower().endswith('.md')):
        raise ValueError('檔名必須是 imports 下的 .md 或 export-ID/results/檔名.md')
    root = IMPORT_DIR.resolve()
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('匯入路徑不可包含符號連結')
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise FileNotFoundError('找不到匯入檔案')
    if path.stat().st_size > scenario_authoring.MAX_FILE_BYTES:
        raise ValueError('RESOURCE_LIMIT：匯入檔超過 20 MB')
    return path


def import_candidates() -> list[tuple[str, dict]]:
    if not IMPORT_DIR.is_dir():
        return []
    paths = list(IMPORT_DIR.iterdir())
    for directory in IMPORT_DIR.glob('export-*'):
        results = directory / 'results'
        if not directory.is_symlink() and not results.is_symlink() and results.is_dir():
            paths.extend(results.iterdir())
    candidates = []
    for path in sorted(paths):
        if path.suffix.lower() != '.md':
            continue
        filename = path.relative_to(IMPORT_DIR).as_posix()
        try:
            payload = scenario_authoring.parse_markdown(import_path(filename).read_text(encoding='utf-8'))
            if '/' in filename and payload.get('export_id') != filename.split('/')[0]:
                continue
            if payload.get('authoring_version') in (1, 2) or payload.get('schema_version') == 3:
                candidates.append((filename, payload))
        except (OSError, ValueError):
            continue
    return candidates


def import_matches(scenario_id: str, payload: dict, *, manifest: dict | None = None) -> bool:
    if manifest is None:
        manifest, _ = _source(scenario_id)
    if 'authoring_version' in payload:
        try:
            _, registry = scenario_authoring.registry_for(_root() / scenario_id / 'exports', payload,
                                                          manifest['content_hash'], _chapter_hash(manifest))
            return type(payload['authoring_version']) is int and payload['authoring_version'] == registry.get('authoring_version', 1)
        except (OSError, ValueError):
            return False
    return (payload.get('source_hash') == manifest['content_hash']
            and payload.get('chapter_hash') == _chapter_hash(manifest))


def import_progress(scenario_id: str, filename: str) -> str:
    payload = scenario_authoring.parse_markdown(import_path(filename).read_text(encoding='utf-8'))
    if 'authoring_version' not in payload:
        return ''
    manifest, _ = _source(scenario_id)
    return scenario_authoring.progress(_root() / scenario_id / 'exports', payload,
                                       manifest['content_hash'], _chapter_hash(manifest), IMPORT_DIR)


def _diagnose_records(scenario_id: str, records: list, *, version: int) -> list[str]:
    errors: list[dict[str, Any]] = []
    review_issues: list[dict[str, Any]] = []
    manifest, source = _source(scenario_id)
    blocks = {b['id']: b for b in _blocks(manifest, source)}
    for record in records:
        previous = len(errors)
        if isinstance(record, dict):
            rid = record.get('id', '')
            block = blocks.get(record.get('source_id')) if isinstance(record.get('source_id'), str) else None
            if block:
                for field, expected in [('page', block['page']), ('source_pages', block['pages']), ('chapter_id', block['chapter_id'])]:
                    if record.get(field) != expected:
                        errors.append(scenario_authoring.issue('SOURCE_METADATA_MISMATCH', rid, field, expected, record.get(field)))
            for field in ('type', 'name', 'source_spans'):
                if not record.get(field):
                    errors.append(scenario_authoring.issue('MISSING_FIELD', rid, field, 'required field', record.get(field)))
            if record.get('visibility') not in ('public', 'kp_only'):
                errors.append(scenario_authoring.issue('INVALID_VISIBILITY', rid, 'visibility', 'public / kp_only', record.get('visibility')))
            if record.get('visibility') == 'kp_only' and record.get('public_text'):
                errors.append(scenario_authoring.issue('PRIVATE_PUBLIC_CONFLICT', rid, 'public_text', 'empty', 'nonempty'))
            if isinstance(record.get('rules'), list):
                for i, rule in enumerate(record['rules']):
                    if isinstance(rule, dict):
                        for field, value in rule.items():
                            if not isinstance(value, dict) or not value.get('source_quote'):
                                errors.append(scenario_authoring.issue('RULE_EVIDENCE_MISSING', rid, f'rules[{i}].{field}', 'text + source_quote', value))
                            elif isinstance(value.get('text'), str) and isinstance(value['source_quote'], str):
                                expected = scenario_numbers.counts(value['source_quote'])
                                actual = scenario_numbers.counts(value['text'])
                                if expected != actual and not _percentage_format_only(expected, actual):
                                    errors.append(_rule_numeric_issue(rid, i, field, value, expected, actual))
        # Numeric field errors must not hide other records' coverage/uncertainty.
        if isinstance(record, dict) and block:
            try:
                review_issues.extend(_review_issues(record, _record_source(record, block['text'])))
            except (ValueError, KeyError, TypeError, AttributeError):
                pass  # Structural validation below reports malformed records.
        if previous != len(errors):
            continue
        try:
            _validate(scenario_id, [record], version=version, partial=True, source_data=(manifest, blocks))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            rid = record.get('id', '') if isinstance(record, dict) else ''
            errors.append(scenario_authoring.issue('INVALID_RECORD', rid, 'record', 'valid source-bound record', str(exc)))
    if errors:
        raise scenario_authoring.Diagnostics(errors + review_issues)
    try:
        return _validate(scenario_id, records, version=version, source_data=(manifest, blocks))[2]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise scenario_authoring.Diagnostics([scenario_authoring.issue('COVERAGE_OR_DEPENDENCY', '', 'records', 'complete source coverage and valid dependencies', str(exc))]) from exc


def status(scenario_id: str) -> dict[str, Any]:
    manifest, _ = _source(scenario_id)
    return {
            "variants": [{**v, "current": v.get("source_hash") == manifest["content_hash"]
                          and v.get("chapter_hash") == _chapter_hash(manifest)
                          and (v.get("schema_version"), v.get("compiler_version")) in {(3, _COMPILER_VERSION), (4, _V4_COMPILER)}}
                         for v in _all_variants(scenario_id)]}


def _read_variant(scenario_id: str, variant_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, _ = _source(scenario_id)
    path = _variant_dir(scenario_id, manifest["content_hash"], variant_id)
    variant = scenario_library._read_json(path / "manifest.json", None)
    records = scenario_library._read_json(path / "records.json", None)
    if not isinstance(variant, dict) or not isinstance(records, list):
        raise FileNotFoundError(variant_id)
    if (variant.get("schema_version"), variant.get("compiler_version")) not in {(3, _COMPILER_VERSION), (4, _V4_COMPILER)}:
        raise ValueError("模板結構版本已過期，請重新產生或匯入校對")
    if variant.get("records_hash") != hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest():
        raise ValueError("模板內容已變更，請重新匯入校對")
    if variant.get("chapter_hash") != _chapter_hash(manifest):
        raise ValueError("中文模板章節版本已過期")
    return variant, records


def approve(scenario_id: str, variant_id: str, *, reviewer_id: str) -> None:
    variant, records = _read_variant(scenario_id, variant_id)
    try:
        warnings = _diagnose_records(scenario_id, records, version=variant["schema_version"])
        if warnings:
            manifest, text = _source(scenario_id)
            blocks = {b['id']: b for b in _blocks(manifest, text)}
            issues = [item for record in records
                      for item in _review_issues(record, _record_source(record, blocks[record['source_id']]['text']))]
            raise scenario_authoring.Diagnostics(issues)
    except scenario_authoring.Diagnostics as exc:
        _write_diagnostics(exc, scenario_id=scenario_id, variant_id=variant_id,
                           source_hash=variant['source_hash'], chapter_hash=variant['chapter_hash'])
        raise
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
    path = import_path(filename)
    try:
        payload = scenario_authoring.parse_markdown(path.read_text(encoding="utf-8"))
        if '/' in filename and payload.get('export_id') != filename.split('/')[0]:
            raise ValueError('成果目錄與 export_id 不符')
        manifest, text = _source(scenario_id)
        source_hash, chapter_hash = manifest["content_hash"], _chapter_hash(manifest)
        if "authoring_version" in payload:
            def validate(records):
                return _diagnose_records(scenario_id, records, version=4)
            def save(records, issues):
                blocks = {b['id']: b for b in _blocks(manifest, text)}
                for record in records:
                    record['source_excerpt'] = _record_source(record, blocks[record['source_id']]['text'])
                return _save_variant(scenario_id, source_hash, chapter_hash, records, issues,
                                     origin="external-authoring", version=4,
                                     variant_id="zh-TW-" + scenario_authoring.digest([payload["export_id"], records])[:12])
            return scenario_authoring.import_batch(_root() / scenario_id / "exports", payload,
                                                   source_hash, chapter_hash, validate, save)
        if payload.get("schema_version") != 3:
            raise ValueError("模板版本不支援；請重新匯出整備工作檔")
        records = payload.get("records")
        if not isinstance(records, list) or not records or len(records) > 5000:
            raise ValueError("模板須有 1 至 5000 筆完整記錄")
        if payload.get("source_hash") != source_hash or payload.get("chapter_hash") != chapter_hash:
            raise ValueError("匯入模板的來源或章節版本已過期")
        issues = _diagnose_records(scenario_id, records, version=3)
        source_blocks = {b["id"]: b for b in _blocks(manifest, text)}
        for record in records:
            record["source_excerpt"] = _record_source(record, source_blocks[record["source_id"]]["text"])
        return _save_variant(scenario_id, source_hash, chapter_hash, records, issues, origin="manual")
    except scenario_authoring.Diagnostics as exc:
        _write_diagnostics(exc, scenario_id=scenario_id, filename=filename)
        raise


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
            key = (scenario_id, variant_id, tuple(state.context_chapter_ids), SCENARIO_RAG_EMBEDDING_MODEL, scenario_projection.VERSION, _V4_COMPILER)
            stamp = _selection_stamp(scenario_id, variant_id)
            cached = _selection_cache.get(key)
            if cached is not None and cached[0] == stamp:
                diagnostics.update(effective_variant=variant_id, variant_fallback="none", template_cache="memory")
                diagnostics["projection_version"] = _V4_COMPILER if cached[1].record_store is not None else scenario_projection.VERSION
                cached[1].index_cache = "memory"
                return cached[1]
            diagnostics["template_cache"] = "miss"
            variant, records = _read_variant(scenario_id, variant_id)
            diagnostics["projection_version"] = variant["compiler_version"]
            diagnostics["variant_fallback"] = "unapproved"
            if variant.get("review_status") == "approved":
                diagnostics["variant_fallback"] = "empty_window"
                window = tuple(state.context_chapter_ids)
                selected = [r for r in records if r.get("chapter_id") in window]
                if selected:
                    digest = hashlib.sha256(json.dumps(
                        [scenario_id, variant["source_hash"], variant["chapter_hash"],
                         variant_id, window, SCENARIO_RAG_EMBEDDING_MODEL, scenario_projection.VERSION, _V4_COMPILER],
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
                     metrics: dict[str, Any] | None = None, *,
                     source: str = "auto", continuation: str = "", principal: str = "") -> tuple[scenario_rag.ScenarioIndex, list[dict]]:
    """Search authorized evidence; retrieval success never certifies completeness."""
    if source not in {"auto", "original"}:
        raise ValueError("source must be auto or original")
    diagnostics = metrics if metrics is not None else {}
    diagnostics['query_fallback'] = 'none'
    if source == "original" and continuation:
        raise ValueError("原文補查請不要帶中文續取識別")
    if source == "original":
        index = scenario_rag.get_index(state.group_id, state.scenario_text)
        results = scenario_rag.search(index, query, top_k=top_k, metrics=diagnostics)
        diagnostics.update(query_fallback='explicit_original', effective_variant='original')
        return index, [dict(row, retrieval_source='original_explicit') for row in results]
    index = index_for_state(state, diagnostics)
    from app import scenario_retrieval
    binding = [state.group_id, getattr(state, "timeline_id", ""), principal, state.scenario_library_id,
               state.scenario_variant_id, list(state.context_chapter_ids), index.text_hash, query,
               BUDGET_VERSION, top_k, hashlib.sha256(json.dumps(getattr(state, "log", []), ensure_ascii=False, default=str).encode()).hexdigest()] if getattr(index, "record_store", None) is not None else []
    if continuation:
        offset = scenario_retrieval.continuation_offset(continuation, binding)
        if getattr(index, "record_store", None) is None:
            raise ValueError("續取版本已失效")
        roots = scenario_retrieval.continuation_roots(continuation, binding)
        assert index.record_store is not None
        results = scenario_retrieval.project(index.record_store, roots, query, offset=offset)
    else:
        offset = 0
        results = scenario_rag.search(index, query, top_k=top_k, metrics=diagnostics)
    if getattr(index, "record_store", None) is not None and results:
        scenario_retrieval.bind_continuation(results, binding, offset)
        # Known required evidence cannot be certified by unrelated original hits.
        # Return explicit incompleteness; Executor can request source=original.
        return index, results
    incomplete = any(row.get('budget_omitted') or '【依據尚未完整】' in row.get('text', '')
                     for row in results)
    if ((results and not incomplete) or not query.strip()
            or diagnostics.get('effective_variant', 'original') == 'original'):
        return index, results
    chinese_variant = diagnostics.get('effective_variant', 'original')
    diagnostics['chinese_result_count'] = len(results)
    diagnostics['chinese_query_embedding_status'] = diagnostics.get('query_embedding_status', 'unknown')
    # Never expand the authorized chapter window to fill a missing dependency.
    original_index = scenario_rag.get_index(state.group_id, state.scenario_text)
    originals = scenario_rag.search(original_index, query, top_k=top_k, metrics=diagnostics)
    diagnostics.update(query_fallback='chinese_incomplete' if incomplete else 'chinese_no_match',
                       original_result_count=len(originals),
                       effective_variant='mixed' if results and originals else 'original')
    if results and not originals:
        diagnostics['effective_variant'] = chinese_variant
        diagnostics['query_embedding_status'] = diagnostics['chinese_query_embedding_status']
        return index, [dict(row, original_supplement_missing=True) for row in results]
    return original_index, [*results, *[dict(row, retrieval_source='original_fallback') for row in originals]]
