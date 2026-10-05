"""A memory chunk is bounded before it is embedded, a failed embedding is diagnosed and retried a bounded number
of times, and lexical-only memory is never the silent steady state."""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import patch

import pytest

from app import (
    db,
    embedding_execution,
    keeper,
    memory_chunking,
    memory_rag,
    observability,
)
from app.models import GroupState
from app.repositories import group_state

LIMIT = 200  # tokens in these tests; measure_tokens is len, so also characters


@pytest.fixture(autouse=True)
def bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    monkeypatch.setattr(memory_rag, "measure_tokens", len)
    monkeypatch.setattr(memory_rag, "MEMORY_EMBEDDING_MAX_TOKENS", LIMIT)
    memory_rag._index_cache.clear()


def messages(count: int, size: int = 90) -> list[dict[str, str]]:
    return [{"role": "user" if n % 2 else "assistant", "content": f"事件{n}：" + "字" * size} for n in range(count)]


def sources(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"record_kind": "narration", "authority": "mixed", "turn_id": f"turn-{n}", "timeline_id": "timeline-a"}
            for n, _ in enumerate(rows)]


def vectors(texts: list[str], rag_kind: str = "memory") -> list[list[float]]:
    return [[1.0, float(len(text) % 7 + 1)] for text in texts]


# --- bounding ------------------------------------------------------------------------------

def test_messages_are_packed_in_order_into_parts_that_fit() -> None:
    rows = messages(8)
    parts = memory_chunking.pack(rows, sources(rows), max_tokens=LIMIT, measure=len)
    assert len(parts) > 1 and all(len(part.text) <= LIMIT for part in parts)
    assert "\n".join(part.text for part in parts) == "\n".join(f"{m['role']}: {m['content']}" for m in rows)


def test_every_part_keeps_the_provenance_of_its_own_messages() -> None:
    rows = messages(6)
    parts = memory_chunking.pack(rows, sources(rows), max_tokens=LIMIT, measure=len)
    assert [src["turn_id"] for part in parts for src in part.source_messages] == [f"turn-{n}" for n in range(6)]


def test_a_trim_that_fits_stays_one_part() -> None:
    rows = messages(2, size=20)
    [part] = memory_chunking.pack(rows, sources(rows), max_tokens=LIMIT, measure=len)
    assert part.text == "\n".join(f"{m['role']}: {m['content']}" for m in rows)


def test_one_message_longer_than_a_part_is_cut_at_sentence_ends_and_loses_nothing() -> None:
    long_message = [{"role": "assistant", "content": "。".join(f"第{n}句" + "字" * 40 for n in range(12)) + "。"}]
    parts = memory_chunking.pack(long_message, sources(long_message), max_tokens=LIMIT, measure=len)
    assert len(parts) > 1 and all(len(part.text) <= LIMIT for part in parts)
    assert "".join(part.text for part in parts) == f"assistant: {long_message[0]['content']}"
    assert all(part.source_messages == (sources(long_message)[0],) for part in parts)


def test_text_with_no_boundary_at_all_is_cut_by_measured_length() -> None:
    pieces = memory_chunking.split_text("字" * 1000, LIMIT, len)
    assert "".join(pieces) == "字" * 1000 and all(len(piece) <= LIMIT for piece in pieces)


def test_the_limit_is_measured_in_tokens_not_characters() -> None:
    def two_per_character(text: str) -> int:
        return len(text) * 2

    pieces = memory_chunking.split_text("字" * 300, LIMIT, two_per_character)
    assert all(len(piece) * 2 <= LIMIT for piece in pieces) and len(pieces) >= 3


def test_nothing_longer_than_the_limit_is_ever_sent_for_embedding() -> None:
    rows = messages(30)
    sent: list[list[str]] = []

    def record(texts: list[str], rag_kind: str = "memory") -> list[list[float]]:
        sent.append(list(texts))
        return vectors(texts)

    with patch.object(memory_rag, "_embed_texts", record):
        prepared = memory_rag.prepare_memory(rows, sources(rows))
    assert len(prepared.parts) > 1 and all(len(text) <= LIMIT for batch in sent for text in batch)
    assert len(prepared.embeddings) == len(prepared.parts) and all(vector is not None for vector in prepared.embeddings)


