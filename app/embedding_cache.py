"""Shared query-embedding cache for app/scenario_rag.py and app/memory_rag.py.

Both modules independently call OpenAI's embeddings API for a QUERY string
via their own _embed_texts, whenever the Keeper calls the search_scenario/
search_memory tools (app/keeper.py's _execute_tool) — including, in the same
turn, for the same or overlapping keywords (e.g. searching an NPC's name in
both the scenario and past memory). Ported from main_v2's
feature/query-embedding-cache, where this was confirmed in production logs
to be a *guaranteed* duplicate on every turn (main_v2's app/agents/
context_builder.py runs scenario_rag.search() and memory_rag.search_memory()
proactively with the identical query text). This branch (single-file
app/keeper.py architecture, no context_builder) doesn't have that
structural guarantee — the AI decides independently whether/when to call
each tool — but the cache still pays off whenever a turn happens to repeat
or overlap a query, at effectively zero cost when it doesn't.

Because both files embed via the same SCENARIO_RAG_EMBEDDING_MODEL, a cache
keyed by (model, text) shared across both files' calls catches a cross-file
duplicate (e.g. the Keeper searching the same term in both scenario and
memory); two independent per-file caches would not.

Only ever caches a successful embedding — a failed/unavailable call (no
OPENAI_API_KEY, transient network error, ...) is never stored, so a
transient failure doesn't get permanently remembered as "no embedding for
this text"; the caller's existing BM25 fallback behavior on a cache miss is
unchanged from before this module existed.

Deliberately scoped to query-text embeddings only — not wired into either
file's scenario/memory *index*-building path (embedding many distinct
chunks of scenario/memory content in bulk), where cache hits would be rare
to nonexistent and not worth the bookkeeping.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable

# Small and bounded on purpose: entries are short player-message-shaped
# query strings, not scenario content, so even a modest cap comfortably
# covers realistic reuse across nearby turns without unbounded growth over
# a long-running process. Evicts the least-recently-used entry once full.
_MAX_ENTRIES = 512
_cache: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()


def get_query_embedding(
    model: str, text: str, embed_one: Callable[[], list[float] | None]
) -> list[float] | None:
    """Returns the cached embedding for (model, text) if present (and marks
    it most-recently-used); otherwise calls embed_one() to compute it,
    caching the result only if it succeeded. `embed_one` takes no arguments
    — callers close over whatever text their own _embed_texts call needs;
    this module never calls the embeddings API itself."""
    key = (model, text)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    embedding = embed_one()
    if embedding is None:
        return None
    _cache[key] = embedding
    _cache.move_to_end(key)
    if len(_cache) > _MAX_ENTRIES:
        _cache.popitem(last=False)
    return embedding


def clear() -> None:
    """Test-only escape hatch — production code never needs to clear this;
    a stale entry is never wrong (embeddings are a pure function of
    (model, text)), only ever a bounded amount of memory."""
    _cache.clear()
