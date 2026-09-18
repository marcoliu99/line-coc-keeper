from __future__ import annotations

import asyncio
from typing import Any

from app.models import GroupState
from app.domain.models import AgentMessage
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
    # If the user isn't bound to a character yet (e.g. they are just chatting
    # in the lobby), we still run RAG but skip location-based matching.
    rag_task = None
    if state.scenario_text and state.scenario_title:
        # Run RAG asynchronously to save time
        rag_task = asyncio.create_task(
            asyncio.to_thread(
                scenario_rag.query_scenario,
                conversation_id,
                state.scenario_text,
                state.scenario_title,
                text,
                resolved_location,
            )
        )

    # 2. Memory Context (Past events)
    memory_task = None
    if char:
        memory_task = asyncio.create_task(
            asyncio.to_thread(
                memory_rag.query_memory,
                conversation_id,
                user_id,
                text,
            )
        )

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