# --- storing and finding the parts ------------------------------------------------------------

def commit(group_id: str, rows: list[dict[str, str]], prepared: memory_rag.PreparedMemory, key: str = "k1") -> str:
    state = GroupState(group_id, timeline_id="timeline-a", log=list(rows))
    group_state.save_state(state)
    stored = group_state.load_state(group_id)
    return keeper._persist_memory_maintenance_state(
        group_id, "摘要", rows, timeline_id="timeline-a", base_summary=stored.campaign_summary,
        source_revision=stored.state_revision, idempotency_key=key, embedding=None, prepared=prepared,
    )


def test_an_oversized_trim_is_stored_as_traceable_parts_of_one_parent() -> None:
    rows = messages(12)
    with patch.object(memory_rag, "_embed_texts", vectors):
        prepared = memory_rag.prepare_memory(rows, sources(rows))
    assert commit("g1", rows, prepared) == "committed"
    chunks = db.get_json("memory_chunks", "g1")
    assert len(chunks) == len(prepared.parts) > 1
    assert {c["parent_id"] for c in chunks} == {"k1"} and [c["part_index"] for c in chunks] == list(range(1, len(chunks) + 1))
    assert {c["part_count"] for c in chunks} == {len(chunks)} and {c["timeline_id"] for c in chunks} == {"timeline-a"}
    assert all(c["embedding"] is not None and len(c["text"]) <= LIMIT for c in chunks)
    assert [s["turn_id"] for c in chunks for s in c["source_messages"]] == [f"turn-{n}" for n in range(12)]


def test_replaying_the_commit_does_not_store_the_parts_again() -> None:
    rows = messages(12)
    with patch.object(memory_rag, "_embed_texts", vectors):
        prepared = memory_rag.prepare_memory(rows, sources(rows))
    appended = []
    for _ in range(2):
        with db.transaction() as conn:
            appended.append(memory_rag.append_memory_parts_tx(
                conn, "g2", prepared, timeline_id="timeline-a", idempotency_key="k1", source_revision=1))
    assert appended == [True, False]
    assert len(db.get_json("memory_chunks", "g2")) == len(prepared.parts)


def test_a_trim_that_fits_is_stored_exactly_as_before() -> None:
    rows = messages(2, size=10)
    with patch.object(memory_rag, "_embed_texts", vectors):
        prepared = memory_rag.prepare_memory(rows, sources(rows))
    commit("g3", rows, prepared)
    [chunk] = db.get_json("memory_chunks", "g3")
    assert "parent_id" not in chunk and chunk["idempotency_key"] == "k1" and chunk["embedding"] is not None


def test_the_parts_of_one_memory_come_back_as_one_result_in_order() -> None:
    rows = messages(12)
    with patch.object(memory_rag, "_embed_texts", vectors):
        prepared = memory_rag.prepare_memory(rows, sources(rows))
        commit("g4", rows, prepared)
        results = memory_rag.search_memory("g4", "字", top_k=5, timeline_id="timeline-a")
    assert len(results) == 1 and results[0]["parent_id"] == "k1"
    assert results[0]["parts"] == sorted(results[0]["parts"]) and len(results[0]["parts"]) > 1
    assert results[0]["text"].index("事件0") < results[0]["text"].index("事件11")


# --- failing safely and recovering ---------------------------------------------------------------

class ProviderError(Exception):
    def __init__(self, status: int, code: str = "", kind: str = "") -> None:
        super().__init__("secret sk-live-123 rejected: Incorrect API key provided")
        self.status_code, self.code, self.type = status, code, kind


def failing_openai(error: Exception):
    def create(**_kwargs: Any) -> Any:
        raise error
    client = types.SimpleNamespace(embeddings=types.SimpleNamespace(create=create))
    return types.SimpleNamespace(OpenAI=lambda **_kw: client)


