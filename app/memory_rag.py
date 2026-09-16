"""Semantic search over *trimmed* conversation history — the long-term memory
layer beyond app/keeper.py's rolling campaign_summary.

Why this exists alongside campaign_summary: a rolling summary is "old summary
+ new chunk -> updated summary" — every trim recompresses the *already
compressed* text, so over a long enough campaign (many trims) fine detail
keeps eroding a little more each pass. This module keeps the actual dropped
text too, indexed for semantic retrieval, so a player asking about something
from months ago can still be answered from the original wording, not just
whatever survived N rounds of re-summarization — see app/keeper.py's run_turn
(same trim point that calls summarize_log_chunk also calls append_memory)
and the search_memory tool.

Deliberately a separate module from app/scenario_rag.py rather than a shared
refactor of it, even though the BM25+embeddings scoring logic is almost
identical — that module is live in production (SCENARIO_RAG_ENABLED=true) and
this project would rather carry a little duplication than risk regressing a
working, already-tested feature by restructuring it to fit a second caller.

Unlike scenario_rag (which rebuilds its index from state.scenario_text, a
single string that's replaced wholesale on a new PDF upload), conversation
memory *accumulates* — chunks are appended one at a time as they're trimmed
off app/keeper.py's state.log, and persisted via app/db.py (SQLite) so they
survive a bot restart instead of only living in an in-memory cache.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app import db
from app.config import OPENAI_API_KEY, SCENARIO_RAG_EMBEDDING_MODEL, SCENARIO_RAG_EMBEDDING_WEIGHT

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]+")

_K1 = 1.5  # BM25 term-frequency saturation
_B = 0.75  # BM25 length-normalization strength

# Same relevance gate and calibration as app/scenario_rag.py's
# _MIN_COSINE_RELEVANCE — see that constant's own comment for the full
# derivation (a blended/normalized score doesn't work as a relevance gate;
# raw cosine similarity does). Not independently re-calibrated against real
# memory chunk data (this project's production memory_chunks table was empty
# at the time this was added — no long-enough campaign had triggered a trim
# yet), but the same embedding model and scoring shape are shared with
# scenario_rag, so the same threshold is a reasonable starting point.
_MIN_COSINE_RELEVANCE = 0.32


def _tokenize(text: str) -> list[str]:
    """Same scheme as app/scenario_rag.py's _tokenize — CJK runs become
    overlapping bigrams, ASCII words lowercase whole."""
    tokens = [w.lower() for w in _ASCII_WORD_RE.findall(text)]
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Best-effort: None if no OPENAI_API_KEY or the call fails — callers
    fall back to pure BM25 in that case, same contract as scenario_rag.py."""
    if not OPENAI_API_KEY or not texts:
        return None
    try:
        import openai

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.embeddings.create(model=SCENARIO_RAG_EMBEDDING_MODEL, input=texts)
        ordered = [None] * len(texts)
        for item in response.data:
            ordered[item.index] = item.embedding
        if any(v is None for v in ordered):
            return None
        return ordered
    except Exception:
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class _Chunk:
    label: str  # human-readable "roughly when" hint, e.g. "記憶片段 #3" — not
    # meant to be precise, just enough for a search result to be citable
    text: str
    tokens: list[str] = field(default_factory=list)
    term_counts: dict[str, int] = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass
class MemoryIndex:
    chunks: list[_Chunk]
    doc_freq: dict[str, int]
    avg_length: float
    has_embeddings: bool = False


def _load_raw_chunks(group_id: str) -> list[dict]:
    """Persisted via app/db.py (SQLite) rather than a standalone
    data/groups/<id>_memory.json file — same "one JSON blob per key" shape
    as before, just a different storage backend. A missing row or corrupted/
    unexpected content both degrade to "no memory yet", not a crash."""
    try:
        return db.get_json("memory_chunks", group_id) or []
    except Exception:
        return []


def _save_raw_chunks(group_id: str, raw_chunks: list[dict]) -> None:
    db.set_json("memory_chunks", group_id, raw_chunks)


def append_memory(group_id: str, text: str) -> None:
    """Called once per rolling-summarization trim (see app/keeper.py's
    run_turn) — persists the chunk being dropped from state.log, embedding it
    best-effort (never raises; no OPENAI_API_KEY or a failed call just stores
    it without an embedding, still searchable via BM25 alone)."""
    if not text.strip():
        return
    raw_chunks = _load_raw_chunks(group_id)
    label = f"記憶片段 #{len(raw_chunks) + 1}"
    embedding = None
    try:
        embedded = _embed_texts([text])
        if embedded is not None:
            embedding = embedded[0]
    except Exception:
        pass
    raw_chunks.append({"label": label, "text": text, "embedding": embedding})
    _save_raw_chunks(group_id, raw_chunks)


def _build_index(raw_chunks: list[dict]) -> MemoryIndex:
    chunks = []
    doc_freq: dict[str, int] = {}
    total_length = 0
    for raw in raw_chunks:
        text = raw.get("text", "")
        tokens = _tokenize(text)
        term_counts: dict[str, int] = {}
        for t in tokens:
            term_counts[t] = term_counts.get(t, 0) + 1
        for t in term_counts:
            doc_freq[t] = doc_freq.get(t, 0) + 1
        total_length += len(tokens)
        chunks.append(_Chunk(
            label=raw.get("label", ""), text=text, tokens=tokens,
            term_counts=term_counts, embedding=raw.get("embedding"),
        ))
    avg_length = (total_length / len(chunks)) if chunks else 0.0
    has_embeddings = any(c.embedding is not None for c in chunks)
    return MemoryIndex(chunks=chunks, doc_freq=doc_freq, avg_length=avg_length, has_embeddings=has_embeddings)


