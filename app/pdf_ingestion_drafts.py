"""Atomic, private import checkpoints; these are never scenario-library entries."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from app.config import SCENARIO_LIBRARY_DIR

SCHEMA_VERSION = 1


class ContinueImportMessage(str):
    """Adapter-visible invitation to resume a particular private draft."""

    def __new__(cls, text: str, draft_id: str) -> Self:
        value = super().__new__(cls, text)
        value.draft_id = draft_id
        return value

    draft_id: str


def _path(conversation_id: str) -> Path:
    key = hashlib.sha256(conversation_id.encode()).hexdigest()
    return SCENARIO_LIBRARY_DIR / '.ingestion-drafts' / f'{key}.json'


def load(conversation_id: str) -> dict[str, Any] | None:
    path = _path(conversation_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != SCHEMA_VERSION or data.get('conversation_id') != conversation_id:
        raise ValueError('Unsupported PDF draft identity or schema')
    payload = base64.b64decode(data['pdf'], validate=True)
    if hashlib.sha256(payload).hexdigest() != data['pdf_sha256']:
        raise ValueError('PDF draft source hash mismatch')
    return data


def save(conversation_id: str, pdf_bytes: bytes, file_name: str, report: dict,
         result: tuple, *, owner_id: str = '', reparse_candidate_id: str | None = None) -> dict:
    _, _, _, images, maps = result
    pages = report.get('pages', [])
    rows = pages.values() if isinstance(pages, dict) else pages
    source_hash = hashlib.sha256(pdf_bytes).hexdigest()
    records = {}
    for row in rows:
        page = int(row.get('pdf_page', row.get('page', 0)))
        text = row.get('selected_text', '')
        image = images.get(page, images.get(str(page)))
        records[str(page)] = {
            'pdf_sha256': source_hash, 'pipeline_version': report.get('pipeline_version', ''),
            'selected_text': text, 'selected_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'report': row, 'image': base64.b64encode(image).decode() if image else None,
            'map': maps.get(page, maps.get(str(page))),
            'derived_description': row.get('derived_description', ''),
        }
    data = {'schema_version': SCHEMA_VERSION, 'conversation_id': conversation_id,
            'draft_id': uuid4().hex, 'pdf_sha256': source_hash, 'file_name': file_name,
            'owner_id': owner_id, 'reparse_candidate_id': reparse_candidate_id,
            'pipeline_version': report.get('pipeline_version', ''), 'report': report,
            'pages': records, 'pdf': base64.b64encode(pdf_bytes).decode()}
    path = _path(conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.draft-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return data


def pdf_bytes(draft: dict) -> bytes:
    return base64.b64decode(draft['pdf'], validate=True)


def resume_pages(draft: dict, pipeline_version: str) -> dict[int, dict]:
    if draft['pipeline_version'] != pipeline_version:
        return {}
    cached = {}
    for page, stored in draft['pages'].items():
        row = stored['report']
        if row.get('disposition', row.get('status')) not in ('accepted', 'legacy_route'):
            continue
        record = dict(stored)
        if record['image']:
            record['image'] = base64.b64decode(record['image'], validate=True)
        cached[int(page)] = record
    return cached


def discard(conversation_id: str, draft_id: str = '') -> None:
    draft = load(conversation_id)
    if draft and (not draft_id or draft['draft_id'] == draft_id):
        _path(conversation_id).unlink(missing_ok=True)


def progress(draft: dict) -> str:
    unresolved = [page for page, item in draft['pages'].items()
                  if item['report'].get('disposition', item['report'].get('status')) == 'needs_review']
    return (f"《{draft['file_name']}》匯入草稿已保存；待修復頁面：{', '.join(unresolved) or '未知'}。"
            '\n使用 /coc scenario continue 繼續匯入；/coc scenario status 查看；/coc scenario cancel 取消。')
