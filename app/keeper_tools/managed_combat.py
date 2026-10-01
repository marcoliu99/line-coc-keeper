"""Bot Keeper commands for durable combat; no player-supplied roll outcomes."""
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from app import combat_resources, combat_rules
from app.keeper_tools import resource_bridge

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def _mutate(call: ToolCall, operation):
    from app import keeper

    def mutate(state):
        before = state.to_dict()
        result = operation(state)
        result.setdefault("provisional", resource_bridge.managed(state))
        return keeper.ToolStateMutation(result, should_save=state.to_dict() != before)
    result = keeper.mutate_tool_state(call.state, mutate)
    return public_result(result, include_private=call.speaker_role == 'kp_assistant')


def public_result(result: dict[str, Any], *, include_private: bool = False) -> dict[str, Any]:
    """Nested runner receipts must not reveal enemy HP in ordinary tool output."""
    from app import spoiler_policy
    if include_private or not spoiler_policy.is_privacy_isolation_enabled():
        return result
    from copy import deepcopy

    projected = deepcopy(result)
    def visit(value):
        if isinstance(value, dict):
            for key in ('events', 'roll_receipts', 'pending_checks', 'pending_luck_decisions', 'audit', 'actions', 'effects', 'obligations', 'private_notes', 'legacy_state'):
                value.pop(key, None)
            if value.get('side') == 'enemy' or value.get('is_enemy'):
                for key in ('hp', 'hp_before', 'hp_after', 'hp_max', 'armor', 'armor_used', 'armor_reduction', 'armor_label', 'raw_damage', 'weakness_bonus'):
                    value.pop(key, None)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
    visit(projected)
    return projected


def _battle(state, call):
    if str(call.input.get('combat_id') or '') != state.combat.combat_id:
        raise ValueError('Administrative command requires the current combat_id')
    reason = str(call.input.get('reason') or '').strip()
    if not reason:
        raise ValueError('Explicit controller reason is required')
    return reason


def declare_combat_action(call: ToolCall) -> dict[str, Any]:
    from app import combat, combat_flow

    def operation(state):
        actor = combat.find_combatant(state, call.input['actor_id'])
        if not actor:
            return {'ok': False, 'error': 'Unknown combat actor'}
        if actor.is_pc:
            character = combat.character_for_combatant(state, actor)
            if not character or not call.actor_id or character.owner_id != call.actor_id:
                return {'ok': False, 'error': 'Player action belongs to another investigator'}
        instance = None
        if actor.is_pc:
            character = combat.character_for_combatant(state, actor)
            metadata = resource_bridge.effective(state, character).weapon_instances.get(call.input['weapon_reference'], {})
            if metadata:
                definition_id = metadata.get('definition_id')
                version = metadata.get('catalog_version')
                pin = next((d for d in combat_rules.weapon_catalog() if d.id == definition_id
                            and (not version or d.catalog_version == version)), None)
                if not pin:
                    return {'ok': False, 'phase': 'NEEDS_RULING', 'error': 'Pinned weapon definition unavailable'}
                instance = combat_rules.WeaponInstance(call.input['weapon_reference'], definition_id, pin)
        result = combat_flow.declare_action(
            state, action_id=call.input['action_id'], actor_id=actor.combatant_id,
            target_id=call.input['target_id'], weapon_reference=call.input['weapon_reference'],
            action_kind=call.input.get('action_kind', 'melee'),
            distance_yards=call.input.get('distance_yards'), weapon_instance=instance,
        )
        result['provisional'] = True
        return result
    return _mutate(call, operation)


