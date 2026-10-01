"""Effective resource access for transport handlers; rule modules own mechanics."""
from __future__ import annotations

from typing import Any

from app import combat_resources
from app.models import Character, GroupState


def managed(state: GroupState) -> bool:
    return state.combat.active and state.combat.pipeline_version == combat_resources.PIPELINE_VERSION


def participating(state: GroupState, character: Character) -> bool:
    return managed(state) and character.character_id in state.combat.working_resources


def effective(state: GroupState, character: Character) -> Character:
    # Legacy reads remain inspectable; their missing baseline never authorizes a
    # resource mutation or an automatic migration.
    return combat_resources.effective_character(state, character) if managed(state) else character


def mutation_id(tool_name: str, tool_input: dict[str, Any]) -> str:
    explicit = str(tool_input.get('event_id') or '').strip()
    if explicit:
        return explicit
    raise ValueError('Managed resource mutation requires an explicit stable event_id')


def guard_replacement(state: GroupState) -> str | None:
    if state.combat.active:
        return '戰鬥尚未結算；請先由 Keeper 結算或明確回滾，再更換角色或重設遊戲。'
    if any(o.get('status') != 'resolved' for o in state.postcombat_obligations):
        return '仍有戰鬥後待履行事項；請先處理或明確裁定，再重設遊戲或更換角色。'
    return None


def reconcile(state: GroupState, character: Character, *, event_id: str, reason: str = '') -> None:
    if participating(state, character):
        combat_resources.reconcile_effective_character(state, character, event_id=event_id, reason=reason)


def owned_character(state: GroupState, pending: dict[str, Any], owner_id: str) -> Character:
    """Reject a changed active character binding before any pending roll/Luck."""
    timeline = state.timeline_id or f'legacy-{state.group_id}'
    if pending.get('timeline_id') and pending['timeline_id'] != timeline:
        raise ValueError('The owned control belongs to an expired timeline')
    active = state.get_active_character(owner_id)
    expected_id = ''
    context = pending.get('combat_context')
    continuing = pending.get('postcombat_context')
    if context:
        action = state.combat.actions.get(context.get('action_id', ''), {})
        expected_id = action.get('character_id', '')
        if context.get('check_role') == 'medical':
            expected_id = action.get('medical_context', {}).get('healer_character_id', '')
        if not expected_id:
            participant_id = action.get('actor_id') if context.get('check_role') == 'attack' else action.get('target_id')
            participant = next((p for p in state.combat.order if p.combatant_id == participant_id), None)
            expected_id = participant.character_id if participant else ''
    elif pending.get('medical_context'):
        expected_id = pending['medical_context'].get('healer_character_id', '')
    elif continuing:
        obligation: dict[str, Any] = next((dict(o) for o in state.postcombat_obligations
                           if o.get('obligation_id') == continuing.get('obligation_id')), {})
        expected_id = obligation.get('character_id', '')
    if not active or not expected_id or active.character_id != expected_id:
        raise ValueError('The owned check investigator binding changed; explicit reconciliation required')
    return effective(state, active)


def record_control_receipt(state, pending, owner_id, character, result, *, pending_luck=False, choice=None):
    context = pending.get('combat_context', {})
    if not context or context.get('combat_id') != state.combat.combat_id:
        return
    action = state.combat.actions.get(context.get('action_id', ''))
    if action is None:
        return
    receipt = {'combat_id': state.combat.combat_id, 'timeline_id': pending.get('timeline_id'),
               'owner_id': owner_id, 'character_id': character.character_id,
               'check_id': pending['check_id'], 'decision_id': pending.get('decision_id', ''),
               'skill': pending.get('skill_name') or pending.get('skill', ''),
               'skill_value': result.skill_value, 'roll': result.roll, 'tier': result.tier,
               'pending_luck': pending_luck, 'choice': choice,
               'visibility': pending.get('visibility', 'public')}
    cache = action.setdefault('control_delivery_receipts', {})
    cache[pending['check_id']] = receipt
    if pending.get('decision_id') and not pending_luck:
        cache[pending['decision_id']] = dict(receipt)


def record_choice_control_receipt(state, pending, owner_id, option, reply_text):
    context = pending['combat_context']
    action = state.combat.actions[context['action_id']]
    character = state.get_active_character(owner_id)
    assert character is not None
    index = pending['options'].index(option)
    action.setdefault('control_delivery_receipts', {})[pending['check_id']] = {
        'kind': 'choice', 'combat_id': state.combat.combat_id,
        'timeline_id': pending.get('timeline_id'), 'owner_id': owner_id,
        'character_id': character.character_id, 'reply_text': reply_text,
        'options': [f'#{index}', option['label'], option['skill'], option['kind']],
    }


def control_receipt(state: GroupState, owner_id: str, identity: str, *, choice=None, check_option=None) -> dict | None:
    """Read only a retained same-battle control; a new battle never replays it."""
    if not identity or not state.combat.combat_id:
        return None
    closed = state.closed_combat_receipts.get(state.combat.combat_id, {})
    if closed.get('status') == 'rolled_back':
        return None
    active = state.get_active_character(owner_id)
    timeline = state.timeline_id or f'legacy-{state.group_id}'
    for action in state.combat.actions.values():
        receipt = action.get('control_delivery_receipts', {}).get(identity)
        if (receipt and receipt.get('owner_id') == owner_id and receipt.get('timeline_id') == timeline
                and receipt.get('combat_id') == state.combat.combat_id and active
                and receipt.get('character_id') == active.character_id
                and (choice is None or receipt.get('choice') == choice)
                and (receipt.get('kind') != 'choice' or check_option in receipt['options'])):
            return dict(receipt)
    return None
