"""Retrieval over a loaded scenario's text — BM25 always on, optionally
blended with OpenAI embeddings for semantic matching.

This is the third layer from the architecture sketch the user described
(Intent Parser -> Keeper Skill -> Deterministic Engine -> Map/Scene Engine ->
Scenario RAG -> LLM): instead of stuffing the whole scenario into the system
prompt (what app/keeper.py does by default, capped by MAX_SCENARIO_CHARS), a
loaded scenario can be indexed once and queried per-turn, returning only the
pages that actually match — see app/config.py's SCENARIO_RAG_ENABLED.

BM25 over whitespace/CJK-bigram tokens is the baseline: zero extra cost, zero
extra dependency, works for anyone regardless of which LLM_PROVIDER or keys
they have configured. It's good at "which pages mention this NPC/location/
item" but — being purely lexical — misses a paraphrase that shares no
vocabulary with the query (a query for "地下室" won't match a page that only
says "地窖" or "陰暗的樓下空間").

Embeddings close that gap when OPENAI_API_KEY is available (this project
already needs OpenAI for the Keeper/vision paths once LLM_PROVIDER=openai —
reusing that key here means no new provider/dependency, just an additional
API call): each chunk gets embedded once at index-build time, each query
gets embedded once at search time, and the two scores are combined into one
ranking (see search() and SCENARIO_RAG_EMBEDDING_WEIGHT). No OPENAI_API_KEY
still works fine — build_index simply leaves embeddings unset and search()
falls back to pure BM25, exactly like before embeddings existed here.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass, field

from app import db
from app.config import OPENAI_API_KEY, SCENARIO_RAG_EMBEDDING_MODEL, SCENARIO_RAG_EMBEDDING_WEIGHT

_logger = logging.getLogger(__name__)

_PAGE_SPLIT_RE = re.compile(r"^--- 第 (\d+) 頁 ---$", re.MULTILINE)
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]+")

_K1 = 1.5  # BM25 term-frequency saturation
_B = 0.75  # BM25 length-normalization strength

# Minimum RAW cosine similarity (not the blended/normalized score used for
# ranking below) for a chunk to be considered a candidate purely on semantic
# grounds — see search()'s own docstring for why this has to gate on the raw
# value specifically. Calibrated against this project's real 68-chunk
# production scenario: a query for actual scenario content ("lighthouse
# keeper") scored 0.43-0.48 on its best-matching chunks; a genuine paraphrase
# sharing no literal vocabulary ("the elderly man who used to maintain the
# tower and its light") scored 0.33-0.35; a deliberately unrelated nonsense
# query topped out at 0.31. 0.32 sits just above that nonsense ceiling while
# still keeping most of the paraphrase's matches.
_MIN_COSINE_RELEVANCE = 0.32


@dataclass
class _Chunk:
    page: int
    text: str
    tokens: list[str] = field(default_factory=list)
    term_counts: dict[str, int] = field(default_factory=dict)
    embedding: list[float] | None = None
    # Cached |embedding| — computed once (build_index / _load_index_from_disk),
    # not recomputed per search/per comparison. See _cosine_similarity.
    norm: float = 0.0


@dataclass
class ScenarioIndex:
    chunks: list[_Chunk]
    doc_freq: dict[str, int]  # term -> number of chunks containing it
    avg_length: float
    text_hash: str
    has_embeddings: bool = False


def _tokenize(text: str) -> list[str]:
    """ASCII text tokenizes into lowercased words; CJK runs tokenize into
    overlapping bigrams (single Chinese characters are too common to be
    useful terms on their own, and there's no word-segmentation dependency
    in this project to do better) — e.g. "劇本內容" -> ["劇本", "本內", "內容"]."""
    tokens = [w.lower() for w in _ASCII_WORD_RE.findall(text)]
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def split_pages(scenario_text: str) -> list[tuple[int, str]]:
    """Splits app/pdf_loader.py's "--- 第 N 頁 ---" -delimited scenario text
    back into (page_number, page_text) chunks. Text that appears before the
    first marker (shouldn't normally happen) is dropped rather than mis-
    numbered."""
    pieces = _PAGE_SPLIT_RE.split(scenario_text)
    # re.split with a capturing group yields [pre, num1, text1, num2, text2, ...]
    pages = []
    for i in range(1, len(pieces), 2):
        page_num = int(pieces[i])
        text = pieces[i + 1].strip() if i + 1 < len(pieces) else ""
        if text:
            pages.append((page_num, text))
    return pages


_CHUNK_TARGET_CHARS = 400  # rough target size per sub-page chunk
_CHUNK_OVERLAP_RATIO = 0.15  # ~15% of target size carried over into the next chunk
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def _split_page_into_chunks(page_num: int, text: str) -> list[tuple[int, str]]:
    """Splits one page's text into paragraph-sized chunks (~_CHUNK_TARGET_CHARS
    each) instead of indexing the whole page as one unit — a page mixing
    several unrelated topics (a room description followed by an unrelated
    NPC's stat block, say) used to dilute a query's match against whichever
    part it was actually about. Adjacent short paragraphs are merged up to
    the target size (so this doesn't over-fragment into single-sentence
    chunks that lose surrounding context); a page with no blank-line breaks
    at all (one long unbroken block) still comes back as a single chunk —
    finer-grained than a whole page, never worse. Every chunk keeps the same
    page_num as its source page, so multiple search results can point back
    to the same page (that's expected, not a bug) and callers that only care
    about "which page" (e.g. /coc showpage) still work unchanged.

    Adjacent chunks share a small trailing/leading overlap (the last
    _CHUNK_OVERLAP_RATIO of the previous chunk's characters, carried into the
    start of the next) — without it, a point made right at a chunk boundary
    (a sentence whose context is split across the cut) could end up only
    partially represented in either chunk, with neither one scoring well
    against a query about it."""
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    if not paragraphs:
        return []
    overlap_chars = int(_CHUNK_TARGET_CHARS * _CHUNK_OVERLAP_RATIO)
    result: list[tuple[int, str]] = []
    buffer = ""
    for para in paragraphs:
        if buffer and len(buffer) + len(para) > _CHUNK_TARGET_CHARS:
            result.append((page_num, buffer))
            carry = buffer[-overlap_chars:] if overlap_chars else ""
            buffer = f"{carry}\n\n{para}" if carry else para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        result.append((page_num, buffer))
    return result


_EMBEDDING_BATCH_SIZE = 100  # conservative — well under OpenAI's per-request
# input-count limit (documented up to ~2048), and keeps any one request's
# total token count comfortable regardless of how long individual chunks
# are. A longer scenario (or the finer per-paragraph chunking above, which
# produces more chunks than one-per-page did) now spans multiple requests
# instead of risking one oversized request failing outright.


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Best-effort: embed texts via OpenAI, batching requests so a large
    input list (a long scenario, or many fine-grained chunks) can't exceed
    a single request's limits. Returns None if unavailable (no
    OPENAI_API_KEY) or any batch's call fails for any reason — callers
    should fall back to pure BM25 in that case, not treat it as an error.
    Order matches the input list regardless of what order each batch's
    response returns embeddings in (each Embedding carries its own .index,
    relative to its own batch)."""
    if not OPENAI_API_KEY or not texts:
        return None
    try:
        import openai

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        ordered: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + _EMBEDDING_BATCH_SIZE]
            response = client.embeddings.create(model=SCENARIO_RAG_EMBEDDING_MODEL, input=batch)
            for item in response.data:
                ordered[start + item.index] = item.embedding
        if any(v is None for v in ordered):
            return None
        return ordered
    except Exception:
        return None


def _vector_norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _cosine_similarity(a: list[float], norm_a: float, b: list[float], norm_b: float) -> float:
    """Exact cosine similarity, dot(a,b) / (|a|*|b|) — norms are passed in
    pre-computed rather than recomputed here.

    An earlier version of this function skipped normalization entirely,
    assuming OpenAI's embeddings are unit vectors (measured at the time as
    |v| within ~3e-4 of 1.0 for the configured model). That was reverted:
    even that small a deviation isn't negligible here, because the result
    feeds a hard threshold (_MIN_COSINE_RELEVANCE, calibrated in raw cosine
    units) and a full ranking sort — a chunk whose true cosine sits within
    ~1e-3 of the threshold, or within ~1e-3 of another chunk's score, could
    have the gate or the ordering flip purely from which side of 1.0 each
    vector's actual norm happened to land on. That's a real, not
    theoretical, correctness risk given how close real production queries
    scored to the threshold (see _MIN_COSINE_RELEVANCE's own comment).

    What's still worth keeping from that version: recomputing norm_a (the
    query vector's norm) and norm_b (a chunk's norm) from scratch on every
    single comparison was the actual waste — norm_a is identical across all
    O(chunks) comparisons in one search, and norm_b never changes once an
    embedding is computed. So both are computed ONCE (see build_index,
    _load_index_from_disk, and search below) and cached/passed in here,
    instead of being recomputed per comparison — this keeps ~the same
    speedup as skipping normalization, without the approximation, and
    without depending on the embedding source producing unit vectors at
    all."""
    denom = norm_a * norm_b
    if denom == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denom


def _compute_bm25_stats(chunks: list[_Chunk]) -> tuple[dict[str, int], float]:
    """Fills in each chunk's tokens/term_counts from its text and returns the
    index-level doc_freq/avg_length derived from them. Pure CPU (tokenize +
    count), cheap enough to redo whenever chunk text is available — including
    right after loading a persisted index from disk, so the disk format only
    needs to store text + embedding, not these derived fields."""
    doc_freq: dict[str, int] = {}
    total_length = 0
    for chunk in chunks:
        tokens = _tokenize(chunk.text)
        term_counts: dict[str, int] = {}
        for t in tokens:
            term_counts[t] = term_counts.get(t, 0) + 1
        chunk.tokens = tokens
        chunk.term_counts = term_counts
        for t in term_counts:
            doc_freq[t] = doc_freq.get(t, 0) + 1
        total_length += len(tokens)
    avg_length = (total_length / len(chunks)) if chunks else 0.0
    return doc_freq, avg_length


def build_index(scenario_text: str) -> ScenarioIndex:
    pages = split_pages(scenario_text) or [(1, scenario_text)]  # no page markers: one big chunk
    sub_chunks: list[tuple[int, str]] = []
    for page_num, text in pages:
        sub_chunks.extend(_split_page_into_chunks(page_num, text) or [(page_num, text)])

    chunks = [_Chunk(page=page_num, text=text) for page_num, text in sub_chunks]
    doc_freq, avg_length = _compute_bm25_stats(chunks)
    text_hash = hashlib.md5(scenario_text.encode("utf-8"), usedforsecurity=False).hexdigest()

    # Embeddings call(s) for the whole scenario, done once at index-build
    # time (cached by get_index below, and persisted to disk so a bot
    # restart doesn't pay for this again) rather than per search — batched
    # internally by _embed_texts now that a scenario can produce many more
    # (smaller) chunks than one-per-page did.
    embeddings = _embed_texts([c.text for c in chunks])
    has_embeddings = embeddings is not None
    if embeddings is not None:
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb
            chunk.norm = _vector_norm(emb)

    return ScenarioIndex(
        chunks=chunks, doc_freq=doc_freq, avg_length=avg_length, text_hash=text_hash, has_embeddings=has_embeddings
    )


def _idf_cache(index: ScenarioIndex, query_tokens: list[str]) -> dict[str, float]:
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


def _bm25_score(index: ScenarioIndex, query_tokens: list[str], chunk: _Chunk, idf_cache: dict[str, float]) -> float:
    score = 0.0
    doc_len = len(chunk.tokens)
    for term in set(query_tokens):
        freq = chunk.term_counts.get(term, 0)
        if freq == 0:
            continue
        denom = freq + _K1 * (1 - _B + _B * doc_len / (index.avg_length or 1))
        score += idf_cache[term] * (freq * (_K1 + 1)) / denom
    return score


def search(index: ScenarioIndex, query: str, top_k: int = 5) -> list[dict]:
    """Returns up to top_k {"page": int, "text": str, "score": float} results,
    highest-scoring first. An empty/no-match query returns an empty list
    rather than an arbitrary top_k — callers should treat that as "nothing
    found", not an error.

    Pure BM25 when the index has no embeddings (no OPENAI_API_KEY at index-
    build time, or the embeddings call failed). Otherwise hybrid: BM25 scores
    are min-max normalized to 0-1 across the chunks that matched at least one
    query token, cosine similarity (already roughly 0-1) is computed against
    every chunk regardless of lexical overlap — this is exactly what lets a
    query surface a page that uses different wording for the same thing —
    and the two are combined via SCENARIO_RAG_EMBEDDING_WEIGHT. A chunk only
    needs to score on *either* signal to be a candidate, not both.

    A chunk with no literal keyword overlap (no BM25 hit) still needs its
    RAW cosine similarity to clear _MIN_COSINE_RELEVANCE to be considered at
    all — confirmed by testing against this project's real production
    scenario that the *blended/normalized* score doesn't work as a relevance
    gate: BM25's min-max normalization is relative to each query's own best
    match, so an entirely unrelated query's best (still bad) match gets
    normalized up to a deceptively high value, occasionally scoring higher
    than a genuinely relevant but harder query. Raw cosine similarity alone
    turned out to separate cleanly instead, which is why the gate uses that
    signal specifically rather than the combined ranking score. A chunk that
    already has a literal BM25 hit is never excluded by this gate — a real
    keyword match is trusted regardless of what the embedding model thinks."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    idf_cache = _idf_cache(index, query_tokens)
    bm25_raw = {id(c): _bm25_score(index, query_tokens, c, idf_cache) for c in index.chunks}
    matched = [c for c in index.chunks if bm25_raw[id(c)] > 0]

    if not index.has_embeddings:
        scored = sorted(((bm25_raw[id(c)], c) for c in matched), key=lambda sc: -sc[0])
        return [{"page": c.page, "text": c.text, "score": s} for s, c in scored[:top_k]]

    query_embedding = _embed_texts([query])
    if query_embedding is None:
        # Embeddings worked at index time but the query-time call just failed
        # (transient error, key revoked mid-session, ...) — degrade to BM25
        # for this one search rather than returning nothing.
        scored = sorted(((bm25_raw[id(c)], c) for c in matched), key=lambda sc: -sc[0])
        return [{"page": c.page, "text": c.text, "score": s} for s, c in scored[:top_k]]
    query_vec = query_embedding[0]
    query_norm = _vector_norm(query_vec)  # computed once, not once per chunk below

    max_bm25 = max(bm25_raw.values(), default=0.0) or 1.0
    weight = max(0.0, min(1.0, SCENARIO_RAG_EMBEDDING_WEIGHT))

    candidates = {id(c) for c in matched}  # a literal BM25 hit is always trusted, regardless of cosine
    cosine_scores: dict[int, float] = {}
    for c in index.chunks:
        if c.embedding is None:
            continue
        cos = _cosine_similarity(query_vec, query_norm, c.embedding, c.norm)
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
    return [{"page": c.page, "text": c.text, "score": s} for s, c in combined[:top_k]]


def format_results(results: list[dict]) -> str:
    if not results:
        return "（沒有找到相關內容）"
    return "\n\n".join(f"--- 第 {r['page']} 頁 ---\n{r['text']}" for r in results)


def _save_index_to_disk(group_id: str, index: ScenarioIndex) -> None:
    """Best-effort: a failed write just means the next restart re-embeds from
    scratch (same as before this existed), not a functional error. Persisted
    via app/db.py (SQLite) rather than a standalone data/groups/*.json file —
    same "one JSON blob per key" shape as before, just a different storage
    backend."""
    try:
        payload = {
            "text_hash": index.text_hash,
            "has_embeddings": index.has_embeddings,
            "chunks": [{"page": c.page, "text": c.text, "embedding": c.embedding} for c in index.chunks],
        }
        db.set_json("scenario_indexes", group_id, payload)
    except Exception:
        _logger.exception("failed to persist scenario index for group_id=%s (next restart will just re-embed)", group_id)


def _load_index_from_disk(group_id: str) -> ScenarioIndex | None:
    """Returns None on anything unexpected (no row yet, corrupt/old format)
    so callers fall back to a normal rebuild rather than crashing."""
    try:
        data = db.get_json("scenario_indexes", group_id)
        if data is None:
            return None
        chunks = []
        for c in data["chunks"]:
            embedding = c.get("embedding")
            chunks.append(_Chunk(
                page=c["page"], text=c["text"], embedding=embedding,
                # Recomputed on load rather than persisted: cheap (O(chunks),
                # once per bot restart) and avoids needing a schema migration
                # for indexes saved before `norm` existed on this dataclass.
                norm=_vector_norm(embedding) if embedding is not None else 0.0,
            ))
        doc_freq, avg_length = _compute_bm25_stats(chunks)
        return ScenarioIndex(
            chunks=chunks,
            doc_freq=doc_freq,
            avg_length=avg_length,
            text_hash=data["text_hash"],
            has_embeddings=data.get("has_embeddings", False),
        )
    except Exception:
        return None


# Rebuilding the BM25 stats is pure CPU (tokenize + count), no LLM call, but a
# long scenario is still tens of thousands of tokens to re-tokenize on every
# single message — cache the last-built index per conversation in memory,
# invalidated whenever the scenario text actually changes (new PDF upload /
# different content). On top of the in-memory cache, the built index (chunk
# text + embeddings) is also persisted to disk per group, so a bot restart
# doesn't have to pay for OpenAI embeddings calls again for a scenario it has
# already indexed before — only tokenize/count is redone (cheap) after a disk
# load, not the embeddings call.
_index_cache: dict[str, ScenarioIndex] = {}


def get_index(group_id: str, scenario_text: str) -> ScenarioIndex:
    text_hash = hashlib.md5(scenario_text.encode("utf-8"), usedforsecurity=False).hexdigest()
    cached = _index_cache.get(group_id)
    if cached is not None and cached.text_hash == text_hash:
        return cached

    disk_index = _load_index_from_disk(group_id)
    if disk_index is not None and disk_index.text_hash == text_hash:
        _index_cache[group_id] = disk_index
        return disk_index

    index = build_index(scenario_text)
    _index_cache[group_id] = index
    _save_index_to_disk(group_id, index)
    return index