def run_combat_action(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    return _mutate(call, lambda state: combat_flow.run_action(state, call.input['action_id']))


def submit_combat_choice(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    if not call.actor_id:
        return {'ok': False, 'error': 'Owned combat choice requires player identity'}
    def operation(state):
        pending = state.pending_checks.get(call.actor_id, {})
        resource_bridge.owned_character(state, pending, call.actor_id)
        return combat_flow.submit_choice(state, interaction_id=call.input['interaction_id'],
                                        owner_id=call.actor_id, choice=call.input['choice'])
    return _mutate(call, operation)


def preview_combat_settlement(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    def operation(state):
        preview = combat_resources.get_settlement(state, obligations=combat_flow.postcombat_obligations(state))
        return {'ok': True, 'preview': preview, 'provisional': True}
    return _mutate(call, operation)


def confirm_combat_settlement(call: ToolCall) -> dict[str, Any]:
    def operation(state):
        if not str(call.input.get('reason') or '').strip():
            raise ValueError('Explicit controller reason is required')
        old = state.closed_combat_receipts.get(str(call.input.get('combat_id') or ''), {})
        if old.get('settlement_id') != call.input['settlement_id']:
            _battle(state, call)
        receipt = combat_resources.commit_settlement(state, call.input['settlement_id'])
        return {'ok': True, 'receipt': receipt, 'provisional': False}
    return _mutate(call, operation)


def rollback_combat(call: ToolCall) -> dict[str, Any]:
    def operation(state):
        reason = str(call.input.get('reason') or '').strip()
        if not reason:
            raise ValueError('Explicit controller reason is required')
        old = state.closed_combat_receipts.get(str(call.input.get('combat_id') or ''), {})
        if old.get('rollback_event_id') != call.input['event_id']:
            reason = _battle(state, call)
        receipt = combat_resources.rollback_combat(state, event_id=call.input['event_id'], reason=reason)
        return {'ok': True, 'receipt': receipt, 'rolled_back': True}
    return _mutate(call, operation)


def correct_combat_event(call: ToolCall) -> dict[str, Any]:
    def operation(state):
        reason = _battle(state, call)
        receipt = combat_resources.correct_event(state, call.input['target_event_id'],
            event_id=call.input['event_id'], changes=call.input['changes'], reason=reason)
        return {'ok': True, 'receipt': receipt, 'phase': state.combat.phase, 'provisional': True}
    return _mutate(call, operation)


def reconcile_combat_baseline(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    def operation(state):
        reason = _battle(state, call)
        character = keeper.require_character(state, call.input['investigator'])
        receipt = combat_resources.reconcile_baseline(state, character, event_id=call.input['event_id'],
            decision=call.input['decision'], reason=reason)
        return {'ok': True, 'receipt': receipt, 'provisional': True}
    return _mutate(call, operation)


def change_combat_initiative(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    def operation(state):
        reason = _battle(state, call)
        return combat_flow.set_initiative(state, actor_ids=call.input['order'],
                                            event_id=call.input['event_id'], reason=reason)
    return _mutate(call, operation)


def process_postcombat_obligations(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    return _mutate(call, lambda state: combat_flow.process_postcombat(
        state, logical_round=call.input['logical_round'], event_id=call.input['event_id']))


def get_damage_severity(call: ToolCall) -> dict[str, Any]:
    result = combat_rules.resolve_severity(call.input['severity_id'])
    return {'ok': result.status == 'resolved', 'status': result.status,
            'definition': asdict(result.definition) if result.definition else None, 'error': result.reason,
            'note': 'Explicit severity lookup does not authorize an effect or bypass its CON/defense/stop rules'}


def declare_combat_effect(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    def operation(state):
        reason = _battle(state, call)
        return combat_flow.declare_effect(state, effect_id=call.input['effect_id'],
            target_id=call.input['target_id'], severity_id=call.input['severity_id'],
            scope=call.input.get('scope', 'incident'), reason=reason,
            stop_condition=call.input['stop_condition'], timing=call.input.get('timing', 'round_end'),
            special_rule=call.input.get('special_rule'), defense=call.input.get('defense', 'none'))
    return _mutate(call, operation)


def stop_combat_effect(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    def operation(state):
        reason = _battle(state, call)
        return combat_flow.stop_effect(state, effect_id=call.input['effect_id'],
                                       event_id=call.input['event_id'], reason=reason)
    return _mutate(call, operation)


def close_legacy_combat(call: ToolCall) -> dict[str, Any]:
    from app import combat

    def operation(state):
        reason = str(call.input.get('reason') or '').strip()
        if not reason:
            raise ValueError('Explicit controller reason required for legacy closure')
        return combat.close_legacy_combat(state, event_id=call.input['event_id'], reason=reason)
    return _mutate(call, operation)


def resolve_combat_ruling(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    def operation(state):
        reason = _battle(state, call)
        return combat_flow.resolve_ruling(state, action_id=call.input['action_id'],
            event_id=call.input['event_id'], reason=reason, decision=call.input['decision'],
            weapon_reference=call.input.get('weapon_reference'), distance_yards=call.input.get('distance_yards'))
    return _mutate(call, operation)


def reconcile_combat_correction(call: ToolCall) -> dict[str, Any]:
    from app import combat_flow

    def operation(state):
        reason = _battle(state, call)
        return combat_flow.reconcile_correction(state, event_id=call.input['event_id'], reason=reason,
            injury_by_character=call.input['injury_by_character'],
            acknowledge_action_ids=call.input['acknowledge_action_ids'])
    return _mutate(call, operation)


def get_weapon_definition(call: ToolCall) -> dict[str, Any]:
    """Definition lookup establishes no owned weapon identity or ammunition."""
    lookup = combat_rules.resolve_weapon(call.input['reference'])
    return {'ok': lookup.status == 'resolved', 'status': lookup.status,
            'definition': asdict(lookup.definition) if lookup.definition else None,
            'candidates': [asdict(d) for d in lookup.candidates], 'reason': lookup.reason,
            'note': 'Reviewed type lookup does not establish ownership, ammo or scenario authority'}
