from __future__ import annotations

import logging
from typing import Any

from app import keeper, observability, spoiler_policy
from app.agents import (
    assistant,
    context_builder,
    executor,
    guard,
    intent_router,
    narrator,
    state_reducer,
)
from app.domain.models import MechanicResult
from app.models import GroupState
from app.services import prompt_config

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
    Orchestrates the asynchronous pipeline of Agents to produce a response.
    Returns: (reply_text, private_messages, image_requests)
    """
    _logger.info(f"Supervisor starting turn for {display_name} ({user_id})")
    # Capture one authoritative timeline before any agent await.  Executor
    # tools may initialize or persist timeline-bound state; without this
    # early capture, a legacy state with no timeline would later fall back to
    # ``legacy-*`` and the canonical log commit could reject the whole turn.
    turn_timeline_id = keeper._ensure_turn_timeline(state)

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
    mechanic_result: MechanicResult | None = None
    if intent == "GAMEPLAY_ACTION":
        _logger.info("Routing to ExecutorAgent (Slow Path)")
        mechanic_result = await executor.run_executor(message)
        # The post-tool in-memory snapshot is synchronized from persisted state
        # by _mutate_and_save_state. Prefer that authoritative final state to
        # tool-call summaries, and include a pending check carried in from an
        # earlier turn too.
        pending_check = state.pending_checks.get(user_id)
        if pending_check:
            pending_details = {
                key: pending_check[key]
                for key in ("investigator", "skill", "skill_value", "difficulty", "options")
                if key in pending_check
            }
            active_character = state.get_active_character(user_id)
            if active_character is not None:
                pending_details.setdefault("investigator", active_character.name)
            mechanic_result.check_status["pending"] = pending_details
        else:
            mechanic_result.check_status["pending"] = None
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

    # 6. Rule Validator & Guard Agent (Repair Loop) — see
    # docs/specs/enhancement-guard-agent.md for the GUARD_ENABLED switch and
    # the fail-closed fallback this delegates to.
    reply_text = await guard.enforce_narrative_safety(message, reply_text)

    # 7. Spoiler output guard (§6 of the spoiler-protection-hardening spec) —
    # separate from the Rule Validator/Guard Agent loop above, which only
    # checks for system leaks/formatting. This is a deterministic scan for
    # kp_only facts/clues and secret goals; a hit gets a fixed neutral
    # fallback rather than another LLM repair attempt (see spoiler_policy).
    spoiler_check = spoiler_policy.sanitize_public_text(
        reply_text, spoiler_policy.collect_protected_terms(state)
    )
    if not spoiler_check.is_safe:
        reply_text = spoiler_check.fallback_text or reply_text

    if intent == "GAMEPLAY_ACTION" and mechanic_result is not None:
        checked_reply = prompt_config.enforce_mechanic_check_consistency(reply_text, mechanic_result)
        if checked_reply != reply_text:
            observability.event("narrator.check_consistency.corrected", status="corrected")
            reply_text = checked_reply

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
        committed = keeper._commit_turn_result(
            state,
            [
                {"role": "user", "content": f"{speaker_role} {display_name}: {text}"},
                {"role": "assistant", "content": reply_text},
            ],
            timeline_id=turn_timeline_id,
        )
        if not committed:
            return "（這次回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）", [], []

    return reply_text, private_messages, image_requests
