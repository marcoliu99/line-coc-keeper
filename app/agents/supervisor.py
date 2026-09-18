from __future__ import annotations

import logging
from typing import Any

from app.models import GroupState
from app.agents import context_builder, intent_router, executor, state_reducer, narrator, rule_validator, guard


_logger = logging.getLogger(__name__)


async def run_turn(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: str,
    conversation_id: str,
) -> tuple[str, list[dict], list[dict]]:
    """
    The main entry point for the Agentic Keeper Supervisor.
    Orchestrates the synchronous pipeline of Agents to produce a response.
    Returns: (reply_text, private_messages, image_requests)
    """
    _logger.info(f"Supervisor starting turn for {display_name} ({user_id})")

    # 1. Build Context
    message = await context_builder.build_context(
        state=state,
        user_id=user_id,
        display_name=display_name,
        text=text,
        resolved_location=resolved_location,
        speaker_role=speaker_role,
        conversation_id=conversation_id,
    )

    # 2. Intent Routing (Fast Path vs Slow Path)
    intent = intent_router.classify_intent(message)
    message.payload["intent"] = intent
    
    _logger.info(f"Intent classified as: {intent}")

    # 3. Route to Executor (Slow Path) or Skip to Narrator (Fast Path)
    if intent == "GAMEPLAY_ACTION":
        _logger.info("Routing to ExecutorAgent (Slow Path)")
        mechanic_result = await executor.run_executor(message)
        
        # 4. State Reducer (Pure Python)
        state_reducer.apply_mechanic_result(message, mechanic_result)
    else:
        _logger.info("Routing directly to NarratorAgent (Fast Path)")

    # 5. Narrator Agent generates the final text
    reply_text, private_messages, image_requests = await narrator.run_narrator(message)

    # 6. Rule Validator & Guard Agent (Repair Loop)
    max_repairs = 2
    attempts = 0
    while attempts < max_repairs:
        is_valid, error_reason = rule_validator.validate_narrative(reply_text)
        if is_valid:
            break
            
        _logger.warning(f"Narrative validation failed: {error_reason}. Triggering Guard Agent (Attempt {attempts + 1}).")
        reply_text = await guard.run_repair(message, reply_text, error_reason)
        attempts += 1

    # Note: Phase 6 Persistence (save_state) is already handled inside the StateReducer 
    # for GAMEPLAY_ACTION. If we need to save dialogue history for PURE_ROLEPLAY,
    # it would be handled via a log append here.
    from app.repositories.group_state import save_state
    
    # Append the final turn to the log
    if state.game_started:
        state.log.append({"role": "user", "content": f"{speaker_role} {display_name}: {text}"})
        state.log.append({"role": "assistant", "content": reply_text})
        save_state(state)

    return reply_text, private_messages, image_requests