@pytest.mark.parametrize(("status", "code", "retryable", "status_class"), [
    (400, "invalid_request_error", False, "4xx"), (401, "invalid_api_key", False, "4xx"),
    (429, "rate_limit_exceeded", True, "4xx"), (429, "insufficient_quota", False, "4xx"),
    (500, "", True, "5xx"), (503, "", True, "5xx"),
])
def test_a_provider_failure_is_diagnosed_without_its_message(status, code, retryable, status_class) -> None:
    error = ProviderError(status, code, "invalid_request_error")
    with patch.dict(sys.modules, {"openai": failing_openai(error)}), \
            patch.object(observability, "event") as event:
        assert embedding_execution.embed_texts(["一", "二"], rag_kind="memory", api_key="k", model="m", timeout=1) is None
    failure = embedding_execution.take_failure()
    assert failure is not None and (failure.retryable, failure.status_class, failure.status_code) == (retryable, status_class, status)
    fields = event.call_args.kwargs
    assert fields["provider"] == "openai" and fields["operation"] == "embeddings.create"
    assert fields["input_count"] == 2 and fields["largest_input_bytes"] == 3 and fields["retryable"] is retryable
    assert fields["provider_error_code"] == (code or None) and fields["fallback"] == "bm25"
    assert "sk-live-123" not in repr(event.call_args) and "Incorrect API key" not in repr(event.call_args)
    assert embedding_execution.take_failure() is None  # taken once


def test_a_timeout_and_a_dropped_connection_are_retryable() -> None:
    class APITimeoutError(Exception): ...
    class APIConnectionError(Exception): ...

    for error, status_class in ((APITimeoutError("t"), "timeout"), (APIConnectionError("c"), "connection")):
        failure = embedding_execution.classify(error)
        assert (failure.retryable, failure.status_class) == (True, status_class)


def stored_without_vector(group_id: str, count: int = 1, text_size: int = 60) -> list[dict]:
    rows = [{"label": f"記憶片段 #{n + 1}", "chunk_id": f"c{n}", "idempotency_key": f"c{n}", "timeline_id": "timeline-a",
             "source_revision": 1, "text": f"事件{n}：" + "字" * text_size, "embedding": None,
             "source_messages": [{"turn_id": f"turn-{n}"}]} for n in range(count)]
    db.set_json("memory_chunks", group_id, rows)
    return rows


def test_a_chunk_stored_without_a_vector_gets_one_after_a_later_success() -> None:
    stored_without_vector("g5", count=2)
    with patch.object(memory_rag, "_embed_texts", vectors):
        stats = memory_rag.backfill_embeddings("g5")
        results = memory_rag.search_memory("g5", "事件0", timeline_id="timeline-a")
    assert stats["embedded"] == 2 and all(c["embedding"] is not None for c in db.get_json("memory_chunks", "g5"))
    assert results and memory_rag._get_index("g5", db.get_json("memory_chunks", "g5"), "timeline-a").has_embeddings


def test_memory_stays_searchable_by_keyword_while_embeddings_are_down() -> None:
    stored_without_vector("g6", count=2)
    metrics: dict[str, Any] = {}
    with patch.object(observability, "event") as event:
        results = memory_rag.search_memory("g6", "事件1", timeline_id="timeline-a", metrics=metrics)
    assert results and "事件1" in results[0]["text"] and metrics["chunks_without_embedding"] == 2
    gap = [c for c in event.call_args_list if c.args[0] == "memory.embedding_gap"]
    assert gap and gap[0].kwargs["chunks_without_embedding"] == 2


def test_a_non_retryable_refusal_is_not_tried_again() -> None:
    stored_without_vector("g7")
    calls = []

    def refuse(texts: list[str], rag_kind: str = "memory") -> None:
        calls.append(texts)
        embedding_execution._local.failure = embedding_execution.classify(ProviderError(400, "invalid_request_error"))

    with patch.object(memory_rag, "_embed_texts", refuse):
        first = memory_rag.backfill_embeddings("g7")
        second = memory_rag.backfill_embeddings("g7")
        third = memory_rag.backfill_embeddings("g7")
    assert len(calls) == 1 and first["gave_up"] == 1 and second["examined"] == third["examined"] == 0
    [chunk] = db.get_json("memory_chunks", "g7")
    assert chunk["embedding_status"] == "failed_permanent" and chunk["embedding_failure"]["retryable"] is False
    assert chunk["text"].startswith("事件0")  # still there, still searchable


