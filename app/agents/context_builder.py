from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.models import GroupState
from app.domain.models import AgentMessage
from app.config import SCENARIO_RAG_ENABLED, SCENARIO_RAG_TOP_K, SCENARIO_RAG_EMBEDDING_MODEL, SCENARIO_RAG_EMBEDDING_WEIGHT
from app import memory_rag, observability, scenario_rag

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
    rag_task = None
    if SCENARIO_RAG_ENABLED and state.scenario_text and state.scenario_title:
        def _run_scenario_rag() -> str:
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
                results = scenario_rag.search(index, text, top_k=SCENARIO_RAG_TOP_K)
                metrics.update(
                    candidate_count=len(getattr(index, "chunks", ())),
                    result_count=len(results),
                    has_embeddings=getattr(index, "has_embeddings", None),
                    index_cache=getattr(index, "index_cache", "unknown"),
                )
                return scenario_rag.format_results(results)

        rag_task = asyncio.create_task(asyncio.to_thread(_run_scenario_rag))

    # 2. Memory Context (Past events) — same two-call shape as
    # app/keeper.py's search_memory tool: search_memory -> format_results.
    memory_task = None
    if char:
        def _run_memory_rag() -> str:
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
                results = memory_rag.search_memory(conversation_id, text, metrics=metrics)
                return memory_rag.format_results(results)

        memory_task = asyncio.create_task(asyncio.to_thread(_run_memory_rag))

    # Await RAG tasks
    rag_context = ""
    memory_context = ""
    if rag_task:
        rag_context = await rag_task
    if memory_task:
        memory_context = await memory_task

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
        "rag_context": rag_context,
        "memory_context": memory_context,
    }

    return AgentMessage(payload=payload)
