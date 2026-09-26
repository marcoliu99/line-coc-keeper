from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Literal

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
from app.providers.turn_budget import with_turn_deadline
from app.services import prompt_config

_logger = logging.getLogger(__name__)
PlayerTurnKind = Literal["player_action", "resolved_check_followup", "opening_fallback"]


@with_turn_deadline
async def run_turn(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: str,
    conversation_id: str,
    *,
    turn_kind: PlayerTurnKind = "player_action",
    resolved_check_context: dict[str, Any] | None = None,
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
    message.payload["turn_kind"] = turn_kind
    if turn_kind == "resolved_check_followup":
        if not resolved_check_context:
            raise ValueError("resolved_check_followup requires an authoritative result")
        message.payload["resolved_check_context"] = resolved_check_context

    # 2. Intent Routing (Fast Path vs Slow Path)
    intent = (
        intent_router.classify_intent(message)
        if turn_kind == "player_action" else turn_kind.upper()
    )
    message.payload["intent"] = intent
    
    _logger.info(f"Intent classified as: {intent}")

    # KP Assistant uses its own provider/tool/Guard/commit path. Its OOC
    # replies enter kp_ooc_log; explicit or tool-created canon enters log.
    # Neither case goes through the player Executor/Narrator pipeline.
    if intent == "OOC_ASSISTANT":
        _logger.info("Routing to AssistantAgent (OOC Path)")
        return await assistant.run_assistant(message)

    # 3. Route to Executor (Slow Path) or Skip to Narrator (Fast Path)
    mechanic_result: MechanicResult | None = None
    if intent == "GAMEPLAY_ACTION":
        _logger.info("Routing to ExecutorAgent (Slow Path)")
        pending_checks_before = deepcopy(state.pending_checks)
        pending_luck_before = deepcopy(state.pending_luck_decisions)
        mechanic_result = await executor.run_executor(message)
        # The post-tool in-memory snapshot is synchronized from persisted state
        # by _mutate_and_save_state. Prefer that authoritative final state to
        # tool-call summaries, and include a pending check carried in from an
        # earlier turn too.
        new_or_changed_pending = [
            (owner_id, pending_check)
            for owner_id, pending_check in state.pending_checks.items()
            if pending_checks_before.get(owner_id) != pending_check
        ]
        # Prefer a check newly created or replaced by this turn, even when it
        # belongs to another player. Otherwise preserve the active player's
        # existing pending check so Narrator does not tell them to create it
        # again.
        resolution = mechanic_result.turn_resolution
        referenced_owner = None
        if resolution and resolution.disposition in {"await_check", "await_luck"}:
            target_id = resolution.waiting_for or resolution.actor_character_id
            referenced_owner = next((c.owner_id for c in state.active_characters()
                                     if (c.character_id or f"legacy-user:{c.owner_id}") == target_id), None)
        pending_check = (
            new_or_changed_pending[-1][1]
            if new_or_changed_pending
            else state.pending_checks.get(user_id)
        )
        pending_owner_id = (
            new_or_changed_pending[-1][0]
            if new_or_changed_pending
            else user_id
        )
        if referenced_owner is not None and resolution and resolution.disposition == "await_check":
            pending_check = state.pending_checks.get(referenced_owner)
            pending_owner_id = referenced_owner
        if pending_check is None:
            mechanic_result.check_status["pending"] = None
        else:
            pending_details = {
                key: pending_check[key]
                for key in (
                    "investigator", "skill", "skill_value", "difficulty", "options",
                    "check_id", "timeline_id", "action_context",
                )
                if key in pending_check
            }
            active_character = state.get_active_character(pending_owner_id)
            if active_character is not None:
                pending_details.setdefault("investigator", active_character.name)
            mechanic_result.check_status["pending"] = pending_details

        # A pending Luck decision means a roll already happened but its
        # outcome is not final. Carry it explicitly to Narrator, including
        # decisions created earlier (e.g. a retried wall action may have been
        # rejected because this choice remains outstanding).
        new_or_changed_luck = [
            (owner_id, decision)
            for owner_id, decision in state.pending_luck_decisions.items()
            if pending_luck_before.get(owner_id) != decision
        ]
        luck_owner_id, pending_luck = (
            new_or_changed_luck[-1]
            if new_or_changed_luck
            else (user_id, state.pending_luck_decisions.get(user_id))
        )
        if referenced_owner is not None and resolution and resolution.disposition in {"await_check", "await_luck"}:
            luck_owner_id = referenced_owner
            pending_luck = state.pending_luck_decisions.get(referenced_owner)
        if pending_luck:
            luck_details: dict[str, Any] = {
                key: pending_luck[key]
                for key in (
                    "skill_name", "display_label", "value", "roll", "original_tier",
                    "difficulty", "options", "decision_id", "check_id", "timeline_id",
                    "action_context",
                )
                if key in pending_luck
            }
            active_character = state.get_active_character(luck_owner_id)
            if active_character is not None:
                luck_details["investigator"] = active_character.name
            mechanic_result.check_status["pending_luck"] = luck_details
            # A pending Luck decision takes precedence over pending narration:
            # the dice are known, but the final tier/outcome is not.
            mechanic_result.check_status["pending"] = None
            mechanic_result.check_status["resolved"] = None
        else:
            mechanic_result.check_status["pending_luck"] = None

        # Narrator reads this back out of the payload (see narrator.py) to
        # decide between build_mechanic_facts_block and PURE_ROLEPLAY_BLOCK —
        # without this, every GAMEPLAY_ACTION turn silently narrated as if
        # nothing mechanical had happened, contradicting whatever the
        # Executor's tool calls actually rolled/changed.
        resolution = mechanic_result.turn_resolution
        if resolution is not None and resolution.waiting_for:
            waiting_character = next((c for c in state.active_characters()
                                      if (c.character_id or f"legacy-user:{c.owner_id}") == resolution.waiting_for), None)
            waiting_combatant = next((c for c in state.combat.order
                                     if resolution.waiting_for in {c.character_id, c.combatant_id}), None)
            mechanic_result.check_status["waiting_for_name"] = (
                waiting_character.name if waiting_character else
                "目前的敵方行動者" if waiting_combatant else "目前行動者"
            )
        # Keep every actor's pending evidence, even though the UI may select a
        # primary next step. Never turn a multi-actor turn into one global flag.
        from app.services.turn_context import current_state

        mechanic_result.check_status["current_turn_state"] = current_state(state)
        message.payload["mechanic_result"] = mechanic_result

        # 4. State Reducer (Pure Python)
        state_reducer.apply_mechanic_result(message, mechanic_result)
    else:
        _logger.info("Routing directly to NarratorAgent (Fast Path)")

    # 5. Narrator Agent generates the final text
    reply_text, private_messages, image_requests = await narrator.run_narrator(message)
    if turn_kind == "opening_fallback" and message.payload.get("narration_failed"):
        # A failed opening produced no scene. Leave /coc start retryable.
        return reply_text, [], []

    # 6. Rule Validator & Guard Agent (Repair Loop) — see
    # docs/specs/enhancement-guard-agent.md for the GUARD_ENABLED switch and
    # the fail-closed fallback this delegates to.
    reply_text = await guard.enforce_narrative_safety(message, reply_text)
    if turn_kind == "resolved_check_followup":
        reply_text = prompt_config.enforce_resolved_check_consistency(
            reply_text, resolved_check_context or {}
        )

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
    # What's left here is just committing this turn's log entries: reload
    # the latest state under the state lock (so this can't clobber
    # whatever the tool calls above already saved), append, save, then sync
    # this function's own `state` object so a caller that keeps using it
    # afterward sees the up-to-date snapshot.
    if state.game_started or turn_kind != "player_action":
        committed = keeper._commit_turn_result(
            state,
            [
                {"role": "user", "content": f"{speaker_role} {display_name}: {text}"},
                {"role": "assistant", "content": reply_text},
            ],
            timeline_id=turn_timeline_id,
            start_game=(turn_kind == "opening_fallback"),
            invalidate_openai_response_chain=True,
        )
        if not committed:
            return "（這次回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）", [], []

    return reply_text, private_messages, image_requests