def test_a_retryable_failure_is_tried_a_bounded_number_of_times() -> None:
    stored_without_vector("g8")
    calls = []

    def flaky(texts: list[str], rag_kind: str = "memory") -> None:
        calls.append(texts)
        embedding_execution._local.failure = embedding_execution.classify(ProviderError(503))

    with patch.object(memory_rag, "_embed_texts", flaky):
        for _ in range(8):
            memory_rag.backfill_embeddings("g8")
    assert len(calls) == memory_rag.MEMORY_EMBEDDING_MAX_ATTEMPTS
    assert db.get_json("memory_chunks", "g8")[0]["embedding_status"] == "failed_permanent"


def test_one_pass_embeds_at_most_the_configured_number_of_chunks() -> None:
    stored_without_vector("g9", count=10)
    with patch.object(memory_rag, "_embed_texts", vectors):
        stats = memory_rag.backfill_embeddings("g9", limit=3)
    assert stats["embedded"] == 3 and sum(c["embedding"] is not None for c in db.get_json("memory_chunks", "g9")) == 3


def test_an_oversized_chunk_stored_before_parts_existed_is_split_when_it_is_retried() -> None:
    legacy = stored_without_vector("g10", text_size=900)
    legacy[0]["text"] = "。".join(f"第{n}句" + "字" * 40 for n in range(20)) + "。"
    db.set_json("memory_chunks", "g10", legacy)
    sent: list[list[str]] = []

    def record(texts: list[str], rag_kind: str = "memory") -> list[list[float]]:
        sent.append(list(texts))
        return vectors(texts)

    with patch.object(memory_rag, "_embed_texts", record):
        stats = memory_rag.backfill_embeddings("g10")
    assert stats["split"] == 1 and all(len(text) <= LIMIT for batch in sent for text in batch)
    children = db.get_json("memory_chunks", "g10")
    assert len(children) > 1 and {c["parent_id"] for c in children} == {"c0"}
    assert "".join(c["text"] for c in children) == legacy[0]["text"]
    assert all(c["embedding"] is not None for c in children)


def test_a_backfill_never_overwrites_a_chunk_that_changed_meanwhile() -> None:
    stored_without_vector("g11")

    def embed_while_another_writer_wins(texts: list[str], rag_kind: str = "memory") -> list[list[float]]:
        rows = db.get_json("memory_chunks", "g11")
        rows[0]["embedding"] = [9.0, 9.0]
        rows.append({"chunk_id": "late", "idempotency_key": "late", "text": "後來新增", "timeline_id": "timeline-a",
                     "embedding": [1.0, 1.0]})
        db.set_json("memory_chunks", "g11", rows)
        return vectors(texts)

    with patch.object(memory_rag, "_embed_texts", embed_while_another_writer_wins):
        memory_rag.backfill_embeddings("g11")
    rows = db.get_json("memory_chunks", "g11")
    assert [r["chunk_id"] for r in rows] == ["c0", "late"] and rows[0]["embedding"] == [9.0, 9.0]


def test_maintenance_never_stores_one_unbounded_chunk() -> None:
    state = GroupState("g12", timeline_id="timeline-a")
    state.log = messages(40)
    group_state.save_state(state)
    with patch.object(keeper, "MAX_LOG_TURNS", 1), patch.object(keeper, "run_scene_digest_maintenance"), \
            patch.object(keeper, "summarize_log_chunk", return_value="摘要"), \
            patch.object(memory_rag, "_embed_texts", vectors):
        result = keeper.run_post_turn_maintenance("g12")
    assert result["commit_status"] == "committed" and result["embedding_updated"] is True
    chunks = db.get_json("memory_chunks", "g12")
    assert len(chunks) > 1 and all(len(c["text"]) <= LIMIT and c["embedding"] is not None for c in chunks)


def test_maintenance_gives_earlier_gaps_another_chance_before_it_trims() -> None:
    stored_without_vector("g13", count=2)
    state = GroupState("g13", timeline_id="timeline-a")
    group_state.save_state(state)
    with patch.object(keeper, "run_scene_digest_maintenance"), patch.object(memory_rag, "_embed_texts", vectors):
        keeper.run_post_turn_maintenance("g13")
    assert all(c["embedding"] is not None for c in db.get_json("memory_chunks", "g13"))


# --- review: configuration is not a refusal; folded parts keep every correction; one writer at a time ---------

