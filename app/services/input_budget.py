"""Read-only history selection and explicitly approximate token accounting."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app import observability

# Loading a tokenizer reaches the network whenever the BPE cache is cold, so it
# can fail for reasons that do not last. A failure is therefore retried rather
# than cached: pinning one transient fault for the process lifetime would bill
# every later estimate at UTF-8 bytes, which overcounts CJK roughly threefold
# and starves the scenario retrieval budget to zero without ever recovering.
ENCODING_RETRY_SECONDS = 60.0
_MAX_CACHED_ENCODINGS = 8
_encoding_lock = threading.Lock()
_encodings: dict[str, Any] = {}
_encoding_retry_after: dict[str, float] = {}
_encoding_failures: dict[str, int] = {}


def _load_encoding(model: str) -> Any:
    import tiktoken
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding('o200k_base')


def _encoding(model: str) -> Any:
    with _encoding_lock:
        resolved = _encodings.get(model)
        if resolved is not None:
            return resolved
        if time.monotonic() < _encoding_retry_after.get(model, 0.0):
            return None
    # Loaded outside the lock: a slow download must not block other turns.
    try:
        encoding = _load_encoding(model)
    except Exception as exc:  # noqa: BLE001 - no encoding must not prevent a player turn
        with _encoding_lock:
            _encoding_retry_after[model] = time.monotonic() + ENCODING_RETRY_SECONDS
            attempts = _encoding_failures[model] = _encoding_failures.get(model, 0) + 1
        observability.event("llm.tokenizer.unavailable", level=logging.WARNING,
                            tokenizer="utf8_bytes_fallback", error_type=type(exc).__name__,
                            failed_attempts=attempts, retry_after_seconds=ENCODING_RETRY_SECONDS,
                            remediation="install_declared_tiktoken_dependency_and_check_encoding_cache")
        return None
    with _encoding_lock:
        recovered = _encoding_failures.pop(model, 0)
        _encoding_retry_after.pop(model, None)
        if len(_encodings) >= _MAX_CACHED_ENCODINGS:
            _encodings.clear()
        _encodings[model] = encoding
    if recovered:
        observability.event("llm.tokenizer.recovered", tokenizer=getattr(encoding, 'name', 'unknown'),
                            failed_attempts=recovered)
    return encoding


def reset_encoding_cache() -> None:
    """Forget resolved tokenizers and retry deadlines. For tests and reloads."""
    with _encoding_lock:
        _encodings.clear()
        _encoding_retry_after.clear()
        _encoding_failures.clear()


def tokenizer_method(model: str) -> str:
    return "tokenizer_estimate" if _encoding(model) is not None else "utf8_bytes_fallback"


def estimate(value, model: str) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)
    encoding = _encoding(model)
    if encoding is None:
        return len(text.encode('utf-8'))  # conservative fallback, not a measured token count
    return len(encoding.encode(text, disallowed_special=()))


def select_history(history: list[dict], model: str, budget: int, keep_turns: int = 2) -> list[dict]:
    _encoding(model)  # called in the provider worker thread, never lazy network I/O on the loop
    if budget <= 0 or not history:
        return list(history)
    starts = [i for i, entry in enumerate(history) if entry.get('role') == 'user']
    before = estimate(history, model)
    start = 0
    # Keep whole user-led sections, including their tool outputs. The newest
    # sections are a soft minimum even when a single section exceeds budget.
    if len(starts) > keep_turns and before > budget:
        start = starts[-keep_turns]
        for candidate in reversed(starts[:-keep_turns]):
            if estimate(history[candidate:], model) > budget:
                break
            start = candidate
    selected = list(history[start:])
    after = estimate(selected, model)
    encoding = _encoding(model)
    observability.event('llm.history.selected', before_tokens_estimate=before,
                        after_tokens_estimate=after, entries_removed=start, budget=budget,
                        budget_exceeded=after > budget,
                        tokenizer=getattr(encoding, 'name', 'utf8_bytes_fallback'))
    return selected


def provider_history(history: list[dict], model: str, provider: str) -> list[dict]:
    """Share the provider's history selection with retrieval admission checks."""
    if provider == 'openai':
        from app import config
        return select_history(history, model, config.OPENAI_HISTORY_TOKEN_BUDGET,
                              config.OPENAI_HISTORY_MIN_TURNS)
    return list(history)
