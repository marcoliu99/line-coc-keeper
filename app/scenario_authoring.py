"""External authoring workbooks: trusted provenance, resumable batches and diagnostics.

No translation requests are made here. Only the server's immutable export registry
can supply runtime source metadata; uploaded metadata is never authoritative.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

PROMPT = '請依附件內的整備指引完成繁體中文翻譯，回傳可匯入的 Markdown 檔；若需分批，請列出尚未完成的部分。'
VERSION = 1
SEGMENTATION = 'paragraph-v1'
MAX_FILE_BYTES = 20_000_000
MAX_RECORDS = 20000
MAX_UNIT_CHARS = 4000
BATCH_CHARS = 8000
MAX_ISSUES = 100
_lock = threading.RLock()

INSTRUCTIONS = '''# External Chinese authoring / 外部中文整備

Private Keeper material. Do not publish. / 含 KP 原文，請勿公開。

Upload this MD to web Gemini/ChatGPT and paste / 上傳本檔後貼上：

> {prompt}

Translate complete source units, never summarize or invent. Preserve all mechanics,
values, costs, limits, exceptions and consequences. Fill the authoring JSON below.
Keep export_id, batch_id and unit IDs. Do not calculate hashes, pages or offsets.
Only public or kp_only visibility is valid; kp_only requires empty public_text.
Rules use trigger/check/success/failure/exceptions; each populated field is
{{"text": "中文", "evidence": [{{"unit_id": "supplied ID", "source_quote": "exact original"}}]}}.
Exact quotes must occur once within their stated source unit; do not guess ambiguous
matches. Never shorten translations to fit a gameplay budget. Complete every unit,
or retain uncertainty and list unfinished units. Previous/next context is not a
completed translation. Link necessary conditions, limits and consequences through
dependencies; unknown conditions remain required until reviewed.

完整翻譯，不摘要、不補寫其他版本內容。保留數值、費用、限制、例外及後果。
只填下方 JSON，保留匯出／批次／單元 ID；不用計算頁碼、hash 或位置。
規則每個欄位須有 text 與 evidence（unit_id、精確 source_quote）。
visibility 只可 public 或 kp_only；後者 public_text 留空。
不要為遊戲輸入長度刪減翻譯。未解決事項留在 uncertainty，不得假裝完成。
每筆 unit_ids 須完整翻譯其所列來源；不完整時不要提交該筆。
先回報單元清單與分批計畫。回傳一個 authoring JSON 區塊的 Markdown 檔。
完成後下載至伺服器 imports，再用 Help 選檔匯入；網頁 AI 不能直接操作 bot。

Dependencies / dependencies 欄位：
[{{"record_id":"target ID", "kind":"required_for_adjudication", "condition":"", "source_quote":"exact source"}}]
kind: required_for_adjudication / conditional / background.
Do not downgrade mechanical dependencies to background. Conditional dependencies
are conservatively included; background still needs source evidence and review.
A missing record in another batch is allowed only while the aggregate is a draft.

Synthetic example only (not real source IDs; do not copy into your records):
Source: "Armor 2." / 原文範例："Armor 2."
'''


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.writing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        Path(temp).replace(path)
    finally:
        Path(temp).unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('RESOURCE_LIMIT：檔案超過 20 MB')
    return json.loads(path.read_text(encoding='utf-8'))


class Diagnostics(ValueError):
    def __init__(self, issues: list[dict], total: int | None = None):
        self.issues = issues[:MAX_ISSUES]
        self.total = total or len(issues)
        self.report_path: Path | None = None
        super().__init__(f'校對發現 {self.total} 個問題；詳細報告限 KP 查看。')


def issue(code: str, record: Any, field: str, expected: Any, actual: Any) -> dict:
    return {'code': code, 'severity': 'error', 'record_id': str(record)[:100],
            'field': field, 'expected': str(expected)[:200], 'actual': str(actual)[:200],
            'suggestion': '依 expected 修正；來源欄位請使用原匯出檔，不要猜值。'}


def parse_markdown(content: str) -> dict:
    matches = re.findall(r'^```json[ \t]*\r?\n(.*?)^```[ \t]*$', content, re.DOTALL | re.MULTILINE)
    if len(matches) != 1:
        raise Diagnostics([issue('JSON_BLOCK_COUNT', '', 'document', 'exactly one JSON block', len(matches))])
    try:
        payload = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise Diagnostics([issue('INVALID_JSON', '', 'document', 'valid JSON', f'line {exc.lineno}, column {exc.colno}')]) from exc
    if not isinstance(payload, dict):
        raise Diagnostics([issue('INVALID_JSON', '', 'document', 'object', type(payload).__name__)])
    return payload


def unit_ranges(text: str) -> list[tuple[int, int]]:
    """Exact contiguous coverage, preserving paragraphs where possible."""
    result = []
    start = 0
    while start < len(text):
        end = min(start + MAX_UNIT_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind('\n\n', start + MAX_UNIT_CHARS // 2, end)
            if boundary < 0:
                boundary = text.rfind('\n', start + MAX_UNIT_CHARS // 2, end)
            if boundary >= 0:
                end = boundary + (2 if text[boundary:boundary+2] == '\n\n' else 1)
        result.append((start, end))
        start = end
    return result


def blank_record(unit_id: str, index: int) -> dict:
    return {'id': f'r{index}', 'unit_ids': [unit_id], 'type': 'source_unit',
            'name': unit_id, 'aliases': [], 'keywords': [], 'visibility': 'kp_only',
            'public_text': '', 'kp_text': '', 'rules': [], 'related_record_ids': [],
            'dependencies': [], 'uncertainty': '尚未翻譯與校對'}


def export(root: Path, import_dir: Path, source_hash: str, chapter_hash: str,
           blocks: list[dict]) -> Path:
    if root.exists():
        exports = list(root.glob('export-*'))
        storage = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
        if len(exports) >= 100 or storage > 200_000_000:
            raise ValueError('RESOURCE_LIMIT：匯出草稿已達 100 組或 200 MB，請先封存不再使用的工作檔')
    export_id = 'export-' + uuid4().hex
    units: list[dict[str, Any]] = []
    for block in blocks:
        for start, end in unit_ranges(block['text']):
            text = block['text'][start:end]
            units.append({'id': f'u{len(units)+1}', 'source_id': block['id'],
                          'span': [start, end], 'page': block['page'], 'source_pages': block['pages'],
                          'chapter_id': block['chapter_id'], 'text': text, 'hash': digest(text)})
    if len(units) > MAX_RECORDS or sum(len(u['text'].encode()) for u in units) > MAX_FILE_BYTES // 2:
        raise ValueError('RESOURCE_LIMIT：請先將來源分成可管理的章節匯出')
    batches: list[list[dict]] = []
    for unit in units:
        if not batches or sum(len(u['text']) for u in batches[-1]) + len(unit['text']) > BATCH_CHARS:
            batches.append([])
        batches[-1].append(unit)
    registry = {'export_id': export_id, 'source_hash': source_hash, 'chapter_hash': chapter_hash,
                'authoring_version': VERSION, 'segmentation': SEGMENTATION, 'units': units,
                'batches': {f'b{i+1}': [u['id'] for u in batch] for i, batch in enumerate(batches)}}
    directory = root / export_id
    atomic_json(directory / 'registry.json', registry)
    atomic_json(directory / 'registry.sha256.json', digest(registry))
    import_dir.mkdir(parents=True, exist_ok=True)
    filenames = []
    example = blank_record('example-unit', 0)
    example.update(name='護甲', kp_text='護甲 2。', uncertainty='', rules=[{'check': {
        'text': '護甲 2', 'evidence': [{'unit_id': 'example-unit', 'source_quote': 'Armor 2'}]}}])
    unit_positions = {unit["id"]: i for i, unit in enumerate(units)}
    for i, batch in enumerate(batches):
        payload = {'authoring_version': VERSION, 'export_id': export_id, 'batch_id': f'b{i+1}',
                   'records': [blank_record(u['id'], unit_positions[u["id"]]+1) for u in batch]}
        fd, filename = tempfile.mkstemp(prefix=f'scenario-authoring-{export_id[-8:]}-b{i+1}-', suffix='.md', dir=import_dir)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(INSTRUCTIONS.format(prompt=PROMPT))
            stream.write('\n```text\n'+json.dumps(example, ensure_ascii=False, indent=2)+'\n```\n')
            stream.write(f'\nBatch / 批次 {i+1}/{len(batches)}；units / 單元：'+', '.join(u['id'] for u in batch)+'\n')
            stream.write('\n```json\n'+json.dumps(payload, ensure_ascii=False, indent=2)+'\n```\n')
            for unit in batch:
                stream.write(f"\n## SOURCE {unit['id']} (private / 私密)\n")
                # Four-space indentation prevents source text becoming workbook directives.
                stream.write('\n'.join('    '+line for line in unit['text'].splitlines())+'\n')
                pos = unit_positions[unit["id"]]
                for neighbor in (pos-1, pos+1):
                    if 0 <= neighbor < len(units) and units[neighbor]['chapter_id'] == unit['chapter_id']:
                        other = units[neighbor]
                        stream.write(f"\nContext only / 僅上下文 {other['id']}（必要依賴請另讀該單元完整批次）\n")
                        excerpt = other['text'][-500:] if neighbor < pos else other['text'][:500]
                        stream.write('\n'.join('    '+line for line in excerpt.splitlines())+'\n')
        filenames.append(Path(filename).name)
    atomic_json(directory / 'files.json', filenames)
    return import_dir / filenames[0]


def registry_for(root: Path, payload: dict, source_hash: str, chapter_hash: str) -> tuple[Path, dict]:
    export_id = payload.get('export_id', '')
    if not isinstance(export_id, str) or not re.fullmatch(r'export-[a-f0-9]{32}', export_id):
        raise Diagnostics([issue('UNKNOWN_EXPORT', '', 'export_id', 'original export ID', export_id)])
    directory = root / export_id
    try:
        registry = read_json(directory / 'registry.json')
        checksum = read_json(directory / 'registry.sha256.json')
    except FileNotFoundError as exc:
        raise Diagnostics([issue('STALE_EXPORT', '', 'export_id', 're-export source', export_id)]) from exc
    if (digest(registry) != checksum or registry['source_hash'] != source_hash
            or registry['chapter_hash'] != chapter_hash or registry['segmentation'] != SEGMENTATION):
        raise Diagnostics([issue('STALE_EXPORT', '', 'export_id', 're-export current source', export_id)])
    return directory, registry


def compile_records(records: list, registry: dict, allowed_units: set[str], *, complete: bool) -> list[dict]:
    units = {u['id']: u for u in registry['units']}
    errors: list[dict] = []
    compiled = []
    seen: set[str] = set()
    coverage: set[str] = set()
    for raw in records:
        if not isinstance(raw, dict):
            errors.append(issue('INVALID_RECORD', '', 'record', 'object', type(raw).__name__))
            continue
        rid = raw.get('id', '')
        before = len(errors)
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', rid) or rid in seen:
            errors.append(issue('INVALID_ID', rid, 'id', 'unique ASCII identifier', rid))
        else:
            seen.add(rid)
        ids = raw.get('unit_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(x, str) or x not in allowed_units for x in ids):
            errors.append(issue('UNKNOWN_UNIT', rid, 'unit_ids', 'units in this batch', ids))
            continue
        source_ids = {units[x]['source_id'] for x in ids}
        if len(source_ids) != 1 or len(ids) != len(set(ids)) or coverage.intersection(ids):
            errors.append(issue('UNIT_ASSIGNMENT', rid, 'unit_ids', 'unique units in one source parent; link records across parents', ids))
        coverage.update(ids)
        for key in ('type', 'name', 'public_text', 'kp_text', 'uncertainty'):
            if not isinstance(raw.get(key), str) or (key in ('type', 'name') and not raw[key].strip()):
                errors.append(issue('MISSING_FIELD', rid, key, 'text', raw.get(key)))
        for key in ('aliases', 'keywords', 'related_record_ids', 'rules', 'dependencies'):
            value = raw.get(key, [] if key == 'dependencies' else None)
            if not isinstance(value, list) or len(value) > 100 or (key in ('aliases', 'keywords', 'related_record_ids') and any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in value)):
                errors.append(issue('INVALID_FIELD', rid, key, 'bounded list', value))
        if raw.get('visibility') not in ('public', 'kp_only'):
            errors.append(issue('INVALID_VISIBILITY', rid, 'visibility', 'public / kp_only', raw.get('visibility')))
        if raw.get('visibility') == 'kp_only' and raw.get('public_text'):
            errors.append(issue('PRIVATE_PUBLIC_CONFLICT', rid, 'public_text', 'empty for kp_only', 'nonempty'))
        if not raw.get('public_text') and not raw.get('kp_text') and not raw.get('rules'):
            errors.append(issue('EMPTY_TRANSLATION', rid, 'text', '完整中文內容', 'empty'))
        if len(errors) != before:
            continue
        rules = []
        for ri, rule in enumerate(raw['rules']):
            if not isinstance(rule, dict) or not rule or set(rule) - {'trigger','check','success','failure','exceptions'}:
                errors.append(issue('INVALID_RULE', rid, f'rules[{ri}]', 'supported rule fields', rule))
                continue
            output = {}
            for field, value in rule.items():
                path = f'rules[{ri}].{field}'
                if not isinstance(value, dict) or not isinstance(value.get('text'), str) or not value['text'].strip() or not isinstance(value.get('evidence'), list) or not value['evidence']:
                    errors.append(issue('RULE_EVIDENCE_MISSING', rid, path, 'text + evidence list', value))
                    continue
                if len(value['evidence']) != 1:
                    errors.append(issue('RULE_REPRESENTATION', rid, path, 'one exact quote; split complete subrules', len(value['evidence'])))
                    continue
                quotes = []
                for ev in value['evidence']:
                    if not isinstance(ev, dict) or ev.get('unit_id') not in ids or not isinstance(ev.get('source_quote'), str) or not ev['source_quote'].strip():
                        errors.append(issue('RULE_EVIDENCE_MISSING', rid, path, 'quote from assigned unit', ev))
                        continue
                    quote = ev['source_quote']
                    unit_text = units[ev['unit_id']]['text']
                    count = (len(re.findall('(?=' + re.escape(quote) + ')', unit_text))
                             if len(quote) <= len(unit_text) else 0)
                    if count != 1:
                        errors.append(issue('AMBIGUOUS_QUOTE' if count else 'QUOTE_NOT_FOUND', rid, path, 'one exact occurrence', count))
                    quotes.append(quote)
                if len(quotes) != 1:
                    errors.append(issue('RULE_REPRESENTATION', rid, path, 'one unambiguous quote; split complete subrules', len(quotes)))
                elif quotes:
                    output[field] = {'text': value['text'], 'source_quote': quotes[0]}
            rules.append(output)
        dependencies = raw.get('dependencies', [])
        related = list(raw['related_record_ids'])
        for dep in dependencies:
            if (not isinstance(dep, dict) or not isinstance(dep.get('record_id'), str)
                    or dep.get('kind') not in ('required_for_adjudication', 'conditional', 'background')
                    or not isinstance(dep.get('condition'), str) or not isinstance(dep.get('source_quote'), str)
                    or not dep['source_quote'].strip()
                    or not any(dep['source_quote'] in units[u]['text'] for u in ids)):
                errors.append(issue('INVALID_DEPENDENCY', rid, 'dependencies', 'typed dependency with exact source evidence', dep))
            else:
                related.append(dep['record_id'])
        unit = units[ids[0]]
        compiled.append({**raw, 'schema_version': 4, 'source_id': unit['source_id'],
                         'page': unit['page'], 'source_pages': unit['source_pages'], 'chapter_id': unit['chapter_id'],
                         'source_spans': [units[u]['span'] for u in ids], 'rule_text': '', 'rules': rules,
                         'related_record_ids': list(dict.fromkeys(related)), 'dependencies': dependencies})
    if complete:
        missing = allowed_units - coverage
        if missing:
            errors.append(issue('COVERAGE_GAP', '', 'unit_ids', 'all source units', sorted(missing)))
        for record in compiled:
            for target in record['related_record_ids']:
                if target not in seen:
                    errors.append(issue('UNKNOWN_DEPENDENCY', record['id'], 'related_record_ids', 'existing record ID', target))
    if errors:
        raise Diagnostics(errors)
    return compiled


def import_batch(root: Path, payload: dict, source_hash: str, chapter_hash: str,
                 validate: Any, save: Any) -> str:
    if type(payload.get('authoring_version')) is not int or payload.get('authoring_version') != VERSION:
        raise Diagnostics([issue('AUTHORING_VERSION', '', 'authoring_version', VERSION, payload.get('authoring_version'))])
    with _lock:
        directory, registry = registry_for(root, payload, source_hash, chapter_hash)
        batch_id = payload.get('batch_id')
        if not isinstance(batch_id, str) or batch_id not in registry['batches']:
            raise Diagnostics([issue('UNKNOWN_BATCH', '', 'batch_id', 'exported batch', batch_id)])
        records = payload.get('records')
        if not isinstance(records, list) or not records or len(records) > MAX_RECORDS:
            raise Diagnostics([issue('INVALID_RECORDS', '', 'records', 'nonempty bounded list', type(records).__name__)])
        compile_records(records, registry, set(registry['batches'][batch_id]), complete=False)
        draft_path = directory / 'draft.json'
        draft = read_json(draft_path) if draft_path.exists() else {'batches': {}}
        previous = draft['batches'].get(batch_id)
        if previous is not None and previous != records and payload.get('replace_batch') is not True:
            raise Diagnostics([issue('BATCH_CONFLICT', '', 'replace_batch', 'true for explicit replacement', False)])
        candidate = {**draft['batches'], batch_id: records}
        all_records = [r for batch in candidate.values() for r in batch]
        if len(all_records) > MAX_RECORDS or len(json.dumps(candidate, ensure_ascii=False).encode()) > MAX_FILE_BYTES:
            raise Diagnostics([issue('RESOURCE_LIMIT', '', 'batches', 'at most 20000 records / 20 MB', len(all_records))])
        compiled = compile_records(all_records, registry, {u['id'] for u in registry['units']}, complete=False)
        covered = {u for r in all_records for u in r['unit_ids']}
        if covered != {u['id'] for u in registry['units']}:
            atomic_json(draft_path, {'batches': candidate})
            return f'draft:{payload["export_id"]}:{len(covered)}/{len(registry["units"])}'
        compiled = compile_records(all_records, registry, covered, complete=True)
        issues = validate(compiled)
        checksum = digest(all_records)
        if draft.get('complete_hash') == checksum and draft.get('variant_id'):
            return draft['variant_id']
        variant_id = save(compiled, issues)
        atomic_json(draft_path, {'batches': candidate, 'complete_hash': checksum, 'variant_id': variant_id})
        return variant_id
