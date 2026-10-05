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

import asyncio
import hashlib
import json
import logging
import math
import multiprocessing
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app import (
    async_utils,
    db,
    embedding_cache,
    embedding_execution,
    observability,
    scenario_adjacency,
    scenario_projection,
)
from app.config import (
    EMBEDDING_REQUEST_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
    SCENARIO_RAG_ADJACENT_CHUNKS,
    SCENARIO_RAG_EMBEDDING_MODEL,
    SCENARIO_RAG_EMBEDDING_WEIGHT,
    SCENARIO_RAG_ENABLED,
    SCENARIO_RAG_PREWARM_ENABLED,
    SCENARIO_RAG_PREWARM_MAX_CONCURRENT,
)

_logger = logging.getLogger(__name__)


def _run_prewarm_index(group_id: str, scenario_text: str) -> None:
    try:
        get_index(group_id, scenario_text)
    except Exception:  # noqa: BLE001 - report failure via the child exit code.
        raise SystemExit(1) from None


class _PrewarmWorker:
    """One low-priority index build with a public, bounded process shutdown."""

    def __init__(self, group_id: str, scenario_text: str) -> None:
        self.process = multiprocessing.get_context("spawn").Process(
            target=_run_prewarm_index, args=(group_id, scenario_text)
        )
        self._start_stop_lock = threading.Lock()
        self._stopping = False
        self._closed = False
        self._exitcode: int | None = None

    @property
    def exitcode(self) -> int | None:
        return self._exitcode if self._closed else self.process.exitcode

    def _close_finished(self) -> None:
        with self._start_stop_lock:
            if not self._closed and self.process.exitcode is not None:
                self.process.join(timeout=0)
                self._exitcode = self.process.exitcode
                self.process.close()
                self._closed = True

    def start(self) -> bool:
        with self._start_stop_lock:
            if self._stopping:
                return False
            self.process.start()
            return True

    async def wait(self) -> None:
        while self.exitcode is None:
            await asyncio.sleep(0.02)
        await asyncio.to_thread(self._close_finished)
        if self.exitcode != 0:
            raise RuntimeError("prewarm worker exited unsuccessfully")

    def stop(self) -> bool:
        with self._start_stop_lock:
            self._stopping = True
            if self._closed:
                return True
            if self.process.pid is None:
                return True
            if self.process.exitcode is None:
                self.process.terminate()
                self.process.join(timeout=0.1)
                if self.process.exitcode is None:
                    self.process.kill()
                    self.process.join(timeout=0.5)
            else:
                self.process.join(timeout=0)
            if self.process.exitcode is not None:
                self._exitcode = self.process.exitcode
                self.process.close()
                self._closed = True
            return self._closed


@dataclass
class _PrewarmLoopState:
    semaphore: asyncio.Semaphore
    tasks: set[asyncio.Task]
    worker_tasks: set[asyncio.Future[Any]]
    workers: set[_PrewarmWorker] = field(default_factory=set)


_prewarm_states: dict[asyncio.AbstractEventLoop, _PrewarmLoopState] = {}


def _prewarm_state(loop: asyncio.AbstractEventLoop) -> _PrewarmLoopState:
    state = _prewarm_states.get(loop)
    if state is None:
        state = _PrewarmLoopState(
            semaphore=asyncio.Semaphore(min(SCENARIO_RAG_PREWARM_MAX_CONCURRENT, 1)),
            tasks=set(),
            worker_tasks=set(),
        )
        _prewarm_states[loop] = state
    return state


