from __future__ import annotations

import asyncio
from typing import Any

from app.models import GroupState
from app.domain.models import AgentMessage
from app.config import SCENARIO_RAG_TOP_K
from app import scenario_rag, memory_rag


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
    char = state.characters.get(user_id)

    # 1. RAG Context (Scenario Text)
    # scenario_rag has no single query_scenario() entry point — it's a
    # build/cache-index-then-search API (see app/keeper.py's search_scenario
    # tool for the established call shape: get_index -> search ->
    # format_results). If the user isn't bound to a character yet (e.g. they
    # are just chatting in the lobby), we still run RAG but skip
    # location-based matching — scenario_rag.search has no location
    # parameter of its own; resolved_location stays available separately in
    # the payload for anything downstream that wants it.
    rag_task = None
    if state.scenario_text and state.scenario_title:
        def _run_scenario_rag() -> str:
            index = scenario_rag.get_index(conversation_id, state.scenario_text)
            results = scenario_rag.search(index, text, top_k=SCENARIO_RAG_TOP_K)
            return scenario_rag.format_results(results)

        rag_task = asyncio.create_task(asyncio.to_thread(_run_scenario_rag))

    # 2. Memory Context (Past events) — same two-call shape as
    # app/keeper.py's search_memory tool: search_memory -> format_results.
    memory_task = None
    if char:
        def _run_memory_rag() -> str:
            results = memory_rag.search_memory(conversation_id, text)
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
