"""Private durable single-dispatch evidence for optional source-text extraction."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from app import config


class DispatchRecord(TypedDict):
    stage: str
    window_sha256: str
    consumed_requests: int
    status: Literal['reserved', 'completed', 'failed']
    response: dict | None
    error_type: NotRequired[str]


def bounded_window(source: str, stage: str, *, whole_book: bool = True) -> str:
    """At most three physical pages/12k chars; never a multi-page whole book."""
    pages = list(re.finditer(r'^--- 第 [1-9][0-9]* 頁 ---\n', source, re.MULTILINE))
    if pages:
        start = 0
        if stage == 'report_pregens':
            for index, marker in enumerate(pages):
                end = pages[index + 1].start() if index + 1 < len(pages) else len(source)
                if re.search(r'(?i)CHARACTERISTICS|屬性', source[marker.end():end]):
                    start = index
                    break
        count = min(3, max(1, len(pages) - 1) if whole_book else len(pages))
        end_index = min(len(pages), start + count)
        end = pages[end_index].start() if end_index < len(pages) else len(source)
        source = source[pages[start].start():end]
    return source[:12000]


def analyze(provider: Any, source: str, tool: dict, prompt: str, *, window: str | None = None) -> dict | None:
    """Reserve a private one-shot record before SDK dispatch, including failures."""
    try:
        return _analyze(provider, source, tool, prompt, window=window)
    except (OSError, ValueError):
        return None


def _analyze(provider: Any, source: str, tool: dict, prompt: str, *, window: str | None) -> dict | None:
    window = bounded_window(window or source, tool['name'],
                            whole_book=window is None or window.strip() == source.strip())
    if not window.strip():
        return None
    accessor = getattr(provider, 'analysis_model_identity', None)
    model_identity = accessor() if callable(accessor) else config.CODEX_MODEL
    identity = json.dumps([hashlib.sha256(source.encode()).hexdigest(), window, tool,
                           config.LLM_PROVIDER, config.ANALYSIS_PROVIDER, str(model_identity), prompt], sort_keys=True)
    key = hashlib.sha256(identity.encode()).hexdigest()
    directory = config.SCENARIO_LIBRARY_DIR / '.source-analysis'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (key + '.json')
    lock = directory / (key + '.lock')
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR, 0o600), 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if path.exists():
            saved = json.loads(path.read_text())
            if not isinstance(saved, dict) or saved.get('consumed_requests') != 1:
                return None
            response = saved.get('response')
            return response if saved.get('status') == 'completed' and isinstance(response, dict) else None
        record: DispatchRecord = {'stage': tool['name'], 'window_sha256': hashlib.sha256(window.encode()).hexdigest(),
                        'consumed_requests': 1, 'status': 'reserved', 'response': None}
        def checkpoint() -> None:
            temporary = Path(str(path) + '.tmp')
            with os.fdopen(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), 'w') as output:
                json.dump(record, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        checkpoint()
        try:
            response = provider.analyze_text(window, tool, prompt,
                timeout=config.PDF_LAYOUT_IMAGE_TIMEOUT_SECONDS, max_retries=0)
            record['response'] = response if isinstance(response, dict) else None
            record['status'] = 'completed' if isinstance(response, dict) else 'failed'
        except Exception as error:  # noqa: BLE001 - optional metadata cannot prevent source publication.
            record['status'] = 'failed'
            record['error_type'] = type(error).__name__
        checkpoint()
        return record['response']