async def _prewarm_index(group_id: str, scenario_text: str) -> None:
    loop = asyncio.get_running_loop()
    state = _prewarm_state(loop)
    worker_task: asyncio.Future[Any] | None = None
    async with state.semaphore:
        # Yield once so a just-finished upload can send its confirmation before
        # the optional, low-priority embedding work begins.
        await asyncio.sleep(0)
        try:
            # Keep the child shielded from wrapper cancellation; shutdown owns
            # the grace period and, if necessary, terminates the child.
            worker = _PrewarmWorker(group_id, scenario_text)
            state.workers.add(worker)
            if not await asyncio.to_thread(worker.start):
                return
            worker_task = asyncio.create_task(worker.wait())
            state.worker_tasks.add(worker_task)
            def release_finished(done: asyncio.Future[Any]) -> None:
                state.worker_tasks.discard(done)
                if worker.exitcode is not None:
                    state.workers.discard(worker)

            worker_task.add_done_callback(release_finished)
            await asyncio.shield(worker_task)
            observability.event("rag.prewarm.completed", rag_kind="scenario", status="success")
        except asyncio.CancelledError:
            if worker_task is not None:
                async_utils.observe_background_task(worker_task, operation="rag.prewarm")
            observability.event("rag.prewarm.cancelled", level=logging.INFO, rag_kind="scenario")
            raise
        except Exception as exc:  # noqa: BLE001 - prewarm must never block scenario activation.
            observability.event(
                "rag.prewarm.failed", level=logging.WARNING, rag_kind="scenario",
                status="error", error_type=type(exc).__name__,
            )


def schedule_index_prewarm(group_id: str, scenario_text: str) -> asyncio.Task | None:
    """Schedule optional scenario index/embedding work after activation."""
    if not SCENARIO_RAG_ENABLED or not SCENARIO_RAG_PREWARM_ENABLED or not scenario_text.strip():
        return None
    state = _prewarm_state(asyncio.get_running_loop())
    task = asyncio.create_task(_prewarm_index(group_id, scenario_text))
    state.tasks.add(task)
    task.add_done_callback(state.tasks.discard)
    return task


async def shutdown_prewarm() -> None:
    """Cancel wrappers and stop child processes beyond the grace period."""
    loop = asyncio.get_running_loop()
    state = _prewarm_states.get(loop)
    if state is None:
        return
    pending: set[asyncio.Future[Any]] = set()
    try:
        tasks = tuple(state.tasks)
        for wrapper in tasks:
            if not wrapper.done():
                wrapper.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        workers = tuple(state.worker_tasks)
        if workers:
            try:
                done_workers, pending_workers = await asyncio.wait(
                    workers, timeout=PROVIDER_SHUTDOWN_GRACE_SECONDS
                )
            except asyncio.CancelledError:
                pending = {worker for worker in workers if not worker.done()}
                for worker in pending:
                    async_utils.observe_background_task(worker, operation="rag.prewarm")
                raise
            pending = pending_workers
            for worker in done_workers:
                async_utils.observe_background_task(worker, operation="rag.prewarm")
            if pending:
                observability.event(
                    "rag.prewarm.shutdown_degraded",
                    level=logging.ERROR,
                    rag_kind="scenario",
                    status="timeout",
                    timeout_ms=PROVIDER_SHUTDOWN_GRACE_SECONDS * 1000,
                )
                for worker in pending:
                    async_utils.observe_background_task(worker, operation="rag.prewarm")
    except asyncio.CancelledError:
        pending = {worker for worker in state.worker_tasks if not worker.done()}
        for worker in pending:
            async_utils.observe_background_task(worker, operation="rag.prewarm")
        raise
    finally:
        try:
            for child in tuple(state.workers):
                if not await asyncio.to_thread(child.stop):
                    observability.event(
                        "rag.prewarm.shutdown_degraded", level=logging.ERROR,
                        rag_kind="scenario", status="kill_timeout",
                    )
            if state.worker_tasks:
                for worker in tuple(state.worker_tasks):
                    if not worker.done():
                        worker.cancel()
                await asyncio.gather(*state.worker_tasks, return_exceptions=True)
        finally:
            _prewarm_states.pop(loop, None)


def _log_group_id(group_id: str) -> str:
    return hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:12]

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
    result_text: str = ""
    record_id: str = ""
    visibility: str = "public"
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
    index_cache: str = "rebuilt"
    record_store: dict[str, dict] | None = None


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


