"""Post-turn log maintenance: rolling summary, scene digest and memory chunks.

Split out of app/keeper.py unchanged. It runs after the reply has been sent (see
app/services/post_turn.py), never inside a player's turn, and tolerates running unlocked around its own
slow model and embedding calls: a per-conversation in-flight guard keeps two passes from overlapping and its
persist step re-derives what to trim from a freshly reloaded log.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app import (
    locks,
    memory_rag,
    observability,
    scene_digest,
)
from app.config import (
    MAX_LOG_TURNS,
    SCENE_DIGEST_TURN_INTERVAL,
)
from app.keeper_tools import registry as tool_registry
from app.providers.registry import conversation_provider
from app.repositories import state_transaction
from app.repositories.group_state import load_state
from app.services import (
    history_authority,
    mutation_admission,
    turn_phases,
)

_logger = logging.getLogger(__name__)


def _persist_memory_maintenance_state(
    group_id: str,
    campaign_summary: str,
    dropped_chunk: list[dict[str, Any]],
    *,
    timeline_id: str,
    base_summary: str,
    source_revision: int,
    idempotency_key: str,
    embedding: list[float] | None,
    prepared: memory_rag.PreparedMemory | None = None,
) -> str:
    """Commit the maintenance trim and memory chunk atomically.

    ``prepared`` is the trim split into parts that each fit one embedding (``memory_rag.prepare_memory``); without
    it the whole trim is one chunk carrying ``embedding``."""
    idempotency_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
    observability.event(
        "maintenance.commit.started",
        timeline_id=timeline_id,
        source_revision=source_revision,
        idempotency_key_hash=idempotency_hash,
    )
    def commit_trim(ctx: state_transaction.TxContext) -> str:
        latest_state = ctx.state
        latest_timeline_id = ctx.timeline_id
        if latest_state.campaign_summary != base_summary:
            observability.event(
                "maintenance.commit_skipped", level=logging.WARNING,
                reason="summary_changed", source_revision=source_revision,
                current_revision=latest_state.state_revision,
                requested_timeline_id=timeline_id,
                current_timeline_id=latest_timeline_id,
                idempotency_key_hash=idempotency_hash,
            )
            ctx.skip_save()
            return "stale_summary"
        memory_row = ctx.conn.execute("SELECT data FROM memory_chunks WHERE key = ?", (group_id,)).fetchone()
        if memory_row is not None:
            try:
                existing_chunks = json.loads(memory_row[0])
            except (TypeError, json.JSONDecodeError):
                existing_chunks = []
            if not isinstance(existing_chunks, list):
                existing_chunks = []
            if any(
                isinstance(item, dict) and idempotency_key in {item.get("idempotency_key"), item.get("parent_id")}
                for item in existing_chunks
            ):
                observability.event(
                    "maintenance.commit_skipped", level=logging.INFO,
                    reason="duplicate_idempotency_key", source_revision=source_revision,
                    current_revision=latest_state.state_revision,
                    requested_timeline_id=timeline_id,
                    current_timeline_id=latest_timeline_id,
                    idempotency_key_hash=idempotency_hash,
                )
                ctx.skip_save()
                return "duplicate"
        # Only apply anything if the front of the freshly-reloaded log still
        # matches what was actually dropped — guards against e.g. a
        # concurrent /coc newgame reset, or another maintenance pass having
        # already trimmed this exact chunk. On a mismatch, skip BOTH the log
        # trim and the campaign_summary update (not just the trim): the
        # summary was derived from `dropped_chunk`, which no longer reflects
        # what's actually at the front of the current log, so applying it
        # anyway would bleed a stale/unrelated summary into whatever state
        # is live now (e.g. a brand-new campaign after /coc newgame
        # inheriting leftover summary text from the campaign it replaced).
        # Skipping entirely costs nothing but retrying this trim on a later
        # turn — never a correctness problem, and never a partial write.
        n = len(dropped_chunk)
        if not dropped_chunk or latest_state.log[:n] != dropped_chunk:
            observability.event(
                "maintenance.commit_skipped", level=logging.WARNING,
                reason="log_prefix_changed", source_revision=source_revision,
                current_revision=latest_state.state_revision,
                requested_timeline_id=timeline_id,
                current_timeline_id=latest_timeline_id,
                idempotency_key_hash=idempotency_hash,
            )
            ctx.skip_save()
            return "stale_log_prefix"
        latest_state.log = latest_state.log[n:]
        latest_state.campaign_summary = campaign_summary
        if prepared is not None:
            memory_appended = memory_rag.append_memory_parts_tx(
                ctx.conn, group_id, prepared, timeline_id=timeline_id,
                idempotency_key=idempotency_key, source_revision=source_revision,
            )
        else:
            memory_appended = memory_rag.append_memory_tx(
                ctx.conn,
                group_id,
                "\n".join(f"{m['role']}: {m['content']}" for m in dropped_chunk),
                timeline_id=timeline_id,
                idempotency_key=idempotency_key,
                source_revision=source_revision,
                embedding=embedding,
                source_messages=history_authority.memory_source_messages(dropped_chunk),
            )
        ctx.set_result({"memory_appended": bool(memory_appended)})
        return "committed"

    try:
        with turn_phases.phase("memory_write"):
            result = state_transaction.mutate(
                group_id, commit_trim, reason="maintenance", expected_timeline=timeline_id,
            )
    except state_transaction.CorruptStateError as exc:
        observability.event(
            "maintenance.commit_skipped",
            level=logging.WARNING,
            reason="corrupt_group_state",
            source_revision=source_revision,
            requested_timeline_id=timeline_id,
            idempotency_key_hash=idempotency_hash,
            error_type=type(exc.__cause__ or exc).__name__,
        )
        return "corrupt_group_state"
    if result.outcome is state_transaction.Outcome.STALE_TIMELINE:
        observability.event(
            "maintenance.commit_skipped", level=logging.WARNING,
            reason="timeline_mismatch", source_revision=source_revision,
            requested_timeline_id=timeline_id,
            current_timeline_id=result.timeline_id,
            idempotency_key_hash=idempotency_hash,
        )
        return "stale_timeline"
    if result.value != "committed":
        return str(result.value)
    observability.event(
        "maintenance.commit_completed", source_revision=source_revision,
        committed_revision=result.revision,
        memory_appended=result.result.get("memory_appended"),
        requested_timeline_id=timeline_id,
        current_timeline_id=result.timeline_id,
        idempotency_key_hash=idempotency_hash,
    )
    return "committed"


# Guards against more than one run_post_turn_maintenance pass running
# concurrently for the same group_id — see that function's own docstring.
_maintenance_in_flight: set[str] = set()


def run_scene_digest_maintenance(group_id: str) -> None:
    with locks.get_state_lock(group_id):
        mutation_admission.assert_admitted(group_id)
        state = load_state(group_id)
        latest = scene_digest.latest_digest(group_id, state.timeline_id)
        chapter_changed = latest is None or latest.get("scene_label") != (state.active_chapter_id or state.scenario_title or "目前場景")
        current_log_length = len(state.log)
        previous_log_length = latest.get("log_length", 0) if latest else 0
        # Log maintenance can intentionally shrink the in-memory log. Treat
        # that as a new baseline; otherwise the old larger watermark would
        # make this subtraction negative and periodic digests would stop.
        log_was_trimmed = latest is not None and current_log_length < previous_log_length
        log_interval_reached = (
            latest is None
            or log_was_trimmed
            or current_log_length - previous_log_length >= SCENE_DIGEST_TURN_INTERVAL
        )
        if not (chapter_changed or log_interval_reached):
            return
        scene_digest.create_digest(state)


def _run_post_turn_maintenance(group_id: str) -> dict[str, object]:
    """Called after every turn (see app/services/post_turn.py's
    spawn_post_turn_maintenance, which now fires this as an independent
    background task rather than awaiting it inline). Only does real work
    once the log actually crosses the trim threshold — every other call is a
    cheap no-op. `_maintenance_in_flight` skips a call outright if a pass is
    already running for this group_id: without it, several turns landing
    back-to-back while the log is still above threshold would each spawn
    their own full pass (duplicate LLM summarization + embedding API costs),
    racing on the same log/memory-chunk data. The worker prepares the summary
    and embedding outside the commit gate, then appends the prepared chunk
    through memory_rag.append_memory_tx inside the same SQLite transaction as
    the state trim. The in-flight guard avoids duplicate slow work; atomicity
    comes from the commit gate, not from this guard alone.

    The check-then-add on `_maintenance_in_flight` below is itself wrapped in
    `locks.get_state_lock(group_id)` — this function runs via
    `asyncio.to_thread` (see post_turn.spawn_post_turn_maintenance), i.e. on real OS
    worker threads, not just concurrent asyncio tasks, so the GIL making each
    individual `in`/`.add()` call atomic does NOT make the pair atomic: two
    threads could otherwise both observe `group_id not in
    _maintenance_in_flight` before either adds it, both proceed, and run two
    overlapping passes anyway — exactly the failure mode this guard exists
    to prevent."""
    with locks.get_state_lock(group_id):
        if group_id in _maintenance_in_flight:
            return {"skipped": True}
        _maintenance_in_flight.add(group_id)
    result: dict[str, object] = {
        "summary_updated": False,
        "embedding_updated": False,
        "state_saved": False,
        "commit_status": "not_started",
    }
    try:
        run_scene_digest_maintenance(group_id)
        try:
            memory_rag.backfill_embeddings(group_id)
        except Exception:  # an optional index must never stop the trim below
            _logger.exception("Memory embedding backfill failed for %s", group_id)
        with locks.get_state_lock(group_id):
            latest_state = load_state(group_id)
            if len(latest_state.log) <= MAX_LOG_TURNS * 4:
                return result
            keep_from = -MAX_LOG_TURNS * 2
            base_summary = latest_state.campaign_summary
            timeline_id = latest_state.timeline_id or f"legacy-{group_id}"
            source_revision = latest_state.state_revision
            log_snapshot = [dict(message) for message in latest_state.log]
            dropped_chunk = log_snapshot[:keep_from]

        # Rolling summarization (see summarize_log_chunk above): fold the
        # chunk about to be dropped into campaign_summary *before* dropping
        # it, instead of just discarding it — this is the one rare turn every
        # ~MAX_LOG_TURNS*2 turns that pays for an extra (cheap) LLM call, so
        # early plot points survive past what the verbatim log can hold.
        campaign_summary = summarize_log_chunk(base_summary, dropped_chunk)
        # Also persist the chunk's *original* wording into the searchable
        # memory index (app/memory_rag.py) — campaign_summary alone would
        # keep recompressing an already-compressed summary on every future
        # trim, eroding fine detail a little more each pass; this keeps the
        # verbatim text retrievable via search_memory even after that.
        formatted_chunk = "\n".join(f"{m['role']}: {m['content']}" for m in dropped_chunk)
        prepared = memory_rag.prepare_memory(dropped_chunk, history_authority.memory_source_messages(dropped_chunk))
        embedding = prepared.embeddings[0] if len(prepared.embeddings) == 1 else None
        chunk_digest = hashlib.sha256(formatted_chunk.encode("utf-8")).hexdigest()[:24]
        commit_status = _persist_memory_maintenance_state(
            group_id,
            campaign_summary,
            dropped_chunk,
            timeline_id=timeline_id,
            base_summary=base_summary,
            source_revision=source_revision,
            idempotency_key=f"{timeline_id}:{source_revision}:{chunk_digest}",
            embedding=embedding,
            prepared=prepared,
        )
        observability.event(
            "maintenance.result.completed",
            source_revision=source_revision,
            requested_timeline_id=timeline_id,
            commit_status=commit_status,
            summary_changed=campaign_summary != base_summary,
            embedding_prepared=all(vector is not None for vector in prepared.embeddings),
            embedding_parts=len(prepared.parts),
            embedding_failure=prepared.failure.reason if prepared.failure else None,
        )
        result["commit_status"] = commit_status
        result["summary_updated"] = commit_status in {"committed", "duplicate"} and campaign_summary != base_summary
        result["embedding_updated"] = commit_status in {"committed", "duplicate"} and all(
            vector is not None for vector in prepared.embeddings)
        result["state_saved"] = commit_status in {"committed", "duplicate"}
        return result
    finally:
        with locks.get_state_lock(group_id):
            _maintenance_in_flight.discard(group_id)


def run_post_turn_maintenance(group_id: str) -> dict[str, object]:
    """``_run_post_turn_maintenance`` with its own phase timeline (embedding, memory search and write)."""
    with turn_phases.timeline("maintenance", turn_id=observability.new_id("maint"), player_id="", campaign_id=group_id):
        return _run_post_turn_maintenance(group_id)


def summarize_log_chunk(current_summary: str, old_messages: list[dict[str, Any]]) -> str:
    """Rolling summarization — called only on the rare maintenance
    turn where state.log is about to be trimmed past MAX_LOG_TURNS*4. Folds
    old_messages (the chunk about to be dropped) into current_summary via one
    forced tool call, dispatched through whichever LLM_PROVIDER is configured
    (same analyze_text pattern as app/pregen_extractor.py/scenario_compare.py
    — never hard-coded to one vendor's client, since this project's whole
    point is LLM_PROVIDER being freely switchable).

    Degrades gracefully: no provider configured, the call raises, or it
    returns nothing usable all fall back to returning current_summary
    unchanged (logged, not raised) — a failed summarization should never
    crash the turn or lose the existing summary, only leave it stale."""
    provider = conversation_provider()
    if provider is None:
        return current_summary
    try:
        formatted_history = history_authority.summary_input(old_messages)
        result = provider.analyze_text(
            formatted_history,
            tool_registry.SUMMARY_TOOL,
            "你是一個 TRPG 遊戲紀錄員。請將「待整合的舊對話」融合進「現有摘要」，"
            "更新成一份精煉的對話與敘事摘要，用 report_summary 工具回報。"
            "已送出敘事只證明當時如此描述；玩家聲明只證明曾如此聲稱。"
            "不得把無來源的物品、數量、位置、線索或 NPC 身分寫成確定世界事實。"
            "僅明確 KP 正典與可核對的已提交事件能作權威；與當前狀態或劇本衝突時以後者為準。\n\n"
            "若有【敘事更正】或 superseded 標記，應移除被取代的舊描述；更正仍是呈現修復，不能自動創造劇本事實。\n\n"
            f"【現有摘要（同樣未經驗證）】\n{current_summary or '（目前尚無摘要）'}",
        )
        summary = (result or {}).get("summary", "").strip()
        return summary or current_summary
    except Exception:
        _logger.exception("summarize_log_chunk failed, keeping previous summary unchanged")
        return current_summary