def test_a_missing_key_is_never_held_against_a_chunk() -> None:
    stored_without_vector("g14", count=2)

    def no_key(texts: list[str], rag_kind: str = "memory") -> None:
        embedding_execution._local.failure = embedding_execution.EmbeddingFailure("missing_api_key", "none", True)

    with patch.object(memory_rag, "_embed_texts", no_key):
        for _ in range(6):
            memory_rag.backfill_embeddings("g14")
    rows = db.get_json("memory_chunks", "g14")
    assert all(r.get("embedding_status", "pending") == "pending" and r.get("embedding_attempts", 0) == 0 for r in rows)
    with patch.object(memory_rag, "_embed_texts", vectors):  # a key is configured later
        assert memory_rag.backfill_embeddings("g14")["embedded"] == 2


def test_a_chunk_stored_while_no_key_was_configured_stays_eligible_once_one_is() -> None:
    rows = messages(2, size=10)
    def no_key(texts: list[str], rag_kind: str = "memory") -> None:
        embedding_execution._local.failure = embedding_execution.EmbeddingFailure("missing_api_key", "none", True)

    with patch.object(memory_rag, "_embed_texts", no_key):
        prepared = memory_rag.prepare_memory(rows, sources(rows))
    assert prepared.failure is not None and prepared.failure.retryable
    commit("g15", rows, prepared)
    [chunk] = db.get_json("memory_chunks", "g15")
    assert chunk["embedding_status"] == "pending" and chunk["embedding_attempts"] == 0
    with patch.object(memory_rag, "_embed_texts", vectors):
        assert memory_rag.backfill_embeddings("g15")["embedded"] == 1


def test_folded_parts_keep_every_parts_correction_and_provenance() -> None:
    rows = messages(12)
    with patch.object(memory_rag, "_embed_texts", vectors):
        prepared = memory_rag.prepare_memory(rows, sources(rows))
    commit("g16", rows, prepared)
    # A correction lands on a part that is not the best-scoring one.
    stored = db.get_json("memory_chunks", "g16")
    stored[-1]["superseded_by"] = ["fix-9"]
    db.set_json("memory_chunks", "g16", stored)
    memory_rag._index_cache.clear()
    with patch.object(memory_rag, "_embed_texts", vectors):
        [result] = memory_rag.search_memory("g16", "事件0 字", top_k=3, timeline_id="timeline-a")
    assert result["parts"] == sorted(result["parts"]) and len(result["parts"]) > 1
    assert result["superseded_by"] == ["fix-9"]
    assert [m["turn_id"] for m in result["source_messages"]] == [f"turn-{n}" for n in range(12)]
    assert "fix-9" in memory_rag.format_results([result])


def test_a_correction_annotation_waits_for_a_backfill_in_progress() -> None:
    import threading

    stored_without_vector("g17")
    rows = db.get_json("memory_chunks", "g17")
    rows[0]["source_messages"] = [{"turn_id": "turn-0"}]
    db.set_json("memory_chunks", "g17", rows)
    release, started, results = threading.Event(), threading.Event(), {}

    def slow_embed(texts: list[str], rag_kind: str = "memory") -> list[list[float]]:
        started.set()
        release.wait(5)
        return vectors(texts)

    def backfill() -> None:
        with patch.object(memory_rag, "_embed_texts", slow_embed):
            memory_rag.backfill_embeddings("g17")

    worker = threading.Thread(target=backfill)
    worker.start()
    started.wait(5)
    # The embedding is in flight (outside the lock); the annotation lands now, and the apply phase must keep it.
    results["changed"] = memory_rag.mark_superseded_receipt(
        "g17", "timeline-a", turn_id="turn-0", excerpt="事件0", correction_id="fix-1")
    release.set()
    worker.join(10)
    [row] = db.get_json("memory_chunks", "g17")
    assert results["changed"] == 1 and row["superseded_by"] == ["fix-1"] and row["embedding"] is not None


def test_every_writer_of_the_memory_blob_takes_the_same_lock() -> None:
    import inspect

    for function in (memory_rag.mark_superseded_receipt, memory_rag.append_memory, memory_rag.backfill_embeddings):
        assert "locks.get_state_lock(group_id)" in inspect.getsource(function), function.__name__
