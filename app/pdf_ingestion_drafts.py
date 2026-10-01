"""Atomic, private import checkpoints; these are never scenario-library entries."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, Self, TypeVar
from uuid import uuid4

from app.config import SCENARIO_LIBRARY_DIR

SCHEMA_VERSION = 2
_PROCESS_ID = uuid4().hex
_LOCK = threading.RLock()
_P = ParamSpec('_P')
_R = TypeVar('_R')


def _serialized(function: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(function)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _LOCK:
            return function(*args, **kwargs)
    return guarded


class ImportOwnershipError(ValueError):
    """The reservation was canceled, replaced, or is already being processed."""


@dataclass(frozen=True)
class ImportLease:
    conversation_id: str
    draft_id: str
    attempt_id: str


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


@_serialized
def load(conversation_id: str) -> dict[str, Any] | None:
    path = _path(conversation_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') not in (1, SCHEMA_VERSION) or data.get('conversation_id') != conversation_id:
        raise ValueError('Unsupported PDF draft identity or schema')
    payload = base64.b64decode(data['pdf'], validate=True)
    if hashlib.sha256(payload).hexdigest() != data['pdf_sha256']:
        raise ValueError('PDF draft source hash mismatch')
    return data


def _write(conversation_id: str, data: dict) -> None:
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


@_serialized
def reserve(conversation_id: str, source: bytes, file_name: str, *, owner_id: str = '',
            resume_draft_id: str = '', reparse_candidate_id: str | None = None) -> ImportLease:
    """Reserve before any parser work; caller holds the conversation lock.

    A persisted process token lets a restarted worker recover an interrupted
    attempt. Concurrent attempts in this worker cannot acquire the same draft.
    """
    draft = load(conversation_id)
    source_hash = hashlib.sha256(source).hexdigest()
    if draft:
        if not resume_draft_id or draft['draft_id'] != resume_draft_id:
            raise ImportOwnershipError('已有 PDF 匯入草稿，請先繼續或取消。')
        if (draft['pdf_sha256'] != source_hash or draft['file_name'] != file_name
                or draft.get('reparse_candidate_id') != reparse_candidate_id):
            raise ImportOwnershipError('匯入來源與草稿不符。')
        if draft.get('attempt_id') and draft.get('process_id') == _PROCESS_ID:
            raise ImportOwnershipError('這份 PDF 正在處理中，請稍候再查看進度。')
    elif resume_draft_id:
        raise ImportOwnershipError('匯入草稿已取消或變更，請重新查看進度。')
    else:
        draft = {'schema_version': SCHEMA_VERSION, 'conversation_id': conversation_id,
                 'draft_id': uuid4().hex, 'pdf_sha256': source_hash, 'file_name': file_name,
                 'owner_id': owner_id, 'reparse_candidate_id': reparse_candidate_id,
                 'pipeline_version': '', 'extraction_identity': {}, 'report': {}, 'pages': {},
                 'pdf': base64.b64encode(source).decode()}
    draft.update(schema_version=SCHEMA_VERSION, attempt_id=uuid4().hex, process_id=_PROCESS_ID)
    _write(conversation_id, draft)
    return ImportLease(conversation_id, draft['draft_id'], draft['attempt_id'])


@_serialized
def owns(lease: ImportLease) -> bool:
    """Central identity gate for checkpoint, publication and cancellation."""
    current = load(lease.conversation_id)
    return bool(current and current['draft_id'] == lease.draft_id
                and current.get('attempt_id') == lease.attempt_id
                and current.get('process_id') == _PROCESS_ID)


@_serialized
def require_owner(lease: ImportLease) -> dict:
    if not owns(lease):
        raise ImportOwnershipError('匯入草稿已取消或變更，請重新查看 /coc scenario status。')
    current = load(lease.conversation_id)
    assert current is not None
    return current


@_serialized
def release(lease: ImportLease) -> None:
    """Retain source/evidence while releasing a completed or failed attempt."""
    if owns(lease):
        current = require_owner(lease)
        current['attempt_id'] = ''
        _write(lease.conversation_id, current)


@_serialized
def discard_owned(lease: ImportLease) -> None:
    require_owner(lease)
    _path(lease.conversation_id).unlink(missing_ok=True)


@_serialized
def record_budget(lease: ImportLease, budget: dict) -> None:
    """Persist usage before dispatch, including requests interrupted by a crash."""
    current = require_owner(lease)
    current.setdefault('report', {})['layout_budget'] = copy.deepcopy(budget)
    _write(lease.conversation_id, current)


@_serialized
def checkpoint(lease: ImportLease, report: dict, result: tuple, *, release_attempt: bool = True) -> dict:
    """Only the active reservation can replace its durable page checkpoint."""
    data = require_owner(lease)
    _, _, _, images, maps = result
    pages = report.get('pages', [])
    rows = pages.values() if isinstance(pages, dict) else pages
    records = {}
    descriptions = report.get('derived_descriptions', {})
    for row in rows:
        page = int(row.get('pdf_page', row.get('page', 0)))
        text = row.get('selected_text', '')
        image = images.get(page, images.get(str(page)))
        stored_row = copy.deepcopy(row)
        if row.get('map_analysis'):
            previous = data.get('pages', {}).get(str(page), {}).get('report', {})
            history = copy.deepcopy(previous.get('map_analysis_history', []))
            if (not row.get('resumed') and previous.get('map_analysis')
                    and previous.get('map_analysis_attempt_id') != lease.attempt_id):
                history.append(copy.deepcopy(previous['map_analysis']))
            stored_row['map_analysis_history'] = history
            if not row.get('resumed'):
                stored_row['map_analysis_attempt_id'] = lease.attempt_id
        records[str(page)] = {
            'pdf_sha256': data['pdf_sha256'], 'pipeline_version': report.get('pipeline_version', ''),
            'renderer_version': report.get('renderer_version'),
            'extraction_identity': report.get('extraction_identity', {}),
            'selected_text': text, 'selected_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'report': stored_row, 'image': base64.b64encode(image).decode() if image else None,
            'map': maps.get(page, maps.get(str(page))),
            'derived_description': descriptions.get(str(page), descriptions.get(page, row.get('derived_description', ''))),
        }
    data.update(pipeline_version=report.get('pipeline_version', ''),
                extraction_identity=report.get('extraction_identity', {}),
                renderer_version=report.get('renderer_version'), report=report, pages=records,
                attempt_id='' if release_attempt else lease.attempt_id)
    _write(lease.conversation_id, data)
    return data


def pdf_bytes(draft: dict) -> bytes:
    return base64.b64decode(draft['pdf'], validate=True)


def resume_pages(draft: dict, identity: dict) -> dict[int, dict]:
    if draft.get('extraction_identity') != identity or draft.get('renderer_version') != identity.get('renderer_version'):
        return {}
    cached = {}
    for page, stored in draft['pages'].items():
        row = stored['report']
        if row.get('disposition', row.get('status')) not in ('accepted', 'legacy_route', 'soft_review'):
            continue
        if row.get('publication_severity') == 'HARD_BLOCK' or row.get('source_blocking_reasons'):
            continue
        record = dict(stored)
        if row.get('map_analysis') and row['map_analysis'].get('status') != 'MAP_GRAPH_VERIFIED':
            record['map'] = None  # Candidate provenance is never a reusable gameplay artifact.
        if record['image']:
            record['image'] = base64.b64decode(record['image'], validate=True)
        cached[int(page)] = record
    return cached


@_serialized
def discard(conversation_id: str, draft_id: str = '') -> None:
    draft = load(conversation_id)
    if draft and (not draft_id or draft['draft_id'] == draft_id):
        _path(conversation_id).unlink(missing_ok=True)


def progress(draft: dict) -> str:
    unresolved = [page for page, item in draft['pages'].items()
                  if item['report'].get('disposition', item['report'].get('status')) == 'needs_review']
    message = (f"《{draft['file_name']}》匯入草稿已保存；待修復頁面：{', '.join(unresolved) or '處理中／尚未解析'}。"
               '\n使用 /coc scenario continue 繼續匯入；/coc scenario status 查看；/coc scenario cancel 取消。')
    unverified = [page for page, item in draft['pages'].items()
                  if item['report'].get('image_transcription', {}).get('status') == 'unverified'
                  and item['report'].get('disposition') == 'needs_review']
    if unverified:
        message += (f"\n第 {', '.join(unverified)} 頁影像轉錄尚未驗證；候選僅存於私人草稿，未發布。"
                    '需要 AI/provider verification（設定影像供應商後繼續）或 manual approval（人工核對原稿與核准）。')
    budget = draft.get('report', {}).get('layout_budget', {})
    unseen = any(int(page) not in budget.get('visited_pages', []) for page in unresolved)
    if budget and (budget.get('remaining_requests', 0) <= 0
                   or (unseen and budget.get('remaining_pages', 0) <= 0)):
        message += ('\n影像分析額度已用盡。請 KP 調高伺服器設定 PDF_LAYOUT_MAX_REQUESTS／'
                    'PDF_LAYOUT_MAX_PAGES 後重新啟動服務再繼續，或取消匯入。既有使用量仍會保留。')
    return message
