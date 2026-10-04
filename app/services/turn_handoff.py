"""Prepare Narrator facts from validated resolution and authoritative state."""
from __future__ import annotations

from typing import Any

from app.domain.models import MechanicResult, TurnPayload
from app.models import GroupState
from app.services.turn_context import current_state


def owner_for_character(state: GroupState, character_id: str) -> str | None:
    """Resolve an active character identity without accepting a model-supplied owner."""
    return next((character.owner_id for character in state.active_characters()
                 if (character.character_id or f"legacy-user:{character.owner_id}") == character_id), None)


def _unchanged_pending_reply(state: GroupState, user_id: str, result: MechanicResult,
                             before_checks: dict, before_luck: dict, payload: TurnPayload) -> str:
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


def prepare_narrator_handoff(
    state: GroupState, user_id: str, mechanic_result: MechanicResult,
    pending_checks_before: dict, pending_luck_before: dict, payload: TurnPayload,
) -> str:
    """Select live per-Investigator waiting facts; never replay or mutate tools."""
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
    if (resolution and resolution.validation_code == "validated"
            and resolution.disposition in {"await_check", "await_luck"}):
        target_id = resolution.waiting_for or resolution.actor_character_id
        referenced_owner = owner_for_character(state, target_id)
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
        pending_details: dict[str, Any] = {
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
    if resolution is not None and resolution.validation_code == "validated" and resolution.waiting_for:
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
    mechanic_result.check_status["current_turn_state"] = current_state(state)
    return _unchanged_pending_reply(
        state, user_id, mechanic_result, pending_checks_before, pending_luck_before, payload,
    )
