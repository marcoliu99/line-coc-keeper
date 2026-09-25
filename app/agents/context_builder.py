from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import async_utils, memory_rag, observability, scenario_rag
from app.config import (
    EMBEDDING_REQUEST_TIMEOUT_SECONDS,
    SCENARIO_RAG_EMBEDDING_MODEL,
    SCENARIO_RAG_EMBEDDING_WEIGHT,
    SCENARIO_RAG_ENABLED,
    SCENARIO_RAG_TOP_K,
)
from app.domain.models import AgentMessage
from app.models import GroupState

_logger = logging.getLogger(__name__)


async def build_context(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: str,
    conversation_id: str,
) -> AgentMessage:
    """
    Gathers all necessary state, history, RAG, and Memory context for the
    current turn, packaging it into an AgentMessage envelope.
    """
    # Resolve through the active binding instead of the legacy owner index so
    # a stale persisted mapping cannot make a sudo turn use the wrong sheet.
    char = state.get_active_character(user_id)

    # 1. RAG Context (Scenario Text)
    # scenario_rag has no single query_scenario() entry point — it's a
    # build/cache-index-then-search API (see app/keeper.py's search_scenario
    # tool for the established call shape: get_index -> search ->
    # format_results). If the user isn't bound to a character yet (e.g. they
    # are just chatting in the lobby), we still run RAG but skip
    # location-based matching — scenario_rag.search has no location
    # parameter of its own; resolved_location stays available separately in
    # the payload for anything downstream that wants it.
    #
    # Gated on SCENARIO_RAG_ENABLED (default off) the same way
    # keeper._build_static_prompt is: when it's off, the full scenario text
    # (or the current chapter window's text, via the scenario library) is
    # already embedded directly in the static prompt, making this proactive
    # search redundant — and, depending on the configured embeddings
    # backend, a real per-turn cost for zero benefit.
    # Skipped entirely during active combat: combat_block (app/keeper.py's
    # dynamic prompt) already carries the full mechanical state — initiative
    # order, combatant HP, enemy abilities — that combat narration actually
    # needs, so proactive scenario/memory RAG's marginal narrative value is
    # low there, while its embeddings-API round trip (2-5s, see
    # docs/npc_attack_latency_design_spec.md) is a real, unconditional cost
    # on every combat turn regardless of whether anyone asked a
    # scenario/memory-dependent question. Not a correctness change outside
    # combat: state.combat.active is False for every turn this behaved
    # identically before.
    rag_task = None
    if SCENARIO_RAG_ENABLED and state.scenario_text and state.scenario_title and not state.combat.active:
        def _run_scenario_rag() -> tuple[str, str]:
            # See app/keeper.py's search_scenario tool for why this is a
            # plain _logger call, not a structured event field. This site
            # runs proactively once per turn (whenever SCENARIO_RAG_ENABLED)
            # — distinct from the Keeper explicitly choosing to call the
            # search_scenario tool — so the log line is tagged accordingly
            # to keep the two apart when reading a turn's log.
            _logger.info("context_builder.scenario_rag query=%r", text)
            metrics: dict[str, Any] = {}
            with observability.span(
                "rag.search",
                rag_kind="scenario",
                top_k=SCENARIO_RAG_TOP_K,
                embedding_model=SCENARIO_RAG_EMBEDDING_MODEL,
                embedding_weight=SCENARIO_RAG_EMBEDDING_WEIGHT,
                metrics=metrics,
            ):
                index = scenario_rag.get_index(conversation_id, state.scenario_text)
                results = scenario_rag.search(index, text, top_k=SCENARIO_RAG_TOP_K, metrics=metrics)
                metrics.update(
                    candidate_count=len(getattr(index, "chunks", ())),
                    result_count=len(results),
                    has_embeddings=getattr(index, "has_embeddings", None),
                    index_cache=getattr(index, "index_cache", "unknown"),
                )
                if not results:
                    return "", "empty"
                if (
                    metrics.get("has_embeddings") is False
                    or metrics.get("query_embedding_status") == "fallback"
                ):
                    # BM25 remains available to the explicit search tool, but
                    # proactive prompt context only accepts a successful
                    # semantic source.
                    return "", "fallback"
                return scenario_rag.format_results(results), "success"

        rag_task = asyncio.create_task(asyncio.to_thread(_run_scenario_rag))

    # 2. Memory Context (Past events) — same two-call shape as
    # app/keeper.py's search_memory tool: search_memory -> format_results.
    # See the scenario RAG block above for why this also skips during active
    # combat — this one is the more significant saving in practice, since it
    # runs on essentially every player turn with a bound character
    # (unconditional on SCENARIO_RAG_ENABLED), not just when that flag is on.
    memory_task = None
    if char and not state.combat.active:
        def _run_memory_rag() -> tuple[str, str]:
            # See _run_scenario_rag's comment above — this one runs on
            # essentially every player turn with a bound character
            # (unconditional on SCENARIO_RAG_ENABLED), so it's very likely
            # to be *the* proactive contributor when a turn's log shows an
            # embeddings call the Keeper never explicitly asked for via the
            # search_memory tool.
            _logger.info("context_builder.memory_rag query=%r", text)
            metrics: dict[str, Any] = {}
            with observability.span(
                "memory.search", rag_kind="memory", embedding_model=SCENARIO_RAG_EMBEDDING_MODEL,
                embedding_weight=SCENARIO_RAG_EMBEDDING_WEIGHT, metrics=metrics,
            ):
                search_kwargs: dict[str, Any] = {"metrics": metrics}
                # Never let a missing legacy timeline turn this production
                # prompt path into an unscoped group-wide memory search. A
                # fresh/legacy state uses its compatibility timeline until a
                # normal save initializes a new explicit timeline.
                search_kwargs["timeline_id"] = state.timeline_id or f"legacy-{conversation_id}"
                results = memory_rag.search_memory(conversation_id, text, **search_kwargs)
                if not results:
                    return "", "empty"
                if (
                    metrics.get("has_embeddings") is False
                    or metrics.get("query_embedding_status") == "fallback"
                ):
                    return "", "fallback"
                return memory_rag.format_results(results), "success"

        memory_task = asyncio.create_task(asyncio.to_thread(_run_memory_rag))

    async def _collect_rag_source(task: asyncio.Task | None, rag_kind: str) -> tuple[str, str]:
        if task is None:
            return "", "disabled"
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), EMBEDDING_REQUEST_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            async_utils.observe_background_task(task, operation=f"rag.{rag_kind}")
            observability.event("rag.source.cancelled", level=logging.WARNING, rag_kind=rag_kind)
            raise
        except asyncio.TimeoutError:
            async_utils.observe_background_task(task, operation=f"rag.{rag_kind}")
            observability.event(
                "rag.source.degraded", level=logging.WARNING,
                rag_kind=rag_kind, status="timeout",
                timeout_ms=EMBEDDING_REQUEST_TIMEOUT_SECONDS * 1000,
            )
            return "", "timeout"
        except Exception as exc:
            observability.event(
                "rag.source.degraded", level=logging.WARNING,
                rag_kind=rag_kind, status="error", error_type=type(exc).__name__,
            )
            _logger.exception("%s RAG failed; continuing without that source", rag_kind)
            return "", "error"
        if isinstance(result, tuple) and len(result) == 2:
            result, status = result
        else:
            status = "success" if result else "empty"
        if status != "success":
            observability.event("rag.source.degraded", level=logging.INFO, rag_kind=rag_kind, status=status)
            return "", status
        if not result:
            return "", "empty"
        return result, status

    # Await both sources at one explicit synchronization point.  The tasks
    # start before gather, so scenario and memory search/embedding can overlap;
    # return_exceptions=True keeps one optional source from discarding the
    # other.  Cancellation is handled by _collect_rag_source and propagated.
    rag_context = ""
    memory_context = ""
    rag_status = "disabled"
    memory_status = "disabled"
    tasks = [task for task in (rag_task, memory_task) if task is not None]
    if tasks:
        collected = await asyncio.gather(
            *(
                _collect_rag_source(task, rag_kind)
                for task, rag_kind in ((rag_task, "scenario"), (memory_task, "memory"))
                if task is not None
            ),
            return_exceptions=True,
        )
        result_by_kind = {
            kind: result
            for kind, result in zip(
                (kind for task, kind in ((rag_task, "scenario"), (memory_task, "memory")) if task is not None),
                collected,
                strict=True,
            )
            if not isinstance(result, BaseException)
        }
        for result in collected:
            if isinstance(result, asyncio.CancelledError):
                raise result
        rag_context, rag_status = result_by_kind.get("scenario", ("", "error"))
        memory_context, memory_status = result_by_kind.get("memory", ("", "error"))

    # 3. Compile the payload
    payload = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "display_name": display_name,
        "speaker_role": speaker_role,
        "text": text,
        "resolved_location": resolved_location,
        "state": state,  # Reference to the current GroupState
        "character": char, # Reference to the active Character (if any)
        # Historical finalized outcomes are deliberately separate from this
        # turn's tool results. Filter by owner and current timeline so this
        # context cannot leak another investigator's private sheet history.
        "resolved_check_events": [
            dict(event)
            for event in state.resolved_check_events[-20:]
            if event.get("owner_id") == user_id
            and event.get("timeline_id") == (state.timeline_id or f"legacy-{conversation_id}")
        ] if char else [],
        "rag_context": rag_context,
        "memory_context": memory_context,
        "rag_status": rag_status,
        "memory_status": memory_status,
    }

    return AgentMessage(payload=payload)