def _idf_cache(index: MemoryIndex, query_tokens: list[str]) -> dict[str, float]:
    """Precomputes each query term's IDF once per search — it only depends on
    n_docs/doc_freq (both index-level, not per-chunk), so recomputing it
    inside _bm25_score's per-chunk loop (as this used to) redid the same
    math.log call once per (chunk, term) pair instead of once per term:
    O(chunks * query_terms) work for a value that's actually O(query_terms)."""
    n_docs = len(index.chunks)
    return {
        term: math.log((n_docs - index.doc_freq.get(term, 0) + 0.5) / (index.doc_freq.get(term, 0) + 0.5) + 1)
        for term in set(query_tokens)
    }


def _bm25_score(index: MemoryIndex, query_tokens: list[str], chunk: _Chunk, idf_cache: dict[str, float]) -> float:
    score = 0.0
    doc_len = len(chunk.tokens)
    for term in set(query_tokens):
        freq = chunk.term_counts.get(term, 0)
        if freq == 0:
            continue
        denom = freq + _K1 * (1 - _B + _B * doc_len / (index.avg_length or 1))
        score += idf_cache[term] * (freq * (_K1 + 1)) / denom
    return score


_index_cache: dict[str, MemoryIndex] = {}


def _get_index(group_id: str, raw_chunks: list[dict]) -> MemoryIndex:
    """Rebuilding was originally unconditional (tokenizing a handful of short
    trimmed chunks is cheap early on), but chunks only ever accumulate as a
    campaign goes on — a year-long campaign can build up hundreds of them,
    and every search_memory call (one per query the Keeper decides to run)
    was re-tokenizing all of them from scratch. Cache the built index per
    group_id, keyed on chunk count: since append_memory only ever appends
    (existing chunks are immutable once written), a count mismatch against
    the freshly-loaded raw_chunks is both necessary and sufficient to detect
    a new chunk and rebuild — no separate invalidation call needed from
    append_memory itself."""
    cached = _index_cache.get(group_id)
    if cached is not None and len(cached.chunks) == len(raw_chunks):
        return cached
    index = _build_index(raw_chunks)
    _index_cache[group_id] = index
    return index


def search_memory(group_id: str, query: str, top_k: int = 3) -> list[dict]:
    """Returns up to top_k {"label": str, "text": str, "score": float},
    highest first. Empty list if there's no memory yet or nothing matches —
    callers should treat that as "nothing found", not an error. Same hybrid
    BM25 + (if any chunk has one) cosine-similarity blend as
    app/scenario_rag.py's search(), including that module's _MIN_COSINE_RELEVANCE
    gate on purely-semantic (no literal BM25 hit) candidates."""
    raw_chunks = _load_raw_chunks(group_id)
    if not raw_chunks:
        return []
    index = _get_index(group_id, raw_chunks)

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    idf_cache = _idf_cache(index, query_tokens)
    bm25_raw = {id(c): _bm25_score(index, query_tokens, c, idf_cache) for c in index.chunks}
    matched = [c for c in index.chunks if bm25_raw[id(c)] > 0]

    if not index.has_embeddings:
        scored = sorted(((bm25_raw[id(c)], c) for c in matched), key=lambda sc: -sc[0])
        return [{"label": c.label, "text": c.text, "score": s} for s, c in scored[:top_k]]

    query_embedding = _embed_texts([query])
    if query_embedding is None:
        scored = sorted(((bm25_raw[id(c)], c) for c in matched), key=lambda sc: -sc[0])
        return [{"label": c.label, "text": c.text, "score": s} for s, c in scored[:top_k]]
    query_vec = query_embedding[0]

    max_bm25 = max(bm25_raw.values(), default=0.0) or 1.0
    weight = max(0.0, min(1.0, SCENARIO_RAG_EMBEDDING_WEIGHT))

    candidates = {id(c) for c in matched}  # a literal BM25 hit is always trusted, regardless of cosine
    cosine_scores: dict[int, float] = {}
    for c in index.chunks:
        if c.embedding is None:
            continue
        cos = _cosine_similarity(query_vec, c.embedding)
        cosine_scores[id(c)] = cos
        if cos >= _MIN_COSINE_RELEVANCE:
            candidates.add(id(c))

    by_id = {id(c): c for c in index.chunks}
    combined: list[tuple[float, _Chunk]] = []
    for cid in candidates:
        c = by_id[cid]
        bm25_norm = bm25_raw.get(cid, 0.0) / max_bm25
        cos = cosine_scores.get(cid, 0.0)
        score = weight * cos + (1 - weight) * bm25_norm
        combined.append((score, c))
    combined.sort(key=lambda sc: -sc[0])
    return [{"label": c.label, "text": c.text, "score": s} for s, c in combined[:top_k]]


def format_results(results: list[dict]) -> str:
    """Explicitly frames what's returned as retrieved fragments from *earlier*
    in the campaign, not live narration — matching how SillyTavern's Chat
    Vectorization marks retrieved messages as "past events" to signal
    temporal discontinuity to the model. Without this, a raw excerpt handed
    back with no framing risks being read as if it were happening now,
    especially since it's arriving mid-turn as a tool result alongside
    otherwise-current context."""
    if not results:
        return "（沒有找到相關的舊記憶）"
    header = (
        "以下是從更早、已經被摺進摘要或裁切掉的原始對話裡搜出來的片段——"
        "這些是過去發生過的事，不是現在正在進行的場景，描述時不要跟當下的情境混在一起："
    )
    body = "\n\n".join(f"【{r['label']}】\n{r['text']}" for r in results)
    return f"{header}\n\n{body}"
