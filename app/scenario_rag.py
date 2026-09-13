"""Local, lexical (BM25-style) retrieval over a loaded scenario's text.

This is the third layer from the architecture sketch the user described
(Intent Parser -> Keeper Skill -> Deterministic Engine -> Map/Scene Engine ->
Scenario RAG -> LLM): instead of stuffing the whole scenario into the system
prompt (what app/keeper.py does by default, capped by MAX_SCENARIO_CHARS), a
loaded scenario can be indexed once and queried per-turn, returning only the
pages that actually match — see app/config.py's SCENARIO_RAG_ENABLED.

Deliberately NOT embeddings-based: this project has stayed cost/dependency
conscious throughout (prompt caching, a regex-only intent parser instead of
another LLM call, ...), and real semantic retrieval would mean a second paid
API (Anthropic has no embeddings endpoint; the usual choice is Voyage AI or
OpenAI) plus a vector store, for a bot whose scenarios have, in practice, all
fit comfortably under MAX_SCENARIO_CHARS so far. A from-scratch BM25 over
whitespace/CJK-bigram tokens costs nothing extra and is good enough to
retrieve "which pages mention this NPC/location/item" — see README for the
honest trade-off against full-context (it can miss a paraphrase that shares
no vocabulary with the query, and loses the "whole scenario visible at once"
property that lets the Keeper freely connect clues across pages on its own).
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

_PAGE_SPLIT_RE = re.compile(r"^--- 第 (\d+) 頁 ---$", re.MULTILINE)
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]+")

_K1 = 1.5  # BM25 term-frequency saturation
_B = 0.75  # BM25 length-normalization strength


@dataclass
class _Chunk:
    page: int
    text: str
    tokens: list[str] = field(default_factory=list)
    term_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class ScenarioIndex:
    chunks: list[_Chunk]
    doc_freq: dict[str, int]  # term -> number of chunks containing it
    avg_length: float
    text_hash: str


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


def build_index(scenario_text: str) -> ScenarioIndex:
    pages = split_pages(scenario_text) or [(1, scenario_text)]  # no page markers: one big chunk
    chunks = []
    doc_freq: dict[str, int] = {}
    total_length = 0
    for page_num, text in pages:
        tokens = _tokenize(text)
        term_counts: dict[str, int] = {}
        for t in tokens:
            term_counts[t] = term_counts.get(t, 0) + 1
        for t in term_counts:
            doc_freq[t] = doc_freq.get(t, 0) + 1
        total_length += len(tokens)
        chunks.append(_Chunk(page=page_num, text=text, tokens=tokens, term_counts=term_counts))

    avg_length = (total_length / len(chunks)) if chunks else 0.0
    text_hash = hashlib.md5(scenario_text.encode("utf-8")).hexdigest()
    return ScenarioIndex(chunks=chunks, doc_freq=doc_freq, avg_length=avg_length, text_hash=text_hash)


def _bm25_score(index: ScenarioIndex, query_tokens: list[str], chunk: _Chunk) -> float:
    n_docs = len(index.chunks)
    score = 0.0
    doc_len = len(chunk.tokens)
    for term in set(query_tokens):
        freq = chunk.term_counts.get(term, 0)
        if freq == 0:
            continue
        df = index.doc_freq.get(term, 0)
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
        denom = freq + _K1 * (1 - _B + _B * doc_len / (index.avg_length or 1))
        score += idf * (freq * (_K1 + 1)) / denom
    return score


def search(index: ScenarioIndex, query: str, top_k: int = 5) -> list[dict]:
    """Returns up to top_k {"page": int, "text": str, "score": float} results,
    highest-scoring first, for chunks with a positive score. An empty/no-match
    query returns an empty list rather than an arbitrary top_k — callers
    should treat that as "nothing found", not an error."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    scored = [(_bm25_score(index, query_tokens, c), c) for c in index.chunks]
    scored = [(s, c) for s, c in scored if s > 0]
    scored.sort(key=lambda sc: -sc[0])
    return [{"page": c.page, "text": c.text, "score": s} for s, c in scored[:top_k]]


def format_results(results: list[dict]) -> str:
    if not results:
        return "（沒有找到相關內容）"
    return "\n\n".join(f"--- 第 {r['page']} 頁 ---\n{r['text']}" for r in results)


# Rebuilding the index is pure CPU (tokenize + count), no LLM call, but a long
# scenario is still tens of thousands of tokens to re-tokenize on every single
# message — cache the last-built index per conversation, invalidated whenever
# the scenario text actually changes (new PDF upload / different content).
_index_cache: dict[str, ScenarioIndex] = {}


def get_index(group_id: str, scenario_text: str) -> ScenarioIndex:
    text_hash = hashlib.md5(scenario_text.encode("utf-8")).hexdigest()
    cached = _index_cache.get(group_id)
    if cached is not None and cached.text_hash == text_hash:
        return cached
    index = build_index(scenario_text)
    _index_cache[group_id] = index
    return index