def _embed_texts(texts: list[str], *, rag_kind: str = "scenario") -> list[list[float]] | None:
    """Best-effort batched embedding; scenario ranking and fallback remain local."""
    return embedding_execution.embed_texts(
        texts, rag_kind=rag_kind, api_key=OPENAI_API_KEY,
        model=SCENARIO_RAG_EMBEDDING_MODEL, timeout=EMBEDDING_REQUEST_TIMEOUT_SECONDS,
        batch_size=_EMBEDDING_BATCH_SIZE,
    )


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
    embeddings = _embed_texts([c.text for c in chunks], rag_kind="scenario")
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


# Across one search, at most this many neighbours are attached in total, whatever the per-side setting.
_ADJACENT_TOTAL_MAX = 4


def _attach_adjacent(
    chunk: _Chunk, position: int, index: ScenarioIndex, usable: set[int],
    absorbed: set[int], query_tokens: list[str], budget: int,
) -> tuple[str, list[dict[str, Any]]]:
    """The hit's text with the neighbours it visibly needs, and a record of why each came along."""
    per_side = SCENARIO_RAG_ADJACENT_CHUNKS
    shared = set(chunk.term_counts) & set(query_tokens)
    before: list[str] = []
    after: list[str] = []
    notes: list[dict[str, Any]] = []
    cursor = chunk
    for step in range(1, per_side + 1):
        at = position + step
        if budget - len(notes) <= 0 or at >= len(index.chunks):
            break
        neighbour = index.chunks[at]
        if (id(neighbour) not in usable or id(neighbour) in absorbed or neighbour.record_id
                or neighbour.page - cursor.page > 1):
            break
        ahead = index.chunks[position - 1].text if cursor is chunk and position > 0 else ""
        reason = scenario_adjacency.reason_for_next(cursor.text, neighbour.text, shared, ahead)
        if not reason:
            break
        after.append(scenario_adjacency.new_text(cursor.text, neighbour.text))
        absorbed.add(id(neighbour))
        notes.append({"side": "next", "page": neighbour.page, "reason": reason})
        cursor = neighbour
    cursor = chunk
    for step in range(1, per_side + 1):
        at = position - step
        if budget - len(notes) <= 0 or at < 0:
            break
        neighbour = index.chunks[at]
        if (id(neighbour) not in usable or id(neighbour) in absorbed or neighbour.record_id
                or cursor.page - neighbour.page > 1):
            break
        reason = scenario_adjacency.reason_for_previous(neighbour.text, cursor.text, shared)
        if not reason:
            break
        before.insert(0, neighbour.text.strip())
        absorbed.add(id(neighbour))
        notes.append({"side": "previous", "page": neighbour.page, "reason": reason})
        cursor = neighbour
    own = chunk.result_text or chunk.text
    if before:
        own = scenario_adjacency.new_text(index.chunks[position - 1].text, own)
    return "\n\n".join([*before, own, *after]), notes


