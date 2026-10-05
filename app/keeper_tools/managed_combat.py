"""Bot Keeper commands for durable combat; no player-supplied roll outcomes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from app import combat_rules
from app.keeper_tools import resource_bridge, support
from app.services import combat_actions as act
from app.services import combat_engine

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def _mutate(call: ToolCall, operation):

    def mutate(state):
        before = deepcopy(state.to_dict())
        result = operation(state)
        result.setdefault("provisional", resource_bridge.managed(state))
        return support.ToolStateMutation(result, should_save=state.to_dict() != before)
    result = support.mutate_tool_state(call.state, mutate)
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


def _owned_weapon_evidence(state, character, reference):
    metadata = resource_bridge.effective(state, character).weapon_instances.get(reference, {})
    if not metadata:
        return None, ()
    definition_id = metadata.get('definition_id')
    version = metadata.get('catalog_version')
    scenario_definitions = tuple(combat_rules.parse_weapon_definition(row)
                                 for row in metadata.get('scenario_definitions', ()))
    pin = (combat_rules.parse_weapon_definition(metadata['pinned_definition'])
           if metadata.get('pinned_definition') else next(
               (d for d in combat_rules.weapon_catalog() if d.id == definition_id
                and (not version or d.catalog_version == version)), None))
    if pin and ((definition_id and pin.id != definition_id) or (version and pin.catalog_version != version)):
        raise ValueError('Pinned weapon definition identity/version unavailable')
    if not pin and not scenario_definitions:
        raise ValueError('Pinned weapon definition unavailable')
    instance = combat_rules.WeaponInstance(reference,
                                           definition_id or (pin.id if pin else None), pin)
    return instance, scenario_definitions


def declare_combat_action(call: ToolCall) -> dict[str, Any]:
    from app import combat

    def operation(state):
        actor = combat.find_combatant(state, call.input['actor_id'])
        if not actor:
            return {'ok': False, 'error': 'Unknown combat actor'}
        if actor.is_pc:
            character = combat.character_for_combatant(state, actor)
            if not character or not call.actor_id or character.owner_id != call.actor_id:
                return {'ok': False, 'error': 'Player action belongs to another investigator'}
        instance = None
        scenario_definitions = ()
        if actor.is_pc:
            character = combat.character_for_combatant(state, actor)
            try:
                instance, scenario_definitions = _owned_weapon_evidence(state, character, call.input['weapon_reference'])
            except (ValueError, TypeError, KeyError) as error:
                return {'ok': False, 'phase': 'NEEDS_RULING', 'error': str(error)}
        result = combat_engine.handle(state, act.Declare(
            action_id=call.input['action_id'], actor_id=actor.combatant_id,
            target_id=call.input['target_id'], weapon_reference=call.input['weapon_reference'],
            action_kind=call.input.get('action_kind', 'melee'),
            distance_yards=call.input.get('distance_yards'), weapon_instance=instance,
            scenario_definitions=scenario_definitions,
        ))
        result['provisional'] = True
        return result
    return _mutate(call, operation)


def run_combat_action(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.Run(call.input['action_id'])))


def submit_combat_choice(call: ToolCall) -> dict[str, Any]:
    if not call.actor_id:
        return {'ok': False, 'error': 'Owned combat choice requires player identity'}
    def operation(state):
        retained = combat_engine.handle(state, act.ChoiceReceipt(
            interaction_id=call.input['interaction_id'], owner_id=call.actor_id, choice=call.input['choice'],
        ))
        if retained is not None:
            return retained
        pending = state.pending_checks.get(call.actor_id, {})
        resource_bridge.owned_character(state, pending, call.actor_id)
        return combat_engine.handle(state, act.Choose(
            interaction_id=call.input['interaction_id'], owner_id=call.actor_id, choice=call.input['choice'],
        ))
    return _mutate(call, operation)


def preview_combat_settlement(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.PreviewSettlement()))


def confirm_combat_settlement(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.ConfirmSettlement(
        combat_id=str(call.input.get('combat_id') or ''), settlement_id=call.input['settlement_id'],
        reason=str(call.input.get('reason') or ''),
    )))


def rollback_combat(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.Rollback(
        combat_id=str(call.input.get('combat_id') or ''), event_id=call.input['event_id'],
        reason=str(call.input.get('reason') or ''),
    )))


def correct_combat_event(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.CorrectEvent(
        combat_id=str(call.input.get('combat_id') or ''), target_event_id=call.input['target_event_id'],
        event_id=call.input['event_id'], changes=call.input['changes'],
        reason=str(call.input.get('reason') or ''),
    )))


def reconcile_combat_baseline(call: ToolCall) -> dict[str, Any]:

    def operation(state):
        combat_engine.authorize(state, str(call.input.get('combat_id') or ''), str(call.input.get('reason') or ''))
        character = support.require_character(state, call.input['investigator'])
        return combat_engine.handle(state, act.ReconcileBaseline(
            combat_id=str(call.input.get('combat_id') or ''), character=character,
            event_id=call.input['event_id'], decision=call.input['decision'],
            reason=str(call.input.get('reason') or ''),
        ))
    return _mutate(call, operation)


def change_combat_initiative(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.SetInitiative(
        combat_id=str(call.input.get('combat_id') or ''), actor_ids=call.input['order'],
        event_id=call.input['event_id'], reason=str(call.input.get('reason') or ''),
    )))


def process_postcombat_obligations(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.ProcessPostcombat(
        logical_round=call.input['logical_round'], event_id=call.input['event_id'],
    )))


def get_damage_severity(call: ToolCall) -> dict[str, Any]:
    result = combat_rules.resolve_severity(call.input['severity_id'])
    return {'ok': result.status == 'resolved', 'status': result.status,
            'definition': asdict(result.definition) if result.definition else None, 'error': result.reason,
            'note': 'Explicit severity lookup does not authorize an effect or bypass its CON/defense/stop rules'}


def declare_combat_effect(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.DeclareEffect(
        combat_id=str(call.input.get('combat_id') or ''), effect_id=call.input['effect_id'],
        target_id=call.input['target_id'], severity_id=call.input['severity_id'],
        scope=call.input.get('scope', 'incident'), reason=str(call.input.get('reason') or ''),
        stop_condition=call.input['stop_condition'], timing=call.input.get('timing', 'round_end'),
        special_rule=call.input.get('special_rule'), defense=call.input.get('defense', 'none'),
    )))


def stop_combat_effect(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.StopEffect(
        combat_id=call.input['combat_id'], effect_id=call.input['effect_id'],
        event_id=call.input['event_id'], reason=str(call.input.get('reason') or ''),
    )))


def close_legacy_combat(call: ToolCall) -> dict[str, Any]:
    def operation(state):
        reason = str(call.input.get('reason') or '').strip()
        if not reason:
            raise ValueError('Explicit controller reason required for legacy closure')
        return combat_engine.handle(state, act.CloseLegacy(event_id=call.input['event_id'], reason=reason))
    return _mutate(call, operation)


def resolve_combat_ruling(call: ToolCall) -> dict[str, Any]:
    def operation(state):
        from app import combat
        combat_engine.authorize(state, str(call.input.get('combat_id') or ''), str(call.input.get('reason') or ''))
        action = state.combat.actions.get(call.input['action_id'], {})
        actor = combat.find_combatant(state, action.get('actor_id', ''))
        character = combat.character_for_combatant(state, actor) if actor and actor.is_pc else None
        reference = call.input.get('weapon_reference') or action.get('weapon_reference', '')
        definitions = ()
        instance = None
        if character:
            try:
                instance, definitions = _owned_weapon_evidence(state, character, reference)
                if (instance and instance.pinned_definition
                        and not any(d.id == instance.pinned_definition.id for d in definitions)):
                    definitions += (instance.pinned_definition,)
            except (ValueError, TypeError, KeyError) as error:
                return {'ok': False, 'phase': 'NEEDS_RULING', 'error': str(error)}
        return combat_engine.handle(state, act.Rule(
            combat_id=str(call.input.get('combat_id') or ''), action_id=call.input['action_id'],
            event_id=call.input['event_id'], reason=str(call.input.get('reason') or ''),
            decision=call.input['decision'], weapon_reference=reference,
            distance_yards=call.input.get('distance_yards'), scenario_definitions=definitions,
            weapon_instance=instance,
        ))
    return _mutate(call, operation)


def reconcile_combat_correction(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.ReconcileCorrection(
        combat_id=str(call.input.get('combat_id') or ''), event_id=call.input['event_id'],
        reason=str(call.input.get('reason') or ''),
        injury_by_character=call.input['injury_by_character'],
        acknowledge_action_ids=call.input['acknowledge_action_ids'],
        acknowledge_check_ids=call.input.get('acknowledge_check_ids'),
    )))


def get_weapon_definition(call: ToolCall) -> dict[str, Any]:
    """Definition lookup establishes no owned weapon identity or ammunition."""
    lookup = combat_rules.resolve_weapon(call.input['reference'])
    return {'ok': lookup.status == 'resolved', 'status': lookup.status,
            'definition': asdict(lookup.definition) if lookup.definition else None,
            'candidates': [asdict(d) for d in lookup.candidates], 'reason': lookup.reason,
            'note': 'Reviewed type lookup does not establish ownership, ammo or scenario authority'}


def stabilize_investigator(call: ToolCall) -> dict[str, Any]:
    """Use a recorded successful First Aid check; never accept a supplied outcome."""
    return _mutate(call, lambda state: combat_engine.handle(state, act.Stabilize(
        character_id=call.input['character_id'], source_check_id=call.input['source_check_id'],
        event_id=call.input['event_id'], reason=call.input['reason'],
    )))


def request_stabilization_check(call: ToolCall) -> dict[str, Any]:
    def operation(state):
        healer = state.characters_by_id.get(call.input['healer_character_id'])
        if not healer or not call.actor_id or healer.owner_id != call.actor_id:
            return {'ok': False, 'error': 'First Aid declaration belongs to another investigator'}
        return combat_engine.handle(state, act.RequestStabilization(
            healer_character_id=healer.character_id, character_id=call.input['character_id'],
            event_id=call.input['event_id'], reason=call.input['reason'],
        ))
    return _mutate(call, operation)


def run_enemy_combat_plan(call: ToolCall) -> dict[str, Any]:
    return _mutate(call, lambda state: combat_engine.handle(state, act.RunEnemyPlan(call.input['plan_id'])))


def run_combat_effect(call: ToolCall) -> dict[str, Any]:
    def operation(state):
        if call.input['combat_id'] != state.combat.combat_id:
            return {'ok': False, 'error': 'Effect retry requires its current source battle'}
        return combat_engine.handle(state, act.RunEffect(call.input['effect_id']))
    return _mutate(call, operation)
