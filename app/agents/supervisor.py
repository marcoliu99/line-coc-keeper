from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Literal

from app import keeper, observability
from app.agents import (
    assistant,
    context_builder,
    executor,
    guard,
    intent_router,
    narrator,
    state_reducer,
)
from app.domain.models import AgentMessage, MechanicResult
from app.models import GroupState
from app.providers.codex_provider import with_codex_turn
from app.providers.turn_budget import with_turn_deadline
from app.services import (
    mutation_admission,
    prompt_config,
    reply_segments,
    turn_delivery,
)

_logger = logging.getLogger(__name__)
PlayerTurnKind = Literal["player_action", "resolved_check_followup", "opening_fallback"]


def _unchanged_pending_reply(state: GroupState, user_id: str, result: MechanicResult,
                             before_checks: dict, before_luck: dict, payload: dict) -> str:
    resolution = result.turn_resolution
    if (resolution is None or resolution.validation_code != 'validated'
            or result.check_status.get('tool_event_count') != 0
            or result.check_status.get('state_changed') or result.events
            or payload.get('private_messages') or payload.get('image_requests')):
        return ''
    char = state.get_active_character(user_id)
    if char is None or (resolution.waiting_for or resolution.actor_character_id) != (char.character_id or f'legacy-user:{user_id}'):
        return ''
    if resolution.disposition == 'await_check' and not state.pending_luck_decisions.get(user_id):
        pending = state.pending_checks.get(user_id)
        if (pending and pending == before_checks.get(user_id) and pending.get('timeline_id') == state.timeline_id
                and pending.get('check_id') == resolution.check_id):
            return '上一筆檢定仍在等待你擲骰；請使用檢定按鈕或 /coc check。本次沒有建立新檢定。'
    if resolution.disposition == 'await_luck':
        pending = state.pending_luck_decisions.get(user_id)
        if (pending and pending == before_luck.get(user_id) and pending.get('timeline_id') == state.timeline_id
                and pending.get('decision_id') == resolution.check_id):
            return '上一筆骰子已擲出，仍在等待你的 Luck 決定；請使用 Luck 按鈕，或 /coc luck skip 保留原骰果，不要重擲。'
    return ''


