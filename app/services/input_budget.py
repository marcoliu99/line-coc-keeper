"""Read-only history selection and explicitly approximate token accounting."""
from __future__ import annotations

import json
from functools import lru_cache

from app import observability


@lru_cache(maxsize=8)
def _encoding(model: str):
    try:
        import tiktoken
        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            return tiktoken.get_encoding('o200k_base')
    except Exception:  # noqa: BLE001 - no encoding must not prevent a player turn
        return None


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