def _result_rows(scored: list[tuple[float, _Chunk]], top_k: int,
                 eligible: list[_Chunk] | None = None, *,
                 index: ScenarioIndex | None = None, query_tokens: list[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    used = 0
    omitted = 0
    candidates = eligible if eligible is not None else [c for _, c in scored]
    expand = (index is not None and bool(query_tokens) and SCENARIO_RAG_ADJACENT_CHUNKS > 0
              and index.record_store is None)
    position = {id(c): i for i, c in enumerate(index.chunks)} if expand and index else {}
    usable = {id(c) for c in candidates}
    absorbed: set[int] = set()
    adjacent_total = 0
    for score, chunk in scored:
        if len(rows) >= top_k:
            break
        if id(chunk) in absorbed:
            continue  # already travelling with the hit it continues
        identity = chunk.record_id or f"page:{chunk.page}:text:{chunk.text}"
        if identity in seen:
            continue
        seen.add(identity)
        absorbed.add(id(chunk))  # a chunk already emitted as a hit cannot also be attached to another
        siblings = [c for c in candidates if chunk.record_id and c.record_id == chunk.record_id] or [chunk]
        contents = list(dict.fromkeys(c.result_text or c.text for c in siblings))
        content = "\n\n".join(contents)
        if chunk.record_id and used + len(content) > scenario_projection.MAX_RESPONSE_CHARS:
            omitted += 1
            continue
        used += len(content) if chunk.record_id else 0
        row = {"page": chunk.page, "text": content, "score": score, "record_id": chunk.record_id}
        if expand and not chunk.record_id and id(chunk) in position and index is not None:
            text, notes = _attach_adjacent(
                chunk, position[id(chunk)], index, usable, absorbed, query_tokens or [],
                _ADJACENT_TOTAL_MAX - adjacent_total,
            )
            if notes:
                adjacent_total += len(notes)
                row.update(text=text, adjacent_chunks=notes)
        rows.append(row)
    if rows and omitted:
        rows[-1]["budget_omitted"] = omitted
    return rows


def _ranked_rows(index: ScenarioIndex, scored: list, top_k: int, eligible: list, query: str,
                 scopes: set[str] | None) -> list[dict]:
    if index.record_store is None:
        return _result_rows(scored, top_k, eligible, index=index, query_tokens=_tokenize(query))
    from app import scenario_retrieval
    roots = list(dict.fromkeys(c.record_id for _, c in scored))[:top_k]
    if not roots:
        return []
    return scenario_retrieval.project_ranked(index.record_store, roots, query, scopes)


def _note_results(metrics: dict[str, object], results: list[dict]) -> None:
    metrics["result_count"] = len(results)
    metrics["adjacent_chunk_count"] = sum(len(row.get("adjacent_chunks", ())) for row in results)


def search(
    index: ScenarioIndex,
    query: str,
    top_k: int = 5,
    *,
    metrics: dict[str, object] | None = None,
    allowed_visibility: set[str] | None = None,
) -> list[dict]:
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
        if metrics is not None:
            metrics["query_embedding_status"] = "empty"
            metrics["result_count"] = 0
        return []

    idf_cache = _idf_cache(index, query_tokens)
    eligible = [c for c in index.chunks
                if allowed_visibility is None or c.visibility in allowed_visibility]
    bm25_raw = {id(c): _bm25_score(index, query_tokens, c, idf_cache) for c in eligible}
    matched = [c for c in eligible if bm25_raw[id(c)] > 0]

    if not index.has_embeddings:
        if metrics is not None:
            metrics["query_embedding_status"] = "not_used"
        scored = sorted(((bm25_raw[id(c)], c) for c in matched), key=lambda sc: -sc[0])
        results = _ranked_rows(index, scored, top_k, eligible, query, allowed_visibility)
        if metrics is not None:
            _note_results(metrics, results)
        return results

    def _embed_query_once() -> list[float] | None:
        result = _embed_texts([query], rag_kind="scenario")
        return result[0] if result is not None else None

    query_vec = embedding_cache.get_query_embedding(SCENARIO_RAG_EMBEDDING_MODEL, query, _embed_query_once)
    if query_vec is None:
        # Embeddings worked at index time but the query-time call just failed
        # (transient error, key revoked mid-session, ...) — degrade to BM25
        # for this one search rather than returning nothing.
        if metrics is not None:
            metrics["query_embedding_status"] = "fallback"
        scored = sorted(((bm25_raw[id(c)], c) for c in matched), key=lambda sc: -sc[0])
        results = _ranked_rows(index, scored, top_k, eligible, query, allowed_visibility)
        if metrics is not None:
            _note_results(metrics, results)
        return results
    if metrics is not None:
        metrics["query_embedding_status"] = "success"
    query_norm = _vector_norm(query_vec)  # computed once, not once per chunk below

    max_bm25 = max(bm25_raw.values(), default=0.0) or 1.0
    weight = max(0.0, min(1.0, SCENARIO_RAG_EMBEDDING_WEIGHT))

    candidates = {id(c) for c in matched}  # a literal BM25 hit is always trusted, regardless of cosine
    cosine_scores: dict[int, float] = {}
    for c in eligible:
        if c.embedding is None:
            continue
        cos = _cosine_similarity(query_vec, query_norm, c.embedding, c.norm)
        cosine_scores[id(c)] = cos
        if cos >= _MIN_COSINE_RELEVANCE:
            candidates.add(id(c))

    by_id = {id(c): c for c in eligible}
    combined: list[tuple[float, _Chunk]] = []
    for cid in candidates:
        c = by_id[cid]
        bm25_norm = bm25_raw.get(cid, 0.0) / max_bm25
        cos = cosine_scores.get(cid, 0.0)
        score = weight * cos + (1 - weight) * bm25_norm
        combined.append((score, c))
    combined.sort(key=lambda sc: -sc[0])
    results = _ranked_rows(index, combined, top_k, eligible, query, allowed_visibility)
    if metrics is not None:
        _note_results(metrics, results)
    return results


def format_results(results: list[dict]) -> str:
    if not results:
        return "（沒有找到相關內容）"
    rendered = "\n\n".join(f"--- {'原稿補查 · ' if r.get('retrieval_source', '').startswith('original_') else ''}第 {r['page']} 頁 ---\n{r['text']}" for r in results)
    if any(r.get("retrieval_source", "").startswith("original_") for r in results):
        rendered = "【含目前允許章節的原稿補查結果；命中不保證裁決依據完整】\n" + rendered
    if any(r.get("original_supplement_missing") for r in results):
        rendered += "\n【原稿補查未命中】保留中文依據；缺少的事實仍未確認，不可視為不存在。"
    if any(r.get("budget_omitted") for r in results):
        rendered += "\n【檢索預算】部分完整記錄尚未回傳；若缺少裁決必要事實，請針對該事實補查，不可假設不存在。"
    metadata = [{k: v for k, v in row.items() if k not in {"text", "score", "page"} and not k.startswith("_")}
                for row in results if "complete_for_action" in row]
    if metadata:
        rendered += "\n【取用完整性】" + json.dumps(metadata, ensure_ascii=False)
    return rendered


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
            "chunks": [{"page": c.page, "text": c.text, "embedding": c.embedding,
                        "result_text": c.result_text, "record_id": c.record_id,
                        "visibility": c.visibility} for c in index.chunks],
        }
        db.set_json("scenario_indexes", group_id, payload)
    except Exception:
        _logger.exception(
            "failed to persist scenario index for group_id=%s (next restart will just re-embed)",
            _log_group_id(group_id),
        )


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
                result_text=c.get("result_text", ""), record_id=c.get("record_id", ""),
                visibility=c.get("visibility", "public"),
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
    except Exception:  # noqa: BLE001 - corrupt optional index cache triggers a rebuild.
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

# Building an index calls the embeddings API and then writes scenario_indexes,
# and both get_index and get_record_index run in worker threads via
# asyncio.to_thread. Without this, two concurrent misses for one key each pay
# the embeddings round trip and each write; the payload is identical so the
# last write is harmless, but the API cost doubles. A per-key build lock keeps
# the memory/disk fast path lock-free and serializes only the rebuild, with a
# second cache check inside the lock for the caller that waited.
_build_locks_guard = threading.Lock()
_build_locks: dict[str, threading.Lock] = {}


def _build_lock(cache_key: str) -> threading.Lock:
    with _build_locks_guard:
        lock = _build_locks.get(cache_key)
        if lock is None:
            lock = _build_locks[cache_key] = threading.Lock()
        return lock


def _cached_text_index(group_id: str, text_hash: str) -> ScenarioIndex | None:
    cached = _index_cache.get(group_id)
    if cached is not None and cached.text_hash == text_hash:
        cached.index_cache = "memory"
        return cached

    disk_index = _load_index_from_disk(group_id)
    if disk_index is not None and disk_index.text_hash == text_hash:
        disk_index.index_cache = "disk"
        _index_cache[group_id] = disk_index
        return disk_index
    return None


def get_index(group_id: str, scenario_text: str) -> ScenarioIndex:
    text_hash = hashlib.md5(scenario_text.encode("utf-8"), usedforsecurity=False).hexdigest()
    hit = _cached_text_index(group_id, text_hash)
    if hit is not None:
        return hit
    with _build_lock(group_id):
        # A concurrent caller may have finished the build while we waited.
        hit = _cached_text_index(group_id, text_hash)
        if hit is not None:
            return hit
        return _build_text_index(group_id, scenario_text)


def _build_text_index(group_id: str, scenario_text: str) -> ScenarioIndex:
    index_metrics = {"index_cache": "rebuilt"}
    started = time.perf_counter()
    with observability.span("rag.index", rag_kind="scenario", metrics=index_metrics):
        index = build_index(scenario_text)
    index.index_cache = "rebuilt"
    observability.event("rag.index_rebuilt", level=logging.WARNING, rag_kind="scenario",
                        duration_ms=(time.perf_counter() - started) * 1000,
                        chunk_count=len(index.chunks), reason="cache_miss")
    _index_cache[group_id] = index
    _save_index_to_disk(group_id, index)
    return index


def _cached_record_index(cache_key: str, text_hash: str, store: dict | None) -> ScenarioIndex | None:
    cached = _index_cache.get(cache_key)
    if cached is not None and cached.text_hash == text_hash:
        cached.record_store = store
        cached.index_cache = "memory"
        return cached
    disk = _load_index_from_disk(cache_key)
    if disk is not None and disk.text_hash == text_hash:
        disk.record_store = store
        disk.index_cache = "disk"
        _index_cache[cache_key] = disk
        return disk
    return None


def get_record_index(cache_key: str, records: list[dict]) -> ScenarioIndex:
    """Index validated template records once per immutable variant/window."""
    v4 = bool(records) and all(r.get("schema_version") == 4 for r in records)
    store = {r["id"]: r for r in records} if v4 else None
    serialized = json.dumps(records, ensure_ascii=False, sort_keys=True)
    text_hash = hashlib.md5(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()
    hit = _cached_record_index(cache_key, text_hash, store)
    if hit is not None:
        return hit
    with _build_lock(cache_key):
        # A concurrent caller may have finished the build while we waited.
        hit = _cached_record_index(cache_key, text_hash, store)
        if hit is not None:
            return hit
        return _build_record_index(cache_key, records, text_hash, store, v4)


def _build_record_index(cache_key: str, records: list[dict], text_hash: str,
                        store: dict | None, v4: bool) -> ScenarioIndex:
    chunks: list[_Chunk] = []
    projections = {} if v4 else scenario_projection.bundles(records)
    for record in records:
        prefix = " ".join([record["name"], *record["aliases"], *record["keywords"]])
        for scope in ("public", "kp_only"):
            content = scenario_projection.body(record, scope) if v4 else projections[record["id"]][scope]
            if not content:
                continue
            own = scenario_projection.body(record, scope)
            # A link-only scope still needs a sibling so its complete evidence
            # reaches the internal result when another scope matches.
            searchable = ((prefix + "\n" + own) if v4 else own) or record["name"]
            for start in range(0, len(searchable), 500):
                chunks.append(_Chunk(
                    page=int(record["page"]), text=f"{record['name'] if v4 else prefix} {searchable[start:start + 500]}",
                    result_text="" if v4 else content, record_id=record["id"], visibility=scope,
                ))
    stats, average = _compute_bm25_stats(chunks)
    embeddings = _embed_texts([c.text for c in chunks], rag_kind="scenario")
    if embeddings is not None:
        for chunk, vector in zip(chunks, embeddings, strict=True):
            chunk.embedding, chunk.norm = vector, _vector_norm(vector)
    index = ScenarioIndex(chunks=chunks, doc_freq=stats, avg_length=average,
                          text_hash=text_hash, has_embeddings=embeddings is not None, record_store=store)
    _index_cache[cache_key] = index
    _save_index_to_disk(cache_key, index)
    return index
