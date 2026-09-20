"""Shared query-embedding cache for app/scenario_rag.py and app/memory_rag.py.

Both modules independently call OpenAI's embeddings API for a QUERY string
via their own _embed_texts. app/agents/context_builder.py's proactive
per-turn RAG (see that module's _run_scenario_rag/_run_memory_rag) calls
scenario_rag.search() and memory_rag.search_memory() with the *same* `text`
argument — the player's raw message — whenever both proactive searches run
for a turn. Confirmed in production logs (2026-09-20) that this genuinely
double-embeds the identical string every such turn, not an occasional
coincidence: it's guaranteed by both call sites always receiving the same
argument. Because both files embed via the same SCENARIO_RAG_EMBEDDING_MODEL,
a cache keyed by (model, text) *shared* across both files' calls catches
this; two independent per-file caches would not, since the duplicate is
cross-file, not repeated calls within one file.

Only ever caches a successful embedding — a failed/unavailable call (no
OPENAI_API_KEY, transient network error, ...) is never stored, so a
transient failure doesn't get permanently remembered as "no embedding for
this text"; the caller's existing BM25 fallback behavior on a cache miss is
unchanged from before this module existed.

Deliberately scoped to query-text embeddings only — not wired into either
file's scenario/memory *index*-building path (embedding many distinct
chunks of scenario/memory content in bulk), where cache hits would be rare
to nonexistent and not worth the bookkeeping.

Thread-safe by design, not as an afterthought: app/commands.py dispatches
different conversations' Keeper turns via `asyncio.to_thread`, and
app/locks.py's own docstring confirms different conversations run fully
concurrently on separate OS threads — this module's `_cache` is process-wide
shared state, exactly the pattern app/dictionary.py already guards with a
`threading.Lock()`. A PR review caught two real bugs in the original
unsynchronized version:
1. `_cache.get(key)` followed by a separate `_cache.move_to_end(key)` isn't
   atomic as a pair — another thread's insert-triggered eviction could
   remove `key` in between, making `move_to_end` raise `KeyError` and abort
   the caller's search mid-turn. Both operations now happen under one lock
   acquisition.
2. Two threads racing on the same not-yet-cached (model, text) key could
   both miss and both pay for a duplicate embeddings API call — exactly the
   redundant call this module exists to eliminate. `_in_flight` below makes
   the second (and any further) caller for the same key wait for the
   first's result instead of starting its own redundant embed_one() call.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable

# Small and bounded on purpose: entries are short player-message-shaped
# query strings, not scenario content, so even a modest cap comfortably
# covers realistic reuse across nearby turns without unbounded growth over
# a long-running process. Evicts the least-recently-used entry once full.
_MAX_ENTRIES = 512

_lock = threading.Lock()
_cache: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
# One Event per (model, text) currently being embedded — lets a second
# caller for the same key wait for the first (the "leader") instead of
# issuing its own redundant embed_one() call. Only ever touched while
# holding _lock.
_in_flight: "dict[tuple[str, str], threading.Event]" = {}


def get_query_embedding(
    model: str, text: str, embed_one: Callable[[], list[float] | None]
) -> list[float] | None:
    """Returns the cached embedding for (model, text) if present (and marks
    it most-recently-used); otherwise either becomes the "leader" that calls
    embed_one() to compute it (caching the result only on success), or — if
    another thread is already computing this exact (model, text) — waits
    for that thread instead of calling embed_one() itself. `embed_one` takes
    no arguments — callers close over whatever text/rag_kind their own
    _embed_texts call needs; this module never calls the embeddings API
    itself, and never calls embed_one() while holding `_lock` (it's only
    ever held for quick dict/OrderedDict bookkeeping)."""
    key = (model, text)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
        event = _in_flight.get(key)
        is_leader = event is None
        if is_leader:
            event = threading.Event()
            _in_flight[key] = event

    if not is_leader:
        event.wait()
        with _lock:
            result = _cache.get(key)
            if result is not None:
                _cache.move_to_end(key)
            return result

    try:
        embedding = embed_one()
    except BaseException:
        with _lock:
            del _in_flight[key]
            event.set()
        raise

    with _lock:
        if embedding is not None:
            _cache[key] = embedding
            _cache.move_to_end(key)
            if len(_cache) > _MAX_ENTRIES:
                _cache.popitem(last=False)
        del _in_flight[key]
        event.set()
    return embedding


def clear() -> None:
    """Test-only escape hatch — production code never needs to clear this;
    a stale cache entry is never wrong (embeddings are a pure function of
    (model, text)), only ever a bounded amount of memory. Does not touch
    `_in_flight` — tests should not call this while a get_query_embedding()
    call from another thread might be in flight."""
    with _lock:
        _cache.clear()
