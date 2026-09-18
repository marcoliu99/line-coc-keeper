from __future__ import annotations

import logging
from typing import Any

from app import keeper
from app.models import GroupState
from app.agents import context_builder, intent_router, executor, state_reducer, narrator, rule_validator, guard, assistant


_logger = logging.getLogger(__name__)


async def run_turn(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: str,
    conversation_id: str,
) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
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

    # Phase 10: OOC Assistant Path — KP 助手的場外討論完全繞開「機制判定與故事
    # 生成」這條主線（Executor／State Reducer／Narrator／Rule Validator／
    # Guard），直接在這裡回傳。資料隔離見 app/agents/assistant.py 的 docstring：
    # 這輪對話進 state.kp_ooc_log，不進 state.log，所以下面的
    # keeper._commit_turn_result 也不能執行到——提早 return。
    if intent == "OOC_ASSISTANT":
        _logger.info("Routing to AssistantAgent (OOC Path)")
        return await assistant.run_assistant(message)

    # 3. Route to Executor (Slow Path) or Skip to Narrator (Fast Path)
    if intent == "GAMEPLAY_ACTION":
        _logger.info("Routing to ExecutorAgent (Slow Path)")
        mechanic_result = await executor.run_executor(message)
        # Narrator reads this back out of the payload (see narrator.py) to
        # decide between build_mechanic_facts_block and PURE_ROLEPLAY_BLOCK —
        # without this, every GAMEPLAY_ACTION turn silently narrated as if
        # nothing mechanical had happened, contradicting whatever the
        # Executor's tool calls actually rolled/changed.
        message.payload["mechanic_result"] = mechanic_result

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

    # Persistence for GAMEPLAY_ACTION's actual game-state changes (HP/SAN/
    # pending_checks/combat/etc.) already happened inside the Executor's
    # tool calls, via keeper._execute_tool's own locked
    # (_mutate_and_save_state) path — see state_reducer.py's docstring.
    # What's left here is just committing this turn's log entries, the same
    # way app/keeper.py's own run_turn does for the old single-LLM path:
    # reload the latest state under the state lock (so this can't clobber
    # whatever the tool calls above already saved), append, save, then sync
    # this function's own `state` object so a caller that keeps using it
    # afterward sees the up-to-date snapshot.
    if state.game_started:
        keeper._commit_turn_result(
            state,
            [
                {"role": "user", "content": f"{speaker_role} {display_name}: {text}"},
                {"role": "assistant", "content": reply_text},
            ],
        )

    return reply_text, private_messages, image_requests