@with_turn_deadline
@with_codex_turn
async def run_turn(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: str,
    conversation_id: str,
    *,
    actor_user_id: str | None = None,
    actor_is_keeper: bool = False,
    turn_kind: PlayerTurnKind = "player_action",
    resolved_check_context: dict[str, Any] | None = None,
) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """
    The main entry point for the Agentic Keeper Supervisor.
    Orchestrates the asynchronous pipeline of Agents to produce a response.
    Returns: (reply_text, private_messages, image_requests)
    """
    mutation_admission.assert_admitted(state.group_id)
    _logger.info(f"Supervisor starting turn for {display_name} ({user_id})")
    # Capture one authoritative timeline before any agent await.  Executor
    # tools may initialize or persist timeline-bound state; without this
    # early capture, a legacy state with no timeline would later fall back to
    # ``legacy-*`` and the canonical log commit could reject the whole turn.
    turn_timeline_id = keeper._ensure_turn_timeline(state)

    from app.services.narrative_corrections import blocking_reply
    correction_block = blocking_reply(state, [text, resolved_location])
    if correction_block:
        return correction_block, [], []

    route = intent_router.route_request(text, speaker_role, turn_kind)
    action_text = route.ic_text if route.message_mode == "mixed" else text
    # Pure OOC never retrieves private scenario/memory or enters the gameplay
    # prompt builder. Role remains server-owned even when text claims otherwise.
    if route.intent == "PLAYER_OOC":
        message = AgentMessage({"state": state, "user_id": user_id,
            "display_name": display_name, "speaker_role": speaker_role,
            "text": text, "route_decision": route, "intent": route.intent,
            "turn_kind": turn_kind, "conversation_id": conversation_id})
        observability.event("turn.route", route="player_ooc", turn_kind=turn_kind)
        reply_text, _, _ = await narrator.run_narrator(message)
        committed = keeper._commit_turn_result(state, [], timeline_id=turn_timeline_id,
            segment_audit=reply_segments.audit(route, text, user_id))
        if not committed:
            return "時間線已更新，這次場外回覆未送出。", [], []
        # No canonical log entries: summary/memory consume only log.
        if route.audience == "player_private":
            return "場外說明已私訊給你。", [(user_id, reply_text)], []
        from app import spoiler_policy
        safe = spoiler_policy.sanitize_public_text(reply_text, spoiler_policy.collect_protected_terms(state))
        return reply_text if safe.is_safe else (safe.fallback_text or reply_segments.UNRESOLVED), [], []

    # 1. Build Context from IC input only.
    message = await context_builder.build_context(
        state=state,
        user_id=user_id,
        display_name=display_name,
        text=action_text,
        resolved_location=resolved_location,
        speaker_role=speaker_role,
        conversation_id=conversation_id,
    )
    message.payload["actor_user_id"] = actor_user_id or user_id
    message.payload["actor_is_keeper"] = actor_is_keeper
    message.payload["route_decision"] = route
    message.payload["turn_kind"] = turn_kind
    if turn_kind == "resolved_check_followup":
        if not resolved_check_context:
            raise ValueError("resolved_check_followup requires an authoritative result")
        message.payload["resolved_check_context"] = resolved_check_context
        from app.agents.tool_gateway import make_tool_executor
        from app.services import movement
        continuation = state.movement_continuations.get(user_id)
        arrival_result = None
        if continuation and continuation.get("check_id") == resolved_check_context.get("check_id"):
            def resume_entry() -> dict:
                return movement.resume(state, user_id, resolved_check_context) or {"ok": False, "error": "no_matching_movement"}
            gateway = make_tool_executor(state, [], [], "player", [],
                observed_outcomes=message.payload.setdefault("observed_outcomes", []), internal_operation=resume_entry)
            arrival_result = await gateway("commit_movement", {})
        if continuation and arrival_result is not None:
            data = dict(continuation["proposal"])
            data["origin"] = tuple(data["origin"])
            move_session = movement.MovementSession(movement.MovementProposal(**data), data["original_span"], continuation["sources"])
            if arrival_result and arrival_result.get("arrival"):
                move_session.arrived = True
                move_session.committed_position = movement.position(state, user_id)
            move_session._final_skill = continuation.get("skill", "")
            message.payload["movement_session"] = move_session
            message.payload["movement_request_text"] = continuation.get("request_text", data["original_span"])
        if arrival_result is not None:
            message.payload["movement_resume_result"] = arrival_result
            if arrival_result.get("arrival"):
                message.payload["resolved_location"] = {"room_name": arrival_result["arrival"]["destination"]}

    # 2. Intent Routing (Fast Path vs Slow Path)
    intent = (
        intent_router.classify_intent(message)
        if turn_kind == "player_action" else turn_kind.upper()
    )
    message.payload["intent"] = intent
    observability.event("turn.route", route=intent.lower(), turn_kind=turn_kind)
    
    _logger.info(f"Intent classified as: {intent}")

    # KP Assistant uses its own provider/tool/Guard/commit path. Its OOC
    # replies enter kp_ooc_log; explicit or tool-created canon enters log.
    # Neither case goes through the player Executor/Narrator pipeline.
    if intent == "OOC_ASSISTANT":
        _logger.info("Routing to AssistantAgent (OOC Path)")
        return await assistant.run_assistant(message)

    # 3. Route to Executor (Slow Path) or Skip to Narrator (Fast Path)
    mechanic_result: MechanicResult | None = None
    pending_reply = ''
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
                    "player_declaration", "action_basis", "opposed",
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
                    "player_declaration", "action_basis", "opposed",
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
        pending_reply = _unchanged_pending_reply(state, user_id, mechanic_result,
                                                pending_checks_before, pending_luck_before, message.payload)
    else:
        _logger.info("Routing directly to NarratorAgent (Fast Path)")

    # 5. Narrator Agent generates the final text
    if pending_reply:
        reply_text = pending_reply
        private_messages: list[tuple[str, str]] = []
        image_requests: list[tuple[str | None, int]] = []
        observability.event('narrator.pending_reused', status='skipped')
    else:
        reply_text, private_messages, image_requests = await narrator.run_narrator(message)
    if turn_kind == "opening_fallback" and message.payload.get("narration_failed"):
        # A failed opening produced no scene. Leave /coc start retryable.
        return reply_text, [], []

    # Consistency precedes Guard; any Guard rewrite is checked again. The
    # deterministic delivery contract is the final writer and safety boundary.
    public_result = turn_delivery.public_mechanic(mechanic_result, state)

    def consistent(candidate: str) -> str:
        if turn_kind == "resolved_check_followup":
            candidate = prompt_config.enforce_resolved_check_consistency(candidate, resolved_check_context or {})
        if public_result is not None:
            candidate = prompt_config.enforce_mechanic_check_consistency(candidate, public_result)
        return candidate

    ooc_reply = ""
    if route.message_mode == "mixed":
        segments = reply_segments.project(reply_text, route, user_id, {
            o.evidence_ref for o in message.payload.get("observed_outcomes", [])
            if o.success and o.audience == "public"
        })
        reply_text, ooc_reply = segments.canonical, segments.public_ooc
        message.payload["segments_valid"] = segments.valid
        for span in route.spans:
            if span.audience == "player_private":
                # Do not feed private context into the public mixed generation.
                private_messages.append((user_id, "你的角色資料：\n" + reply_segments.self_context(state, user_id)))
    reply_text = consistent(reply_text)
    reply_text = await guard.enforce_narrative_safety(message, reply_text)
    reply_text = consistent(reply_text)
    reply_text, private_controls = turn_delivery.finalize(message, reply_text)
    private_messages.extend(item for item in private_controls if item not in private_messages)

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
                {"role": "user", "content": f"{speaker_role} {display_name}: {action_text}"},
                {"role": "assistant", "content": reply_text},
            ],
            timeline_id=turn_timeline_id,
            start_game=(turn_kind == "opening_fallback"),
            invalidate_openai_response_chain=True,
            segment_audit=reply_segments.audit(route, text, user_id) if route.message_mode == "mixed" else None,
        )
        if not committed:
            return "（這次回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）", [], []

    if ooc_reply:
        # OOC never participates in the canonical commit above.
        from app import spoiler_policy
        if spoiler_policy.sanitize_public_text(ooc_reply, spoiler_policy.collect_protected_terms(state)).is_safe:
            reply_text += "\n\n【場外】" + ooc_reply
        else:
            reply_text += "\n\n【場外】" + reply_segments.UNRESOLVED
    return reply_text, private_messages, image_requests
