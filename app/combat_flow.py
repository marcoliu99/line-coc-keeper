"""Transport-free, receipt-backed combat actions and logical-time obligations.

All entry points run inside the existing group mutation/save transaction.
Authoritative check callbacks are server-only; tools never accept die results.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Literal

from app import check_lifecycle, combat, combat_resources, combat_rules, dice, luck
from app.models import (
    Character,
    CombatAction,
    Combatant,
    CombatCheckContext,
    CombatCheckIdentity,
    CombatCheckRole,
    GroupState,
    InjuryState,
    PostcombatObligation,
    damage_bonus_and_build,
)

ActionKind = Literal['melee', 'single_shot']
_SKILLS = {'fighting-brawl': '格鬥（鬥毆）', 'fighting-axe': '格鬥（斧）',
           'fighting-sword': '格鬥（劍）', 'firearms-handgun': '射擊（手槍）',
           'firearms-rifle-shotgun': '射擊（步槍/霰彈槍）', 'firearms-bow': '射擊（弓）',
           'throw': '投擲'}


def _error(message: str) -> dict[str, Any]:
    return {'ok': False, 'error': message}


def _ruling(state: GroupState, action: CombatAction, reason: str) -> dict[str, Any]:
    state.combat.phase = 'NEEDS_RULING'
    action['needs_ruling'] = reason
    return {'ok': False, 'phase': 'NEEDS_RULING', 'action_id': action['action_id'], 'error': reason}


def _result(state: GroupState, action: CombatAction) -> dict[str, Any]:
    return deepcopy({'ok': True, 'combat_id': state.combat.combat_id, 'action_id': action['action_id'],
                     'phase': state.combat.phase, 'completed': action.get('completed', False),
                     'interaction': state.combat.interaction, 'result': action.get('result', {})})


def _character(state: GroupState, participant_id: str) -> Character | None:
    participant = combat.find_combatant(state, participant_id)
    return combat.character_for_combatant(state, participant) if participant and participant.is_pc else None


def _skill(character: Character, skill_id: str) -> tuple[str, int] | None:
    label = _SKILLS.get(skill_id, skill_id)
    for candidate in (label, skill_id):
        if candidate in character.skills:
            return label, character.skills[candidate]
    return None


def _roll(state: GroupState, action: CombatAction, role: CombatCheckRole, value: int,
          *, penalty: int = 0, difficulty: str = 'regular', character_id: str = '') -> dict[str, Any]:
    return combat_resources.record_roll(
        state, f"{state.combat.combat_id}:{action['action_id']}:{CombatCheckIdentity(role, character_id).serialized_role}",
        lambda: asdict(dice.skill_check(value, penalty_dice=penalty, required_tier=difficulty)),
    )


def _context(state: GroupState, action_id: str, role: CombatCheckRole,
             *, character_id: str = '') -> CombatCheckContext:
    check = CombatCheckIdentity(role, character_id)
    return CombatCheckContext(state.combat.combat_id, action_id,
        f'{state.combat.combat_id}:{action_id}:{check.serialized_role}', check)


def validate_pending_context(state: GroupState, pending: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Validate before consuming a control, rolling, or spending Luck."""
    context = pending.get('combat_context')
    medical = pending.get('medical_context')
    if medical:
        healer = state.characters_by_id.get(medical.get('healer_character_id', ''))
        patient = state.characters_by_id.get(medical.get('character_id', ''))
        current_ids = sorted(o['obligation_id'] for o in _all_obligations(state)
                             if o.get('character_id') == medical.get('character_id')
                             and o.get('kind') == 'dying' and o.get('status') != 'resolved')
        if (not healer or healer.owner_id != owner_id or not patient or not current_ids
                or current_ids != medical.get('obligation_ids')
                or not combat_resources.effective_character(state, patient).injury.get('dying')):
            return _error('Medical control is not bound to the current healer/patient injury')
    obligation_context = pending.get('postcombat_context')
    if obligation_context:
        obligation = next((o for o in _all_obligations(state)
                           if o['obligation_id'] == obligation_context.get('obligation_id')), None)
        if (not obligation or obligation.get('status') != 'pending'
                or obligation.get('next_trigger', {}).get('round') != obligation_context.get('round')):
            return _error('Stale continuing obligation check')
        character = state.characters_by_id.get(obligation.get('character_id', ''))
        if not character or character.owner_id != owner_id or pending.get('check_id') != obligation.get('next_trigger', {}).get('check_id'):
            return _error('Continuing obligation control belongs to another owner/check')
        return {'ok': True}
    if not context:
        return {'ok': True}
    wait = state.combat.interaction
    if (not combat_resources.is_managed(state) or context.get('combat_id') != state.combat.combat_id
            or any(context.get(k) != wait.get(k) for k in ('action_id', 'interaction_id', 'check_role'))
            or owner_id != wait.get('owner_id') or pending.get('check_id') != wait.get('check_id')):
        return _error('Stale or foreign combat control')
    action = state.combat.actions.get(context.get('action_id', ''), {})
    try:
        role = CombatCheckIdentity.from_serialized(context.get('check_role', '')).role
    except (TypeError, ValueError):
        return _error('Unknown combat check role')
    participant_id = action.get('actor_id') if role in ('attack', 'medical') else action.get('target_id')
    participant = combat.find_combatant(state, participant_id or '')
    if participant is not None and participant.defeated and role != 'injury':
        return _error('The original action participant is now incapacitated; explicit reconciliation required')
    if state.combat.phase not in {'PLAYER_CHOICE', 'PLAYER_ROLL', 'INJURY_CHECK', 'LUCK_DECISION'}:
        return _error('Combat is not waiting for this control')
    return {'ok': True}


def roll_pending_check(state: GroupState, pending: dict[str, Any], owner_id: str) -> dice.SkillCheckResult:
    """Server roll seam, lazy durable receipt; never called with client die values."""
    validation = validate_pending_context(state, pending, owner_id)
    if not validation['ok']:
        raise ValueError(validation['error'])
    if pending.get('postcombat_context'):
        context = pending['postcombat_context']
        obligation = next(o for o in _all_obligations(state) if o['obligation_id'] == context['obligation_id'])
        identity = f"{context['obligation_id']}:{context['round']}:con"
        receipts = obligation.setdefault('roll_receipts', {})
        if identity not in receipts:
            receipts[identity] = asdict(dice.skill_check(int(pending['skill_value'])))
        parent_id = obligation.get('effect', {}).get('parent_obligation_id')
        if parent_id:
            parent = next(o for o in _all_obligations(state) if o['obligation_id'] == parent_id)
            parent.setdefault('roll_receipts', {})[identity] = deepcopy(receipts[identity])
        return dice.SkillCheckResult(**receipts[identity])
    if pending.get('medical_context') and not pending.get('combat_context'):
        receipt = pending.get('medical_roll_receipt')
        if receipt is None:
            receipt = asdict(dice.skill_check(int(pending['skill_value'])))
            pending['medical_roll_receipt'] = receipt
            owned = state.pending_checks.get(owner_id)
            if owned and owned.get('check_id') == pending.get('check_id'):
                owned['medical_roll_receipt'] = deepcopy(receipt)
        return dice.SkillCheckResult(**receipt)
    context = pending['combat_context']
    action = state.combat.actions[context['action_id']]
    parent_id = action.get('parent_obligation_id')
    cache_key = action.get('parent_con_receipt_id')
    parent_obligation = next((o for o in _all_obligations(state) if o['obligation_id'] == parent_id), None) if parent_id else None
    cached_con = parent_obligation.get('roll_receipts', {}).get(cache_key or '') if parent_obligation else None
    if cached_con is not None:
        result = combat_resources.record_roll(state, f"{state.combat.combat_id}:{action['action_id']}:{context['check_role']}", cached_con)
        return dice.SkillCheckResult(**result)
    check_identity = CombatCheckIdentity.from_serialized(context['check_role'])
    result = _roll(state, action, check_identity.role, int(pending['skill_value']),
                   penalty=int(pending.get('penalty_dice', 0)), difficulty=pending.get('difficulty', 'regular'),
                   character_id=check_identity.character_id)
    if parent_obligation and cache_key:
        parent_obligation.setdefault('roll_receipts', {})[cache_key] = deepcopy(result)
    return dice.SkillCheckResult(**result)


def _request_check(state: GroupState, action: CombatAction, role: CombatCheckRole, character: Character,
                   skill: str, value: int, *, penalty: int = 0, injury: bool = False) -> dict[str, Any]:
    candidate: dict[str, Any] = {'type': 'skill', 'skill': skill, 'skill_value': value, 'bonus_dice': 0,
                 'penalty_dice': penalty, 'difficulty': action.get('difficulty', 'regular') if role == 'attack' else 'regular',
                 'combat_context': _context(state, action['action_id'], role).to_dict(),
                 'major_wound_trigger': injury, 'allow_luck': not injury}
    if action.get('medical_context'):
        candidate['medical_context'] = deepcopy(action['medical_context'])
    registration = check_lifecycle.register(state, character.owner_id, candidate)
    if registration.status != 'admitted':
        return _error(f'Required check ownership blocked: {registration.blocker}')
    assert registration.pending is not None
    state.combat.interaction = {**candidate['combat_context'], 'owner_id': character.owner_id,
                               'check_id': registration.check_id}
    state.combat.phase = 'INJURY_CHECK' if injury else 'PLAYER_ROLL'
    if state.autoroll_checks:
        result = roll_pending_check(state, registration.pending, character.owner_id)
        state.pending_checks.pop(character.owner_id, None)
        available = combat_resources.effective_character(state, character).luck
        options = [] if injury else luck.buyable_options(value, result.roll, result.tier, available, result.required_tier)
        if options:
            pending = {**registration.pending, 'decision_id': registration.check_id + ':luck',
                       'original_tier': result.tier, 'value': value, 'roll': result.roll,
                       'skill_name': skill, 'display_label': None, 'is_counter': role == 'counter',
                       'attacker_tier': None, 'ranged_attacker': None,
                       'options': [{'tier': o.tier, 'cost': o.cost} for o in options]}
            state.pending_luck_decisions[character.owner_id] = pending
            return on_authoritative_check_result(state, pending_entry=pending, owner_id=character.owner_id,
                                                 result=result, final=False)
        return on_authoritative_check_result(state, pending_entry=registration.pending, owner_id=character.owner_id,
                                             result=result)
    return _result(state, action)


def _weapon_actor_evidence(
    state: GroupState, actor: Combatant, weapon: combat_rules.WeaponDefinition,
    reference: str, instance: combat_rules.WeaponInstance | None = None,
) -> CombatAction:
    """Bind declaration and resumed rulings to the same owned source evidence."""
    character = _character(state, actor.combatant_id)
    if character:
        effective = combat_resources.effective_character(state, character)
        inventory_key = instance.instance_id if instance else reference
        metadata = effective.weapon_instances.get(inventory_key)
        if weapon.id != 'i.weapon.brawl':
            if inventory_key not in effective.weapons and not metadata:
                raise ValueError('Weapon requires an existing owned instance or inventory mapping')
            if metadata and metadata.get('definition_id') not in (None, weapon.id):
                raise ValueError('Owned weapon definition does not match the declared weapon')
        skill = _skill(effective, weapon.skill_id)
        if skill is None:
            raise ValueError('Weapon skill has no authoritative investigator value')
        result: CombatAction = {'skill': skill[0], 'skill_value': skill[1], 'db': effective.damage_bonus}
        if weapon.ammo_per_attack:
            if effective.weapons.get(inventory_key, {}).get('ammo', 0) < weapon.ammo_per_attack:
                raise ValueError('Owned ammunition mapping missing or insufficient')
            result['ammo_key'] = inventory_key
        return result
    card = state.combat.enemy_cards.get(actor.enemy_card_id)
    if not card or card.incomplete or not all(card.source.get(k) for k in ('url', 'revision', 'sha256')):
        raise ValueError('NPC declaration requires a complete reviewed source card')
    attack = next((entry for entry in card.attacks if entry.id == weapon.id), None)
    if attack is None:
        raise ValueError('NPC weapon requires an explicit mapped reviewed attack')
    db = '0'
    if weapon.db_policy != 'none':
        raw_db = card.source.get('damage_bonus')
        if raw_db is None and all(k in card.stats for k in ('STR', 'SIZ')):
            raw_db = damage_bonus_and_build(card.stats['STR'], card.stats['SIZ'])[0]
        if not isinstance(raw_db, str):
            raise ValueError('NPC damage bonus needs verified DB or STR/SIZ')
        db = raw_db
    if weapon.ammo_per_attack and (attack.ammo_or_uses is None or attack.ammo_or_uses < weapon.ammo_per_attack):
        raise ValueError('NPC owned ammunition missing or insufficient')
    return {'skill': attack.skill_name, 'skill_value': attack.skill_value, 'db': db,
            'npc_attack_id': attack.id}


def declare_action(
    state: GroupState, *, action_id: str, actor_id: str, target_id: str,
    weapon_reference: str, action_kind: ActionKind = 'melee', distance_yards: float | None = None,
    scenario_definitions: tuple[combat_rules.WeaponDefinition, ...] = (),
    weapon_instance: combat_rules.WeaponInstance | None = None,
) -> dict[str, Any]:
    combat_resources.initialize_working_state(state)
    if action_id in state.combat.actions:
        action = state.combat.actions[action_id]
        if any(action.get(k) != v for k, v in {'actor_id': actor_id, 'target_id': target_id,
                                              'weapon_reference': weapon_reference, 'action_kind': action_kind}.items()):
            return _error('Action identity cannot be reused for a different declaration')
        return deepcopy(action.get('receipt', _result(state, action)))
    if not action_id or len(action_id) > 160 or action_id.startswith('system:'):
        return _error('A bounded stable action ID is required')
    if (state.combat.interaction or state.combat.phase not in {'READY', 'RESOLVE'}
            or any(not a.get('completed') for a in state.combat.actions.values())):
        return _error('Resolve the current action before declaring another')
    actor = combat.find_combatant(state, actor_id)
    target = combat.find_combatant(state, target_id)
    current = state.combat.order[state.combat.current_index] if state.combat.order else None
    if actor is None or current is None or actor.combatant_id != current.combatant_id or actor.defeated:
        return _error('Only the current capable actor can declare an action')
    if any(a.get('actor_id') == actor.combatant_id and a.get('completed') and a.get('round') == state.combat.round_number
           for a in state.combat.actions.values()):
        return _error('Current actor already completed this turn; advance initiative')
    if target is None or target.defeated or actor is target:
        return _error('Invalid attack target')
    action = {'action_id': action_id, 'actor_id': actor.combatant_id, 'target_id': target.combatant_id,
              'weapon_reference': weapon_reference, 'action_kind': action_kind, 'stage': 'attack',
              'completed': False, 'checks': {}, 'round': state.combat.round_number,
              'mechanical_round': state.mechanical_round, 'distance_yards': distance_yards}
    state.combat.actions[action_id] = action
    lookup = combat_rules.resolve_weapon(weapon_reference, scenario_definitions=scenario_definitions, instance=weapon_instance)
    if lookup.definition is None:
        return _ruling(state, action, lookup.reason)
    weapon = lookup.definition
    if action_kind not in ('melee', 'single_shot') or weapon.attack_mode != action_kind:
        return _ruling(state, action, 'Unsupported attack mode')
    damage = combat_rules.resolve_weapon_damage(weapon, distance_yards=distance_yards)
    if damage.damage is None:
        return _ruling(state, action, damage.reason)
    difficulty = 'regular'
    if action_kind == 'single_shot':
        range_result = combat_rules.resolve_range_difficulty(distance_yards, weapon.base_range_yards)
        if range_result.difficulty is None:
            return _ruling(state, action, range_result.reason)
        difficulty = range_result.difficulty
    try:
        action.update(_weapon_actor_evidence(state, actor, weapon, weapon_reference, weapon_instance))
    except ValueError as exc:
        return _ruling(state, action, str(exc))
    try:
        dice.max_expression_value(damage.damage)
        dice.max_expression_value(action.get('db', '0'))
    except ValueError as exc:
        return _ruling(state, action, str(exc))
    action.update({'weapon': asdict(weapon), 'damage': damage.damage, 'difficulty': difficulty})
    combat_resources.record_event(state, action_id + ':declaration', 'action', data=deepcopy(action))
    return run_action(state, action_id)


def _defense_choice(state: GroupState, action: CombatAction, character: Character) -> dict[str, Any]:
    if check_lifecycle.blocker(state, character.owner_id):
        return _error('Defender has an existing check or Luck decision')
    ranged = action['action_kind'] == 'single_shot'
    dodge = character.skills.get('閃避', character.dex // 2)
    options = ([{'kind': 'dive', 'label': '閃躲', 'skill': '閃避', 'skill_value': dodge,
                 'bonus_dice': 0, 'penalty_dice': 0},
                {'kind': 'no_defense', 'label': '不閃躲', 'skill': '', 'skill_value': 0,
                 'bonus_dice': 0, 'penalty_dice': 0}]
               if ranged else
               [{'kind': 'dodge', 'label': '閃避', 'skill': '閃避', 'skill_value': dodge,
                 'bonus_dice': 0, 'penalty_dice': 0},
                {'kind': 'counter', 'label': '反擊', 'skill': '格鬥（鬥毆）',
                 'skill_value': character.skills.get('格鬥（鬥毆）', 25), 'bonus_dice': 0, 'penalty_dice': 0}])
    candidate: dict[str, Any] = {'type': 'choice', 'options': options,
                 'combat_context': _context(state, action['action_id'], 'defense_choice').to_dict()}
    registered = check_lifecycle.register(state, character.owner_id, candidate)
    if registered.pending is None:
        return _error('Cannot register defender choice')
    state.combat.interaction = {**candidate['combat_context'], 'owner_id': character.owner_id,
                               'check_id': registered.check_id, 'options': options}
    state.combat.phase = 'PLAYER_CHOICE'
    return _result(state, action)


def choice_receipt(state: GroupState, *, interaction_id: str, owner_id: str, choice: str) -> dict[str, Any] | None:
    """Replay only the exact owned input from the retained battle/timeline."""
    character = state.get_active_character(owner_id)
    if not character or state.closed_combat_receipts.get(state.combat.combat_id, {}).get('status') == 'rolled_back':
        return None
    timeline = state.timeline_id or f'legacy-{state.group_id}'
    for action in state.combat.actions.values():
        receipt = action.get('choice_receipts', {}).get(interaction_id)
        if receipt and (receipt['combat_id'], receipt['timeline_id'], receipt['owner_id'],
                        receipt['character_id'], receipt['choice']) == (
                state.combat.combat_id, timeline, owner_id, character.character_id, choice):
            return deepcopy(receipt['result'])
    return None


def submit_choice(state: GroupState, *, interaction_id: str, owner_id: str, choice: str) -> dict[str, Any]:
    retained = choice_receipt(state, interaction_id=interaction_id, owner_id=owner_id, choice=choice)
    if retained is not None:
        return retained
    pending = state.pending_checks.get(owner_id, {})
    validation = validate_pending_context(state, pending, owner_id)
    wait = state.combat.interaction
    if not validation['ok'] or not pending.get('combat_context') or interaction_id != wait.get('interaction_id'):
        return _error('Stale or foreign combat choice')
    if wait.get('check_role') != 'defense_choice':
        return _error('This interaction is not a defense choice')
    option = next((o for o in pending['options'] if choice in (o['kind'], o['label'])), None)
    if option is None:
        return _error('Unsupported defense choice')
    action = state.combat.actions[wait['action_id']]
    character = _character(state, action['target_id'])
    assert character is not None
    state.pending_checks.pop(owner_id)
    state.combat.interaction = {}
    action['defense_kind'] = option['kind']
    action['stage'] = 'defense'
    if option['kind'] == 'no_defense':
        action['checks']['defense'] = {'tier': 'fail', 'success': False}
        response = run_action(state, action['action_id'])
    else:
        response = _request_check(state, action, 'defense', character, option['skill'], option['skill_value'])
    action.setdefault('choice_receipts', {})[interaction_id] = {
        'combat_id': state.combat.combat_id, 'timeline_id': state.timeline_id or f'legacy-{state.group_id}',
        'owner_id': owner_id, 'character_id': character.character_id, 'choice': choice,
        'result': deepcopy(response),
    }
    return response


def on_authoritative_check_result(
    state: GroupState, *, pending_entry: dict[str, Any], owner_id: str,
    result: dice.SkillCheckResult, final: bool = True,
) -> dict[str, Any]:
    validation = validate_pending_context(state, pending_entry, owner_id)
    if not validation['ok']:
        context = pending_entry.get('combat_context', {})
        action = state.combat.actions.get(context.get('action_id', ''), {})
        if action.get('consumed_checks', {}).get(str(pending_entry.get('check_id') or '')):
            return deepcopy(action.get('receipt', _result(state, action)))
        return validation
    if pending_entry.get('postcombat_context'):
        if not final:
            return _error('Continuing injury checks do not permit a Luck wait')
        return _finish_obligation_check(state, pending_entry, owner_id, result)
    context = pending_entry.get('combat_context')
    if not context:
        return {'ok': True, 'managed': False}
    action = state.combat.actions[context['action_id']]
    check_identity = CombatCheckIdentity.from_serialized(context['check_role'])
    role = check_identity.role
    combat_resources.record_roll(state, f"{state.combat.combat_id}:{action['action_id']}:{check_identity.serialized_role}", asdict(result))
    if not final:
        action.setdefault('raw_checks', {})[check_identity.serialized_role] = asdict(result)
        state.combat.phase = 'LUCK_DECISION'
        return _result(state, action)
    action['checks'][check_identity.serialized_role] = asdict(result)
    action.setdefault('consumed_checks', {})[pending_entry['check_id']] = asdict(result)
    state.combat.interaction = {}
    state.combat.phase = 'RESOLVE'
    if role == 'medical':
        action['medical_receipt'] = {'check_id': pending_entry['check_id'], 'timeline_id': state.timeline_id,
            'skill': '急救', 'success': result.success, 'roll': result.roll, 'tier': result.tier,
            'medical_context': deepcopy(pending_entry['medical_context'])}
        return _complete(state, action, deepcopy(action['medical_receipt']))
    if role == 'injury':
        character = state.characters_by_id[action['character_id']]
        effective = combat_resources.effective_character(state, character)
        if not result.success:
            effective.injury['unconscious'] = True
            for tag in ('昏迷', '倒地'):
                if tag not in effective.status_tags:
                    effective.status_tags.append(tag)
        combat_resources.reconcile_effective_character(state, effective,
                                                      event_id=action['action_id'] + ':con', reason='Major wound CON')
        participant = next(p for p in state.combat.order if p.character_id == character.character_id)
        participant.defeated = participant.hp == 0 or bool(effective.injury.get('unconscious'))
        if action.get('injury_queue'):
            action['injury_queue'].pop(0)
            if action['injury_queue']:
                return _next_injury_wait(state, action)
        action['completed'] = True
        parent = action.get('parent_action_id')
        if parent and parent in state.combat.actions:
            return run_action(state, parent)
        state.combat.phase = 'READY'
        return _result(state, action)
    return run_action(state, action['action_id'])


def run_action(state: GroupState, action_id: str, *, transition_budget: int = 16) -> dict[str, Any]:
    combat_resources.initialize_working_state(state)
    action = state.combat.actions.get(action_id)
    if not action or action_id.startswith('system:'):
        return _error('Unknown declared action')
    if action.get('completed'):
        return deepcopy(action.get('receipt', _result(state, action)))
    if action.get('needs_ruling'):
        return _ruling(state, action, action['needs_ruling'])
    if state.combat.interaction:
        if state.combat.interaction.get('action_id') == action_id:
            return _result(state, action)
        return _error('Another interaction is unresolved')
    if transition_budget < 1 or transition_budget > 64:
        return _error('Transition budget must be 1..64')
    actor = combat.find_combatant(state, action['actor_id'])
    target = combat.find_combatant(state, action['target_id'])
    if not actor or not target or state.combat.order[state.combat.current_index].combatant_id != actor.combatant_id:
        return _error('Action actor is no longer current')
    ranged = action['action_kind'] == 'single_shot'
    if 'attack' not in action['checks'] and not ranged:
        pc = _character(state, actor.combatant_id)
        if pc:
            return _request_check(state, action, 'attack', pc, action['skill'], action['skill_value'])
        action['checks']['attack'] = _roll(state, action, 'attack', action['skill_value'])
    if 'defense' not in action['checks']:
        defender = _character(state, target.combatant_id)
        if defender:
            return _defense_choice(state, action, defender)
        card = state.combat.enemy_cards.get(target.enemy_card_id)
        if not card:
            return _ruling(state, action, 'NPC defense requires a reviewed combat card')
        dodge = next((card.skills[k] for k in ('dodge', 'Dodge', '閃避') if k in card.skills), None)
        counter = card.attacks[0] if card.attacks and not card.incomplete else None
        if dodge is not None:
            action['defense_kind'] = 'dive' if ranged else 'dodge'
            action['checks']['defense'] = _roll(state, action, 'defense', dodge)
        elif counter and not ranged:
            if not all(card.source.get(k) for k in ('url', 'revision', 'sha256')) or counter.max_targets != 1:
                return _ruling(state, action, 'NPC counter damage requires reviewed scenario source')
            try:
                dice.max_expression_value(counter.damage)
            except ValueError as exc:
                return _ruling(state, action, str(exc))
            action['defense_kind'] = 'counter'
            action['counter_damage'] = counter.damage
            action['checks']['defense'] = _roll(state, action, 'defense', counter.skill_value)
        else:
            return _ruling(state, action, 'NPC defense has no explicit skill or supported attack')
    if ranged and 'attack' not in action['checks']:
        penalty = 1 if action['defense_kind'] == 'dive' and action['checks']['defense']['success'] else 0
        pc = _character(state, actor.combatant_id)
        if pc:
            return _request_check(state, action, 'attack', pc, action['skill'], action['skill_value'], penalty=penalty)
        action['checks']['attack'] = _roll(state, action, 'attack', action['skill_value'], penalty=penalty,
                                          difficulty=action.get('difficulty', 'regular'))
    attack = action['checks']['attack']
    defense = action['checks']['defense']
    opposed = ('attacker_wins' if attack['success'] else 'both_miss') if ranged else dice.resolve_opposed(
        defense['tier'], attack['tier'], action['defense_kind'] == 'counter')
    counter_hit = opposed == 'defender_wins' and action['defense_kind'] == 'counter'
    hit = opposed in ('attacker_wins', 'tie_attacker_wins') or counter_hit
    if hit and not counter_hit and attack['tier'] in ('extreme', 'critical') and action.get('weapon', {}).get('extreme_rule') not in ('maximum', 'impale'):
        return _ruling(state, action, 'Extreme damage requires an explicit reviewed weapon classification')
    if not action.get('ammunition_spent'):
        if action.get('npc_attack_id') and ranged:
            card = state.combat.enemy_cards[actor.enemy_card_id]
            npc_attack = next(a for a in card.attacks if a.id == action['npc_attack_id'])
            if npc_attack.ammo_or_uses is None or npc_attack.ammo_or_uses < 1:
                return _ruling(state, action, 'NPC ammunition changed before shot')
            npc_attack.ammo_or_uses -= 1
        pc = _character(state, actor.combatant_id)
        amount = action.get('weapon', {}).get('ammo_per_attack', 0)
        if pc and amount:
            effective = combat_resources.effective_character(state, pc)
            if effective.weapons.get(action['ammo_key'], {}).get('ammo', 0) < amount:
                return _ruling(state, action, 'Ammunition changed before shot')
            combat_resources.adjust_ammo(state, pc, action['ammo_key'], -amount,
                                         event_id=action_id + ':ammo', reason='Single shot')
        action['ammunition_spent'] = True
    malfunction = action.get('weapon', {}).get('malfunction')
    if ranged and malfunction is not None and attack['roll'] >= malfunction:
        pc = _character(state, actor.combatant_id)
        if pc:
            combat_resources.set_status_tag(state, pc, '武器故障:' + action.get('ammo_key', action['weapon_reference']), True,
                                            event_id=action_id + ':malfunction', reason='Reviewed malfunction threshold')
        return _complete(state, action, {'hit': False, 'malfunction': True})
    if not hit:
        return _complete(state, action, {'hit': False, 'opposed': opposed})
    recipient = actor if counter_hit else target
    def draw_damage() -> dict[str, Any]:
        if counter_hit:
            defender_pc = _character(state, target.combatant_id)
            expression = action.get('counter_damage', '1d3')
            return asdict(dice.roll_weapon_damage(expression, defender_pc.damage_bonus if defender_pc else '0'))
        weapon = action.get('weapon', {})
        if attack['tier'] in ('extreme', 'critical'):
            return asdict(dice.calculate_impaling_damage(action['damage'], action.get('db', '0'),
                                                        weapon.get('extreme_rule') == 'impale',
                                                        db_policy=weapon.get('db_policy', 'none')))
        return asdict(dice.roll_weapon_damage(action['damage'], action.get('db', '0'),
                                             db_policy=weapon.get('db_policy', 'none')))
    receipt = combat_resources.record_roll(state, f'{state.combat.combat_id}:{action_id}:damage', draw_damage)
    damage_result = apply_managed_damage(state, recipient.combatant_id, max(0, receipt['total']),
                                                event_id=action_id + ':damage', source_id=action['weapon_reference'])
    if not damage_result['ok']:
        return damage_result
    action['result'] = {'hit': True, 'opposed': opposed, 'damage': damage_result, 'damage_receipt': receipt}
    if state.combat.interaction:
        injury_action = state.combat.actions[state.combat.interaction['action_id']]
        injury_action['parent_action_id'] = action_id
        action['stage'] = 'injury'
        return _result(state, action)
    return _complete(state, action, action['result'])


def _complete(state: GroupState, action: CombatAction, result: dict[str, Any]) -> dict[str, Any]:
    action['completed'] = True
    action['result'] = result
    state.combat.phase = 'READY'
    combat_resources.record_event(state, action['action_id'] + ':complete', 'action', data=deepcopy(action))
    action['receipt'] = _result(state, action)
    return deepcopy(action['receipt'])


def request_injury_check(state: GroupState, character: Character, event_id: str) -> dict[str, Any]:
    identity = event_id + ':injury-check'
    action = state.combat.actions.setdefault(identity, {'action_id': identity, 'character_id': character.character_id,
                                                      'kind': 'injury', 'completed': False, 'checks': {}})
    if state.autoroll_checks:
        result = _roll(state, action, 'injury', character.con)
        effective = combat_resources.effective_character(state, character)
        if not result['success']:
            effective.injury['unconscious'] = True
            for tag in ('昏迷', '倒地'):
                if tag not in effective.status_tags:
                    effective.status_tags.append(tag)
        combat_resources.reconcile_effective_character(state, effective, event_id=identity + ':con', reason='Major wound CON')
        action['checks']['injury'] = result
        action['completed'] = True
        return {'pending': False, **result}
    result = _request_check(state, action, 'injury', character, 'CON', character.con, injury=True)
    return {'pending': True, 'skill': 'CON', 'skill_value': character.con, **result}


def ensure_dying_obligation(state: GroupState, character: Character, event_id: str) -> None:
    identity = f'{state.combat.combat_id}:dying:{character.character_id}:{event_id}'
    existing = next((a for a in state.combat.actions.values() if a.get('obligation', {}).get('character_id') == character.character_id
                     and a.get('obligation', {}).get('kind') == 'dying' and a['obligation'].get('status') != 'resolved'), None)
    if existing or any(o.get('character_id') == character.character_id and o.get('kind') == 'dying'
                       and o.get('status') != 'resolved' for o in _all_obligations(state)):
        return
    severity = combat_rules.resolve_severity('minor').definition
    assert severity is not None
    source = asdict(severity.source)  # official damage/injury source
    obligation: PostcombatObligation = {'obligation_id': identity, 'combat_id': state.combat.combat_id,
                                       'character_id': character.character_id, 'kind': 'dying', 'status': 'future',
                                       'next_trigger': {'round': state.mechanical_round + 2, 'timing': 'round_end'},
                                       'stop_condition': 'Successful First Aid stabilization or death',
                                       'rule_source': source, 'processed_timings': [], 'roll_receipts': {}}
    state.combat.actions[identity] = {'action_id': identity, 'kind': 'obligation', 'completed': True,
                                    'obligation': obligation, 'event_id': event_id}


def run_enemy_plan(state: GroupState, plan_id: str) -> dict[str, Any]:
    """Lock a supported NPC plan to one action; unsupported specials pause."""
    plan = state.combat.plans.get(plan_id)
    if not plan:
        return _error('Unknown NPC plan')
    identity = f'npc:{plan_id}'
    if identity in state.combat.actions:
        return run_action(state, identity)
    actor = combat.find_combatant(state, plan.get('enemy_combatant_id', ''))
    current = state.combat.order[state.combat.current_index] if state.combat.order else None
    if not actor or actor is not current or plan.get('round_number') != state.combat.round_number:
        return _error('NPC plan is stale or actor is not current')
    if any(a.get('actor_id') == actor.combatant_id and a.get('completed')
           and a.get('round') == state.combat.round_number for a in state.combat.actions.values()):
        return _error('NPC actor already completed this turn; advance initiative')
    if plan.get('selected_action') != 'attack':
        action: CombatAction = {'action_id': identity, 'completed': False, 'actor_id': actor.combatant_id,
                  'target_id': next(iter(plan.get('target_ids', [])), ''), 'round': state.combat.round_number}
        state.combat.actions[identity] = action
        return _ruling(state, action, 'NPC special/movement plan requires an explicit ruling')
    card = state.combat.enemy_cards.get(actor.enemy_card_id)
    attack = next((a for a in card.attacks if a.id == plan.get('selected_id')), None) if card else None
    targets = plan.get('target_ids', [])
    if not attack or not card or card.incomplete or len(targets) != 1:
        return _error('NPC attack requires a complete single-target definition')
    source = card.source
    if not source or not source.get('url') or not source.get('revision') or not source.get('sha256'):
        action = {'action_id': identity, 'completed': False, 'actor_id': actor.combatant_id,
                  'target_id': next(iter(plan.get('target_ids', [])), ''), 'round': state.combat.round_number}
        state.combat.actions[identity] = action
        return _ruling(state, action, 'NPC attack requires verified scenario provenance')
    try:
        dice.max_expression_value(attack.damage)
    except ValueError:
        return _error('NPC attack damage expression is invalid')
    attack_metadata = source.get('attacks', {}).get(attack.id, source if len(card.attacks) == 1 else {})
    mode = attack_metadata.get('attack_mode', 'melee' if attack.range_band == 'engaged' and len(card.attacks) == 1 else None)
    if mode not in ('melee', 'single_shot'):
        action = {'action_id': identity, 'completed': False, 'actor_id': actor.combatant_id,
                  'target_id': next(iter(plan.get('target_ids', [])), ''), 'round': state.combat.round_number}
        state.combat.actions[identity] = action
        return _ruling(state, action, 'NPC attack mode is not explicit/supported')
    if mode == 'single_shot' and (attack.ammo_or_uses is None or attack.ammo_or_uses < 1
                                  or attack_metadata.get('distance_yards') is None or attack_metadata.get('base_range_yards') is None):
        action = {'action_id': identity, 'completed': False, 'actor_id': actor.combatant_id,
                  'target_id': next(iter(plan.get('target_ids', [])), ''), 'round': state.combat.round_number}
        state.combat.actions[identity] = action
        return _ruling(state, action, 'NPC single shot requires ammunition and reviewed physical range')
    difficulty = 'regular'
    if mode == 'single_shot':
        range_result = combat_rules.resolve_range_difficulty(
            attack_metadata['distance_yards'], attack_metadata['base_range_yards'])
        if range_result.difficulty is None:
            action = {'action_id': identity, 'completed': False, 'actor_id': actor.combatant_id,
                      'target_id': next(iter(plan.get('target_ids', [])), ''), 'round': state.combat.round_number}
            state.combat.actions[identity] = action
            return _ruling(state, action, range_result.reason)
        difficulty = range_result.difficulty
    action = {'action_id': identity, 'actor_id': actor.combatant_id, 'target_id': targets[0],
              'weapon_reference': attack.id, 'action_kind': mode, 'stage': 'attack',
              'completed': False, 'checks': {}, 'round': state.combat.round_number,
              'skill': attack.skill_name, 'skill_value': attack.skill_value, 'db': '0',
              'damage': attack.damage, 'difficulty': difficulty,
              'weapon': {'db_policy': 'none', 'extreme_rule': attack_metadata.get('extreme_rule')},
              'source': deepcopy(source), 'npc_attack_id': attack.id, 'plan_id': plan_id}
    state.combat.actions[identity] = action
    return run_action(state, identity)


def advance_combat(state: GroupState, *, actor_id: str, event_id: str, transition_budget: int = 16) -> dict[str, Any]:
    """Advance a completed current actor; execute NPC actions to one human boundary."""
    combat_resources.initialize_working_state(state)
    prior = next((e for e in state.combat.events if e['event_id'] == event_id), None)
    if prior:
        return deepcopy(prior['data'].get('final_response', prior['data']))
    current = state.combat.order[state.combat.current_index] if state.combat.order else None
    if not current or current.combatant_id != actor_id or not event_id:
        return _error('Only the current actor may advance with a stable event ID')
    if state.combat.interaction or any(not a.get('completed') for a in state.combat.actions.values()):
        return _error('Resolve the current action/interaction before advancing')
    if not any(a.get('actor_id') == actor_id and a.get('completed') and a.get('round') == state.combat.round_number
               for a in state.combat.actions.values()) and not current.defeated:
        return _error('Current actor has no completed action; explicit initiative ruling required')
    if not 1 <= transition_budget <= 64:
        return _error('Transition budget must be 1..64')
    result = combat.advance_turn(state, ops=MANAGED_OPS)
    if not result.get('ok'):
        return result
    transition = deepcopy(result)
    if not result.get('pending'):
        next_actor = state.combat.order[state.combat.current_index]
        if next_actor.side == 'enemy':
            plan = combat.plan_enemy_turn(state, next_actor.display_name, ops=MANAGED_OPS)
            if plan.get('ok'):
                result = run_enemy_plan(state, plan['plan_id'])
    combat_resources.record_event(state, event_id, 'initiative',
                                  data={'transition': transition, 'final_response': deepcopy(result)})
    return result


def set_initiative(state: GroupState, *, actor_ids: list[str], event_id: str, reason: str) -> dict[str, Any]:
    """Explicit controller order change, only between completed actions."""
    combat_resources.initialize_working_state(state)
    previous = next((e for e in state.combat.events if e['event_id'] == event_id), None)
    if previous:
        return deepcopy(previous['data'])
    if (not reason.strip() or not event_id or state.combat.interaction
            or any(not a.get('completed') for a in state.combat.actions.values())):
        return _error('Initiative change needs an explicit reason and completed action boundary')
    current = state.combat.order[state.combat.current_index]
    existing = {p.combatant_id: p for p in state.combat.order}
    if len(actor_ids) != len(existing) or set(actor_ids) != set(existing):
        return _error('Initiative must retain every participant exactly once')
    state.combat.order = [existing[i] for i in actor_ids]
    state.combat.current_index = actor_ids.index(current.combatant_id)
    result = {'ok': True, 'order': list(actor_ids), 'current_actor': current.combatant_id}
    combat_resources.record_event(state, event_id, 'initiative', data=result, reason=reason)
    return result


def declare_effect(
    state: GroupState, *, effect_id: str, target_id: str, severity_id: str,
    scope: Literal['incident', 'round'], reason: str, stop_condition: str,
    timing: str = 'round_end', special_rule: str | None = None, defense: Literal['none'] = 'none',
) -> dict[str, Any]:
    """Explicit table-severity ruling; labels never imply poison/drowning rules."""
    combat_resources.initialize_working_state(state)
    existing = next((e for e in state.combat.events if e['event_id'] == effect_id + ':declaration'), None)
    if existing:
        if existing['data'].get('scope') == 'incident':
            return run_effect(state, effect_id)
        return deepcopy(existing['data'])
    lookup = combat_rules.resolve_severity(severity_id)
    if (not effect_id or not reason.strip() or not stop_condition.strip() or lookup.definition is None
            or scope not in ('incident', 'round') or timing not in ('round_start', 'turn_start', 'turn_end', 'round_end')
            or combat.find_combatant(state, target_id) is None):
        return _error('Effect requires explicit reviewed severity, target, scope, timing, reason and stop condition')
    if special_rule or defense != 'none':
        action: CombatAction = {'action_id': 'effect-ruling:' + effect_id, 'kind': 'effect', 'completed': False}
        state.combat.actions['effect-ruling:' + effect_id] = action
        return _ruling(state, action, 'Special poison/drowning/fire behavior requires supported source ruling')
    definition = lookup.definition
    from app.models import EffectState
    effect = EffectState(id=effect_id, label=reason, source_id=definition.id, target_id=target_id,
                         timing=timing, remaining_rounds=1 if scope == 'incident' else None,
                         damage=definition.damage, tags=['reviewed-generic-damage'],
                         save_or_check={'severity_id': severity_id, 'rule_source': asdict(definition.source),
                                        'scope': scope, 'stop_condition': stop_condition, 'defense': defense,
                                        'next_round': state.mechanical_round + 1})
    state.combat.effects.append(effect)
    result = {'ok': True, 'effect_id': effect_id, 'severity_id': severity_id, 'damage': definition.damage,
              'scope': scope, 'timing': timing, 'defense': defense, 'source': asdict(definition.source)}
    combat_resources.record_event(state, effect_id + ':declaration', 'effect', data=result, reason=reason)
    if scope == 'incident':
        return run_effect(state, effect_id)
    return result


def stop_effect(state: GroupState, *, effect_id: str, event_id: str, reason: str, combat_id: str | None = None) -> dict[str, Any]:
    source_combat = combat_id or (state.combat.combat_id if combat_resources.is_managed(state) else '')
    if not reason.strip() or not event_id or not source_combat:
        return _error('Stopping an effect requires an explicit source battle and recorded reason')
    result = {'ok': True, 'stopped_effect_id': effect_id, 'combat_id': source_combat}
    if combat_resources.is_managed(state):
        prior = next((event for event in state.combat.events if event['event_id'] == event_id), None)
        if prior:
            if (prior['kind'] != 'effect' or prior['data'].get('stopped_effect_id') != effect_id
                    or prior['data'].get('combat_id', state.combat.combat_id) != source_combat):
                return _error('Stop event identity belongs to another effect or source')
            return result
    obligations = [obligation for obligation in _continuing_store(state)
                   if obligation.get('combat_id') == source_combat
                   and obligation.get('effect', {}).get('id') == effect_id]
    active_match = (combat_resources.is_managed(state) and source_combat == state.combat.combat_id
                    and any(effect.id == effect_id for effect in state.combat.effects))
    if not active_match and not obligations:
        return _error('No matching effect in the specified source battle')
    if active_match:
        state.combat.effects = [effect for effect in state.combat.effects if effect.id != effect_id]
    for obligation in obligations:
        obligation['status'] = 'resolved'
        marker = 'stop:' + event_id
        if marker not in obligation.setdefault('processed_timings', []):
            obligation['processed_timings'].append(marker)
    if combat_resources.is_managed(state):
        combat_resources.record_event(state, event_id, 'effect', data=result, reason=reason)
    return result


def postcombat_obligations(state: GroupState) -> list[PostcombatObligation]:
    """Project exact future timing; never reset existing continuing obligations."""
    projected = state.combat.actions.get(combat_resources.CONTINUING_STATE_KEY, {}).get('working_obligations', state.postcombat_obligations)
    result: list[PostcombatObligation] = deepcopy(projected)
    known = {o['obligation_id'] for o in projected}
    for action in state.combat.actions.values():
        obligation = action.get('obligation')
        if obligation and obligation['status'] == 'future' and obligation['obligation_id'] not in known:
            character = state.characters_by_id[obligation['character_id']]
            injury = combat_resources.effective_character(state, character).injury
            if injury.get('dying') and not injury.get('dead'):
                result.append(deepcopy(obligation))
    for effect in state.combat.effects:
        identity = f'{state.combat.combat_id}:effect:{effect.id}'
        if identity in known:
            continue
        source = effect.save_or_check
        if source.get('scope') == 'incident':
            raise combat_resources.CombatAdmissionError('Currently due incident must resolve before settlement')
        if not source.get('rule_source') or not source.get('stop_condition'):
            raise combat_resources.CombatAdmissionError('Continuing effect needs reviewed rule and stop condition')
        participant = combat.find_combatant(state, effect.target_id)
        if not participant or not participant.is_pc:
            raise combat_resources.CombatAdmissionError('Continuing NPC/global effects require explicit supported transfer ruling')
        result.append({'obligation_id': identity, 'combat_id': state.combat.combat_id,
                       'character_id': participant.character_id, 'kind': 'effect', 'status': 'future',
                       'next_trigger': {'round': source.get('next_round', state.mechanical_round + 1), 'timing': effect.timing},
                       'stop_condition': source['stop_condition'], 'rule_source': deepcopy(source['rule_source']),
                       'processed_timings': [], 'roll_receipts': {}, 'effect': effect.to_dict()})
    return result


def _all_obligations(state: GroupState) -> list[PostcombatObligation]:
    if combat_resources.is_managed(state):
        obligations = list(state.combat.actions.get(combat_resources.CONTINUING_STATE_KEY, {}).get('working_obligations', state.postcombat_obligations))
    else:
        obligations = list(state.postcombat_obligations)
    if combat_resources.is_managed(state):
        obligations.extend(a['obligation'] for a in state.combat.actions.values() if a.get('obligation'))
    return obligations


def process_postcombat(state: GroupState, *, logical_round: int, event_id: str) -> dict[str, Any]:
    """Advance one logical round at most; cannot jump over unresolved due work."""
    if not event_id or isinstance(logical_round, bool) or not isinstance(logical_round, int):
        return _error('Explicit logical round and stable event ID are required')
    if logical_round < state.mechanical_round or logical_round > state.mechanical_round + 1:
        return _error('Logical time must advance sequentially without skipping rounds')
    obligations = _all_obligations(state)
    unresolved = [o for o in obligations if o.get('status') in ('due', 'pending')
                  or (o.get('status') == 'future' and o.get('next_trigger', {}).get('round', state.mechanical_round + 1) <= state.mechanical_round)]
    if unresolved and logical_round != state.mechanical_round:
        return _error('Resolve due continuing checks/effects before advancing time')
    state.mechanical_round = logical_round
    results = []
    for obligation in obligations:
        if obligation.get('status') == 'resolved' or obligation.get('next_trigger', {}).get('round', logical_round + 1) > logical_round:
            continue
        character = state.characters_by_id.get(obligation.get('character_id', ''))
        if not character:
            return _error('Continuing obligation participant no longer exists')
        effective = combat_resources.effective_character(state, character)
        if effective.injury.get('dead') or (obligation['kind'] == 'dying' and not effective.injury.get('dying')):
            obligation['status'] = 'resolved'
            continue
        if obligation.get('status') == 'pending':
            existing = state.pending_checks.get(character.owner_id)
            if existing and existing.get('check_id') == obligation.get('next_trigger', {}).get('check_id'):
                results.append({'obligation_id': obligation['obligation_id'], 'pending': True})
                continue
            # A rollback may restore the original pending schedule after its
            # projected control was consumed. Re-admit the original trigger;
            # its cached receipt survives, while old consumed controls expire.
            obligation['status'] = 'due'
        if check_lifecycle.blocker(state, character.owner_id):
            obligation['status'] = 'due'
            return _error('Continuing obligation waits for existing player check/Luck')
        if obligation['kind'] == 'dying':
            trigger_round = obligation['next_trigger']['round']
            pending = {'type': 'skill', 'skill': 'CON', 'skill_value': character.con,
                       'bonus_dice': 0, 'penalty_dice': 0, 'difficulty': 'regular', 'allow_luck': False,
                       'postcombat_context': {'obligation_id': obligation['obligation_id'], 'round': trigger_round}}
            decision = check_lifecycle.register(state, character.owner_id, pending)
            if decision.pending is None:
                return _error('Cannot register due dying CON check')
            obligation['next_trigger']['check_id'] = decision.check_id
            obligation['status'] = 'pending'
            if state.autoroll_checks:
                result = roll_pending_check(state, decision.pending, character.owner_id)
                state.pending_checks.pop(character.owner_id, None)
                results.append(_finish_obligation_check(state, decision.pending, character.owner_id, result))
            else:
                results.append({'obligation_id': obligation['obligation_id'], 'pending': True, 'check_id': decision.check_id})
        else:
            results.append(_process_obligation_effect(state, obligation, character, obligation['next_trigger']['round']))
    return {'ok': True, 'mechanical_round': state.mechanical_round, 'results': results}


def _finish_obligation_check(state: GroupState, pending: dict[str, Any], owner_id: str,
                             result: dice.SkillCheckResult) -> dict[str, Any]:
    context = pending['postcombat_context']
    obligation = next(o for o in _all_obligations(state) if o['obligation_id'] == context['obligation_id'])
    character = state.characters_by_id[obligation['character_id']]
    if character.owner_id != owner_id:
        return _error('Dying check belongs to another player')
    effective = combat_resources.effective_character(state, character)
    identity = f"{obligation['obligation_id']}:{context['round']}"
    obligation.setdefault('roll_receipts', {})[identity + ':con'] = asdict(result)
    parent_id = obligation.get('effect', {}).get('parent_obligation_id')
    if parent_id:
        parent_obligation = next(o for o in _all_obligations(state) if o['obligation_id'] == parent_id)
        parent_obligation.setdefault('roll_receipts', {})[identity + ':con'] = asdict(result)
    if context.get('major_wound'):
        if not result.success:
            effective.injury['unconscious'] = True
            for tag in ('昏迷', '倒地'):
                if tag not in effective.status_tags:
                    effective.status_tags.append(tag)
            _write_character(state, effective, identity + ':unconscious', 'Major wound CON')
        obligation['status'] = 'resolved'
    elif not result.success:
        effective.injury.update({'dead': True, 'dying': False, 'unconscious': True})
        _write_character(state, effective, identity + ':death', 'Failed dying CON')
        obligation['status'] = 'resolved'
    else:
        obligation['status'] = 'future'
        obligation['next_trigger'] = {'round': context['round'] + 1, 'timing': 'round_end'}
    obligation.setdefault('processed_timings', []).append(identity)
    return {'ok': True, 'obligation_id': obligation['obligation_id'], 'dead': bool(effective.injury.get('dead')),
            'next_trigger': obligation['next_trigger']}


def _write_character(state: GroupState, effective: Character, event_id: str, reason: str) -> None:
    if combat_resources.is_managed(state):
        if effective.character_id not in state.combat.working_resources:
            raise combat_resources.CombatAdmissionError('Continuing participant must be admitted before any mutation')
        combat_resources.reconcile_effective_character(state, effective, event_id=event_id, reason=reason)
    else:
        original = state.characters_by_id[effective.character_id]
        original.hp, original.injury, original.status_tags = effective.hp, deepcopy(effective.injury), list(effective.status_tags)
        for owner, mirror in state.characters.items():
            if mirror.character_id == effective.character_id:
                state.characters[owner] = original


def _process_obligation_effect(state: GroupState, obligation: PostcombatObligation,
                               character: Character, logical_round: int) -> dict[str, Any]:
    effect = obligation.get('effect', {})
    if effect.get('save_or_check', {}).get('severity_id') is None:
        obligation['status'] = 'due'
        return _error('Unsupported continuing effect requires a source-backed ruling')
    identity = f"{obligation['obligation_id']}:{logical_round}"
    receipts = obligation.setdefault('roll_receipts', {})
    if identity not in receipts:
        receipts[identity] = asdict(dice.roll_expression(effect['damage']))
    damage = max(0, receipts[identity]['total'])
    effective = combat_resources.effective_character(state, character)
    if combat_resources.is_managed(state) and character.character_id in state.combat.working_resources:
        result = managed_single_hit(state, character, damage, event_id=identity, reason='Continuing effect')
        if not result['ok']:
            obligation['status'] = 'due'
            return result
        injury_action = state.combat.actions.get(identity + ':injury-check')
        if injury_action:
            con_key = f'{identity}:injury:{logical_round}:con'
            injury_action['parent_obligation_id'] = obligation['obligation_id']
            injury_action['parent_con_receipt_id'] = con_key
            if 'injury' in injury_action['checks']:
                receipts[con_key] = deepcopy(injury_action['checks']['injury'])
    else:
        # Outside battle retain the ordinary persistent mutation contract;
        # required injury checks are still registered in this same transaction.
        effective.hp = max(0, effective.hp - damage)
        major = effective.hp_max / 2 <= damage < effective.hp_max
        if damage >= effective.hp_max:
            effective.injury.update({'dead': True, 'unconscious': True, 'dying': False})
        elif major:
            effective.injury['major_wound'] = True
        if effective.hp == 0:
            effective.injury['unconscious'] = True
            effective.injury['dying'] = bool(effective.injury.get('major_wound') and not effective.injury.get('dead'))
        _write_character(state, effective, identity, 'Continuing effect')
        if major:
            # A due immediate major-wound check is represented as a distinct
            # continuing check, with its own receipt and stop rule.
            con_obligation: PostcombatObligation = {'obligation_id': identity + ':injury',
                'combat_id': obligation['combat_id'], 'character_id': character.character_id,
                'kind': 'dying', 'status': 'pending', 'next_trigger': {'round': logical_round},
                'rule_source': obligation['rule_source'], 'stop_condition': 'Immediate injury CON completed',
                'injury': {'major_wound': True}, 'processed_timings': [],
                'effect': {'parent_obligation_id': obligation['obligation_id']},
                'roll_receipts': {k: deepcopy(v) for k, v in receipts.items() if k.startswith(identity + ':injury:')}}
            _continuing_store(state).append(con_obligation)
            pending = {'type': 'skill', 'skill': 'CON', 'skill_value': character.con, 'difficulty': 'regular',
                       'bonus_dice': 0, 'penalty_dice': 0, 'allow_luck': False,
                       'postcombat_context': {'obligation_id': con_obligation['obligation_id'], 'round': logical_round,
                                             'major_wound': True}}
            registration = check_lifecycle.register(state, character.owner_id, pending)
            if registration.pending is None:
                raise RuntimeError('Prevalidated postcombat injury ownership changed')
            con_obligation['next_trigger']['check_id'] = registration.check_id
            if state.autoroll_checks:
                con_result = roll_pending_check(state, registration.pending, character.owner_id)
                state.pending_checks.pop(character.owner_id, None)
                _finish_obligation_check(state, registration.pending, character.owner_id, con_result)
        if effective.injury.get('dying'):
            dying_id = f"{obligation['combat_id']}:dying:{character.character_id}:{identity}"
            if not any(o['obligation_id'] == dying_id and o.get('status') != 'resolved' for o in _all_obligations(state)):
                _continuing_store(state).append({'obligation_id': dying_id, 'combat_id': obligation['combat_id'],
                    'character_id': character.character_id, 'kind': 'dying', 'status': 'future',
                    'next_trigger': {'round': logical_round + 2, 'timing': 'round_end'},
                    'stop_condition': 'First Aid stabilization or death', 'rule_source': obligation['rule_source'],
                    'processed_timings': [], 'roll_receipts': {}})
        result = {'ok': True, 'damage': damage, 'hp_after': effective.hp}
    obligation.setdefault('processed_timings', []).append(identity)
    remaining = effect.get('remaining_rounds')
    if remaining is not None:
        effect['remaining_rounds'] = remaining - 1
    obligation['status'] = 'resolved' if remaining == 1 else 'future'
    obligation['next_trigger'] = {'round': logical_round + 1, 'timing': effect.get('timing', 'round_end')}
    return result


def resolve_ruling(
    state: GroupState, *, action_id: str, event_id: str, reason: str,
    decision: Literal['resume', 'cancel'], weapon_reference: str | None = None,
    distance_yards: float | None = None,
    scenario_definitions: tuple[combat_rules.WeaponDefinition, ...] = (),
    weapon_instance: combat_rules.WeaponInstance | None = None,
) -> dict[str, Any]:
    """Explicit supported source mapping/range ruling or audited cancellation.

    Scenario definitions come from the verified owning scenario layer, never
    freeform model damage. Corrections with already applied dependent damage
    require explicit resource/injury reconciliation before this seam can resume.
    """
    combat_resources.initialize_working_state(state)
    prior = next((e for e in state.combat.events if e['event_id'] == event_id), None)
    if prior:
        return deepcopy(prior['data'])
    action = state.combat.actions.get(action_id)
    if not action or action_id.startswith('system:') or not event_id or not reason.strip() or decision not in ('resume', 'cancel'):
        return _error('Ruling requires an existing action, explicit identity/reason and supported decision')
    if any(e.get('kind') == 'damage' and e['event_id'].startswith(action_id + ':') for e in state.combat.events):
        return _error('Materialized damage needs explicit dependent resource/injury reconciliation; retain original rolls')
    if decision == 'cancel':
        wait = state.combat.interaction
        if wait and wait.get('action_id') != action_id:
            return _error('Cannot cancel another action interaction')
        if wait:
            owner = wait['owner_id']
            action['cancelled_pending'] = deepcopy(state.pending_checks.pop(owner, None))
            action['cancelled_luck'] = deepcopy(state.pending_luck_decisions.pop(owner, None))
            action['cancelled_interaction'] = deepcopy(wait)
            state.combat.interaction = {}
        action.update({'completed': True, 'cancelled': True, 'cancellation_reason': reason})
        action.pop('needs_ruling', None)
        state.combat.phase = 'READY'
        result = _result(state, action)
    else:
        if state.combat.interaction:
            return _error('Resolve/cancel the retained player interaction before source/range reconciliation')
        actor = combat.find_combatant(state, action.get('actor_id', ''))
        if not actor or actor.combatant_id != state.combat.order[state.combat.current_index].combatant_id:
            return _error('Source reconciliation needs the original current actor and target')
        reference = weapon_reference or action.get('weapon_reference', '')
        lookup = combat_rules.resolve_weapon(reference, scenario_definitions=scenario_definitions, instance=weapon_instance)
        if lookup.definition is None:
            return _ruling(state, action, lookup.reason)
        weapon = lookup.definition
        distance = distance_yards if distance_yards is not None else action.get('distance_yards')
        damage = combat_rules.resolve_weapon_damage(weapon, distance_yards=distance)
        if damage.damage is None or weapon.attack_mode != action.get('action_kind'):
            return _ruling(state, action, damage.reason or 'Unsupported changed attack mode')
        try:
            evidence = _weapon_actor_evidence(state, actor, weapon, reference, weapon_instance)
        except ValueError as exc:
            return _ruling(state, action, str(exc))
        if action.get('checks') and action.get('skill') != evidence['skill']:
            return _ruling(state, action, 'Changed skill invalidates retained player result; explicit cancellation required')
        if weapon.attack_mode == 'single_shot':
            range_result = combat_rules.resolve_range_difficulty(distance, weapon.base_range_yards)
            if range_result.difficulty is None:
                return _ruling(state, action, range_result.reason)
            action['difficulty'] = range_result.difficulty
        try:
            dice.max_expression_value(damage.damage)
            dice.max_expression_value(evidence['db'])
        except ValueError as exc:
            return _ruling(state, action, str(exc))
        action.update({'weapon_reference': reference, 'weapon': asdict(weapon), 'damage': damage.damage, 'distance_yards': distance, **evidence})
        action.pop('needs_ruling', None)
        state.combat.phase = 'RESOLVE'
        result = run_action(state, action_id)
    combat_resources.record_event(state, event_id, 'ruling', data=deepcopy(result), reason=reason)
    return result


def request_injury_checks(state: GroupState, characters: list[Character], event_id: str) -> dict[str, Any]:
    """Reserve all simultaneous required CON checks; publish one owned wait at a time."""
    if state.autoroll_checks:
        return {'ok': True, 'checks': [request_injury_check(state, c, event_id + ':' + c.character_id) for c in characters]}
    identity = event_id + ':injury-checks'
    action: CombatAction = {'action_id': identity, 'kind': 'injury', 'completed': False,
                              'checks': {}, 'injury_queue': [], 'character_id': characters[0].character_id}
    state.combat.actions[identity] = action
    for character in characters:
        candidate = {'type': 'skill', 'skill': 'CON', 'skill_value': character.con,
                     'bonus_dice': 0, 'penalty_dice': 0, 'difficulty': 'regular', 'allow_luck': False,
                     'major_wound_trigger': True, 'combat_context': _context(state, identity, 'injury', character_id=character.character_id).to_dict()}
        registration = check_lifecycle.register(state, character.owner_id, candidate)
        if registration.pending is None:
            raise RuntimeError('All-target prevalidated injury admission changed')
        action['injury_queue'].append({'owner_id': character.owner_id, 'character_id': character.character_id,
                                      'pending': deepcopy(registration.pending)})
    return _next_injury_wait(state, action)


def _next_injury_wait(state: GroupState, action: CombatAction) -> dict[str, Any]:
    item = action['injury_queue'][0]
    pending = item['pending']
    action['character_id'] = item['character_id']
    state.combat.interaction = {**pending['combat_context'], 'owner_id': item['owner_id'], 'check_id': pending['check_id']}
    state.combat.phase = 'INJURY_CHECK'
    if state.autoroll_checks:
        result = roll_pending_check(state, pending, item['owner_id'])
        state.pending_checks.pop(item['owner_id'], None)
        return on_authoritative_check_result(state, pending_entry=pending, owner_id=item['owner_id'], result=result)
    return _result(state, action)


def reconcile_correction(
    state: GroupState, *, event_id: str, reason: str,
    injury_by_character: dict[str, dict[str, bool]], acknowledge_action_ids: list[str],
    acknowledge_check_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Controller reviews corrected working resources and all dependent injury/decisions.

    Original rolls and choices remain recorded. This explicit projection does
    not replay RNG or infer a corrected injury from an unevidenced hit total.
    """
    combat_resources.initialize_working_state(state)
    prior = next((e for e in state.combat.events if e['event_id'] == event_id), None)
    if prior:
        return deepcopy(prior['data'])
    wait = state.combat.interaction
    if state.combat.phase != 'NEEDS_RULING' or wait.get('kind') != 'correction_reconciliation':
        return _error('No correction reconciliation is awaiting review')
    if not event_id or not reason.strip() or set(acknowledge_action_ids) != set(state.combat.actions):
        return _error('Explicitly acknowledge every retained affected action/choice with a reason')
    if set(injury_by_character) != set(state.combat.working_resources):
        return _error('Review an exact injury projection for every admitted investigator')
    allowed = {'major_wound', 'unconscious', 'dying', 'dead'}
    for identity, proposed_injury in injury_by_character.items():
        injury = proposed_injury
        if set(injury) - allowed or any(type(v) is not bool for v in injury.values()):
            return _error('Injury projection requires supported boolean states')
        snapshot = state.combat.working_resources[identity]
        if (injury.get('dying') and (snapshot['hp'] != 0 or not injury.get('major_wound') or injury.get('dead'))
                or injury.get('dead') and (snapshot['hp'] != 0 or not injury.get('unconscious'))
                or snapshot['hp'] == 0 and not injury.get('unconscious')):
            return _error('Corrected injury/resource projection is inconsistent')
    owners = {state.characters_by_id[i].owner_id for i in injury_by_character}
    retained = {key: {owner: deepcopy(entry) for owner, entry in getattr(state, key).items() if owner in owners}
                for key in ('pending_checks', 'pending_luck_decisions')}
    expected_checks = {entry.get('check_id') for entries in retained.values() for entry in entries.values()}
    if None in expected_checks or set(acknowledge_check_ids or []) != expected_checks:
        return _error('Explicitly acknowledge every retained owned check/Luck identity before correction confirmation')
    for entries in retained.values():
        for owner, entry in entries.items():
            context = entry.get('postcombat_context')
            if context:
                obligation = next((o for o in _all_obligations(state) if o['obligation_id'] == context['obligation_id']), None)
                if obligation and injury_by_character[obligation['character_id']].get('dying'):
                    return _error('A still-due dying check must resolve; reconciliation cannot grant a free interval')
    for key, entries in retained.items():
        for owner in entries:
            getattr(state, key).pop(owner)
    from app.models import InjuryState
    for identity, projection in injury_by_character.items():
        character = state.characters_by_id[identity]
        effective = combat_resources.effective_character(state, character)
        reviewed_injury = InjuryState()
        for key in ('major_wound', 'unconscious', 'dying', 'dead'):
            if key in projection:
                reviewed_injury[key] = projection[key]  # type: ignore[literal-required]
        effective.injury = reviewed_injury
        for tag in ('昏迷', '倒地'):
            if reviewed_injury.get('unconscious') and tag not in effective.status_tags:
                effective.status_tags.append(tag)
            elif not reviewed_injury.get('unconscious') and tag in effective.status_tags:
                effective.status_tags.remove(tag)
        combat_resources.reconcile_effective_character(state, effective,
                                                      event_id=event_id + ':' + identity, reason=reason)
        if not reviewed_injury.get('dying'):
            for obligation in _all_obligations(state):
                if obligation.get('character_id') == identity and obligation.get('kind') == 'dying':
                    obligation['status'] = 'resolved'
        if reviewed_injury.get('dying'):
            ensure_dying_obligation(state, character, event_id)
        participant = next(p for p in state.combat.order if p.character_id == identity)
        participant.defeated = effective.hp == 0 or bool(reviewed_injury.get('unconscious'))
    for action in state.combat.actions.values():
        action['completed'] = True
        action['correction_acknowledgement'] = event_id
    state.combat.interaction = {}
    state.combat.phase = 'READY'
    result = {'ok': True, 'reconciled_correction': deepcopy(wait), 'event_id': event_id,
              'acknowledged_actions': list(acknowledge_action_ids), 'retained_controls': retained}
    combat_resources.record_event(state, event_id, 'ruling', data=result, reason=reason)
    return result


def _continuing_store(state: GroupState) -> list[PostcombatObligation]:
    if combat_resources.is_managed(state):
        return state.combat.actions[combat_resources.CONTINUING_STATE_KEY]['working_obligations']
    return state.postcombat_obligations


def stabilize_investigator(
    state: GroupState, *, character_id: str, source_check_id: str, event_id: str, reason: str,
) -> dict[str, Any]:
    """Apply successful recorded First Aid stabilization; never accept caller success."""
    character = state.characters_by_id.get(character_id)
    medical_receipts = [a['medical_receipt'] for a in state.combat.actions.values() if combat_resources.is_managed(state) and a.get('medical_receipt')]
    medical_receipts.extend(a['medical_receipt'] for closed in state.closed_combat_receipts.values()
                            for a in closed.get('actions', {}).values() if closed.get('status') == 'committed' and a.get('medical_receipt'))
    evidence = next((e for e in state.resolved_check_events + medical_receipts if e.get('check_id') == source_check_id
                     and e.get('timeline_id') == state.timeline_id and e.get('skill') in ('急救', 'First Aid')
                     and e.get('success') is True), None)
    if not character or evidence is None or not event_id or not reason.strip():
        return _error('Stabilization requires a successful authoritative First Aid check and explicit target/reason')
    source_battle = evidence.get('medical_context', {}).get('combat_id')
    if (source_battle and source_battle != (state.combat.combat_id if combat_resources.is_managed(state) else None)
            and state.closed_combat_receipts.get(source_battle, {}).get('status') != 'committed'):
        return _error('A rolled-back medical check cannot publish stabilization')
    applied = [e['data']['stabilization_receipt'] for e in state.combat.events
               if combat_resources.is_managed(state) and e.get('data', {}).get('stabilization_receipt')]
    applied.extend(e['data']['stabilization_receipt'] for closed in state.closed_combat_receipts.values()
                   for e in closed.get('events', []) if closed.get('status') == 'committed'
                   and e.get('data', {}).get('stabilization_receipt'))
    used = evidence.get('stabilization_receipt') or next((r for r in applied if r['source_check_id'] == source_check_id), None)
    if used:
        if used['event_id'] == event_id and used['character_id'] == character_id:
            return deepcopy(used)
        return _error('First Aid result has already been applied to a stabilization')
    if combat_resources.is_managed(state) and character_id not in state.combat.working_resources:
        return _error('Stabilized investigator must be admitted to the working state')
    effective = combat_resources.effective_character(state, character)
    active_ids = sorted(o['obligation_id'] for o in _all_obligations(state) if o.get('character_id') == character_id
                        and o.get('kind') == 'dying' and o.get('status') != 'resolved')
    context = evidence.get('medical_context', {})
    if (not effective.injury.get('dying') or not active_ids or context.get('character_id') != character_id
            or context.get('obligation_ids') != active_ids):
        return _error('First Aid receipt is not bound to this patient and current dying injury')
    if effective.injury.get('dead'):
        return _error('First Aid cannot reverse death')
    effective.injury['dying'] = False
    _write_character(state, effective, event_id + ':injury', 'Recorded First Aid stabilization')
    for obligation in _all_obligations(state):
        if obligation.get('character_id') == character_id and obligation.get('kind') == 'dying':
            obligation['status'] = 'resolved'
            obligation.setdefault('processed_timings', []).append('stabilized:' + event_id)
            pending = state.pending_checks.get(character.owner_id, {})
            if pending.get('postcombat_context', {}).get('obligation_id') == obligation['obligation_id']:
                obligation['effect'] = {**obligation.get('effect', {}), 'cancelled_due_control': deepcopy(pending),
                                        'stabilization_check_id': source_check_id}
                state.pending_checks.pop(character.owner_id)
    result = {'ok': True, 'character_id': character_id, 'stabilized': True,
              'source_check_id': source_check_id, 'event_id': event_id}
    if not combat_resources.is_managed(state):
        evidence['stabilization_receipt'] = deepcopy(result)
    if combat_resources.is_managed(state):
        combat_resources.record_event(state, event_id, 'injury', data={'character_id': character_id,
                                       'after': deepcopy(effective.injury), 'stabilization_receipt': deepcopy(result)}, reason=reason)
    return result


def request_stabilization_check(
    state: GroupState, *, healer_character_id: str, character_id: str, event_id: str, reason: str,
) -> dict[str, Any]:
    """Register owned First Aid tied to the patient's exact current dying obligations."""
    healer = state.characters_by_id.get(healer_character_id)
    patient = state.characters_by_id.get(character_id)
    if not healer or not patient or not event_id or not reason.strip() or event_id.startswith('system:'):
        return _error('Medical declaration needs stable healer/patient IDs and recorded reason')
    effective = combat_resources.effective_character(state, patient)
    obligations = sorted(o['obligation_id'] for o in _all_obligations(state)
                         if o.get('character_id') == character_id and o.get('kind') == 'dying' and o.get('status') != 'resolved')
    if not effective.injury.get('dying') or effective.injury.get('dead') or not obligations:
        return _error('Patient has no current source-backed dying injury to stabilize')
    if '急救' not in healer.skills:
        return _error('Healer has no authoritative First Aid value')
    context = {'request_id': event_id, 'healer_character_id': healer_character_id,
               'character_id': character_id, 'obligation_ids': obligations, 'reason': reason}
    if combat_resources.is_managed(state):
        context['combat_id'] = state.combat.combat_id
    pending = state.pending_checks.get(healer.owner_id)
    if pending and pending.get('medical_context') == context:
        return {'ok': True, 'pending': True, 'check_id': pending['check_id'], 'medical_context': context}
    if check_lifecycle.blocker(state, healer.owner_id):
        return _error('Healer already has a pending check or Luck decision')
    if combat_resources.is_managed(state):
        if event_id in state.combat.actions:
            action = state.combat.actions[event_id]
            if action.get('medical_context') != context:
                return _error('Medical action identity already belongs to another declaration')
            return deepcopy(action.get('receipt', _result(state, action)))
        current = state.combat.order[state.combat.current_index] if state.combat.order else None
        if (not current or current.character_id != healer_character_id or current.defeated
                or state.combat.interaction or state.combat.phase != 'READY'
                or any(not a.get('completed') for a in state.combat.actions.values())
                or any(a.get('actor_id') == current.combatant_id and a.get('round') == state.combat.round_number
                       and a.get('completed') for a in state.combat.actions.values())):
            return _error('First Aid requires the capable current healer and an unused completed-action boundary')
        action = {'action_id': event_id, 'kind': 'stabilization', 'actor_id': current.combatant_id,
                  'round': state.combat.round_number, 'completed': False, 'checks': {}, 'medical_context': context}
        state.combat.actions[event_id] = action
        return _request_check(state, action, 'medical', healer, '急救', healer.skills['急救'])
    prior = next((e for e in state.resolved_check_events if e.get('medical_context', {}).get('request_id') == event_id), None)
    if prior:
        return {'ok': True, 'check_id': prior['check_id'], 'medical_context': deepcopy(prior['medical_context']), 'completed': True}
    candidate = {'type': 'skill', 'skill': '急救', 'skill_value': healer.skills['急救'], 'bonus_dice': 0,
                 'penalty_dice': 0, 'difficulty': 'regular', 'allow_luck': True, 'medical_context': context}
    registration = check_lifecycle.register(state, healer.owner_id, candidate, source={'action_context': reason})
    if state.autoroll_checks and registration.pending is not None:
        return _autoroll_medical(state, healer, registration.pending)
    return {'ok': registration.status == 'admitted', 'pending': True, 'check_id': registration.check_id,
            'medical_context': context}


def _autoroll_medical(state: GroupState, healer: Character, pending: dict[str, Any]) -> dict[str, Any]:
    result = roll_pending_check(state, pending, healer.owner_id)
    state.pending_checks.pop(healer.owner_id)
    options = luck.buyable_options(healer.skills['急救'], result.roll, result.tier,
                                  combat_resources.effective_character(state, healer).luck)
    if options:
        state.pending_luck_decisions[healer.owner_id] = {**pending, 'decision_id': pending['check_id'] + ':luck',
            'value': result.skill_value, 'roll': result.roll, 'original_tier': result.tier,
            'skill_name': '急救', 'display_label': None, 'is_counter': False, 'attacker_tier': None,
            'ranged_attacker': None, 'options': [{'tier': o.tier, 'cost': o.cost} for o in options]}
        return {'ok': True, 'pending_luck': True, 'check_id': pending['check_id'], 'medical_context': pending['medical_context']}
    evidence = {'event_id': pending['check_id'], 'check_id': pending['check_id'], 'timeline_id': state.timeline_id,
                'owner_id': healer.owner_id, 'character_id': healer.character_id, 'skill': '急救',
                'skill_value': result.skill_value, 'roll': result.roll, 'difficulty': 'regular',
                'success': result.success, 'outcome': result.tier, 'medical_context': deepcopy(pending['medical_context'])}
    state.resolved_check_events.append(evidence)
    return {'ok': True, 'completed': True, 'check_id': pending['check_id'], 'result': evidence}


def run_effect(state: GroupState, effect_id: str) -> dict[str, Any]:
    """Resolve an explicitly declared incident independently of shared timing keys."""
    combat_resources.initialize_working_state(state)
    identity = effect_id + ':incident'
    prior = next((e for e in state.combat.events if e['event_id'] == identity), None)
    if prior:
        return deepcopy(prior['data'])
    effect = next((e for e in state.combat.effects if e.id == effect_id), None)
    if not effect or effect.save_or_check.get('scope') != 'incident':
        return _error('Unknown declared incident effect')
    if not effect.save_or_check.get('rule_source') or effect.save_or_check.get('severity_id') is None:
        return _error('Incident requires reviewed severity evidence')
    receipt = combat_resources.record_roll(state, f'{state.combat.combat_id}:{identity}:damage',
                                           lambda: asdict(dice.roll_expression(effect.damage)))
    result = apply_managed_damage(state, effect.target_id, max(0, receipt['total']), event_id=identity,
                                        source_id=effect.source_id, damage_type=effect.damage_type, tags=effect.tags)
    if result['ok']:
        state.combat.effects.remove(effect)
    return result


def apply_managed_damage(
    state: GroupState, target_id: str, raw_damage: int, *, event_id: str,
    damage_type: str = 'physical', tags: list[str] | None = None,
    source_id: str = '', bypass_armor: bool = False, defer_injury: bool = False,
    entry_point: str = 'apply_managed_damage',
) -> dict[str, Any]:
    """Trusted single-hit primitive; transport must not expose caller hit amounts."""
    combat_resources.initialize_working_state(state)
    previous = next((e for e in state.combat.events if e['event_id'] == event_id), None)
    if previous:
        return dict(previous['data'])
    if not event_id or not isinstance(raw_damage, int) or isinstance(raw_damage, bool) or raw_damage < 0:
        return {'ok': False, 'error': 'Damage requires stable event identity and nonnegative integer'}
    target = combat.find_combatant(state, target_id)
    if target is None:
        return {'ok': False, 'error': 'Unknown combat participant'}
    _, armor_label, final = combat.planned_damage(state, target, raw_damage, damage_type, tags or [], bypass_armor)
    pc = combat.character_for_combatant(state, target) if target.is_pc else None
    effective = combat_resources.effective_character(state, pc) if pc else None
    before = effective.hp if effective else target.hp
    after = max(0, before - final)
    requires_con = effective is not None and effective.hp_max / 2 <= final < effective.hp_max
    if requires_con:
        assert pc is not None
        block = check_lifecycle.blocker(state, pc.owner_id)
        if block and not state.autoroll_checks:
            return combat.major_wound_blocked(state, pc, block, entry_point=entry_point)
        if state.combat.interaction and state.combat.interaction.get('owner_id') != pc.owner_id:
            return {'ok': False, 'error': 'Another combat interaction is unresolved'}
    injury: InjuryState = deepcopy(effective.injury) if effective else InjuryState()
    if effective:
        if final >= effective.hp_max:
            injury.update({'dead': True, 'unconscious': True, 'dying': False})
        elif final >= effective.hp_max / 2:
            injury['major_wound'] = True
        if after == 0:
            injury['unconscious'] = True
            injury['dying'] = bool(injury.get('major_wound') and not injury.get('dead'))
        assert pc is not None
        combat_resources.set_resource(state, pc, 'hp', after, event_id=event_id + ':hp', reason=source_id)
        combat_resources.set_injury(state, pc, injury, event_id=event_id + ':injury', reason=source_id)
        if injury.get('unconscious'):
            for tag in ('昏迷', '倒地'):
                combat_resources.set_status_tag(state, pc, tag, True, event_id=event_id + ':status:' + tag)
        if injury.get('dying'):
            ensure_dying_obligation(state, pc, event_id)
    target.hp = after
    target.defeated = after == 0 or bool(injury.get('unconscious'))
    card = combat.card_for(state, target)
    if card:
        card.hp = after
        if final and combat.damage_taken_trigger_tag() not in card.status_tags:
            card.status_tags.append(combat.damage_taken_trigger_tag())
    result: dict[str, Any] = {
        'ok': True, 'event_id': event_id, 'name': target.display_name, 'target': target.display_name,
        'target_id': target.combatant_id, 'side': target.side, 'raw_damage': raw_damage,
        'final_damage': final, 'armor_reduction': raw_damage - final, 'armor_label': armor_label,
        'damage_type': damage_type, 'weakness_bonus': 0,
        'private_notes': f'raw={raw_damage}, armor={armor_label}, source={source_id}',
        'hp_before': before, 'hp_after': after, 'hp': after, 'hp_max': target.hp_max,
        'injury': injury, 'major_wound_triggered': requires_con, 'defeated': target.defeated,
        'public_summary': f'{target.display_name} 受到 {final} 點傷害（戰鬥暫定）' + ('（部分傷害被擋下）' if armor_label else ''),
    }
    combat_resources.record_event(state, event_id, 'damage', data=result, reason=source_id)
    if requires_con and pc and not defer_injury:
        result['major_wound_check'] = request_injury_check(state, pc, event_id)
        # The damage receipt includes the wait and remains identical on retry.
        next(e for e in state.combat.events if e['event_id'] == event_id)['data'] = dict(result)
    state.last_combat_report = {'timeline_id': state.timeline_id,
                               'scenario_library_id': state.scenario_library_id,
                               'scenario_title': state.scenario_title, 'last_damage': dict(result)}
    return result




def managed_single_hit(
    state: GroupState, character: Character, damage: int, *, event_id: str, reason: str,
) -> dict[str, Any]:
    """Explicit resource adjustment's trusted injury seam, not primary attack adjudication."""
    target = next((p for p in state.combat.order if p.character_id == character.character_id), None)
    if target is None:
        return {'ok': False, 'error': 'Investigator is not in this battle'}
    return apply_managed_damage(state, target.combatant_id, damage, event_id=event_id,
                                source_id=reason, bypass_armor=True, entry_point='managed_single_hit')




def process_managed_timing(state: GroupState, timing: str, target_id: str) -> list[dict[str, Any]]:
    """Recorded damage draws and all-target ownership admission before any effect hit."""
    key = combat.timing_key(state, timing, target_id)
    if key in state.combat.processed_timings:
        return []
    results: list[dict[str, Any]] = []
    failed = False
    for effect in list(state.combat.effects):
        if effect.timing != timing or (target_id and effect.target_id not in (target_id, '__all__')):
            continue
        identity = f'{key}:effect:{effect.id}'
        if identity in state.combat.processed_timings:
            continue
        if effect.damage and (not effect.save_or_check.get('rule_source')
                              or effect.save_or_check.get('severity_id') is None):
            state.combat.phase = 'NEEDS_RULING'
            results.append({'ok': False, 'effect_id': effect.id, 'error': 'Effect requires reviewed severity and special-rule handling'})
            failed = True
            continue
        targets = ([p.combatant_id for p in state.combat.order if not p.defeated]
                   if effect.target_id == '__all__' else [effect.target_id])
        raw = 0
        if effect.damage:
            def draw_effect_damage(expression: str = effect.damage) -> dict[str, Any]:
                return asdict(dice.roll_expression(expression))
            try:
                receipt = combat_resources.record_roll(state, f'{state.combat.combat_id}:{identity}:damage', draw_effect_damage)
            except ValueError as exc:
                results.append({'ok': False, 'effect_id': effect.id, 'error': f'無法解析效果傷害：{exc}'})
                failed = True
                state.combat.phase = 'NEEDS_RULING'
                continue
            raw = max(0, receipt['total'])
        blocked = [(target, block) for target in targets
                   if (block := combat.major_wound_block_for(state, target, raw, damage_type=effect.damage_type, tags=effect.tags))]
        if blocked:
            failed = True
            for target, block in blocked:
                result = combat.major_wound_blocked(state, *block, entry_point='process_timing')
                result.update(effect_id=effect.id, target_id=target)
                results.append(result)
            continue
        required = [target for target in targets if (p := combat.find_combatant(state, target))
                    and combat.major_wound_pc(state, p, combat.planned_damage(state, p, raw, effect.damage_type, effect.tags, False)[2], 0)]
        applied = True
        for target in targets:
            if effect.damage:
                result = apply_managed_damage(state, target, raw, event_id=f'{identity}:{target}',
                                              damage_type=effect.damage_type, tags=effect.tags, source_id=effect.source_id, defer_injury=True)
                result['effect_id'] = effect.id
                results.append(result)
                applied = applied and result['ok']
        if applied:
            if required:
                characters = []
                for injured_target_id in required:
                    participant = combat.find_combatant(state, injured_target_id)
                    if participant is not None:
                        pc = combat.character_for_combatant(state, participant)
                        if pc is not None:
                            characters.append(pc)
                request_injury_checks(state, characters, identity)
            state.combat.processed_timings.append(identity)
            combat.tick_effect(effect)
            effect.save_or_check['next_round'] = state.mechanical_round + 1
            combat_resources.record_event(state, identity, 'timing', data={'effect_id': effect.id, 'timing': timing})
        else:
            failed = True
    state.combat.effects = [e for e in state.combat.effects if e.remaining_rounds is None or e.remaining_rounds > 0]
    if not failed:
        state.combat.processed_timings.append(key)
    return results


def _require_managed(state: GroupState) -> None:
    if not combat_resources.is_managed(state):
        raise combat.ModeMismatch("only a managed battle can be handled by the managed rules")


class ManagedOps:
    """Battles under the working-resource pipeline: receipts, owned waits, logical-time obligations."""

    def process_timing(self, state: GroupState, timing: str, target_id: str) -> list[dict[str, Any]]:
        _require_managed(state)
        return process_managed_timing(state, timing, target_id)

    def apply_damage(
        self, state: GroupState, target_name: str, raw_damage: int, *, damage_type: str,
        tags: list[str] | None, source_id: str, bypass_armor: bool, entry_point: str, event_id: str,
    ) -> dict[str, Any]:
        _require_managed(state)
        return apply_managed_damage(
            state, target_name, raw_damage, event_id=event_id or f'damage:{uuid.uuid4().hex}',
            damage_type=damage_type, tags=tags, source_id=source_id, bypass_armor=bypass_armor,
            entry_point=entry_point,
        )

    def sync_hp(self, state: GroupState, combatant: Combatant) -> None:
        _require_managed(state)
        if not combatant.is_pc:
            combat.sync_pc_hp(state, combatant)
            return
        pc = combat.character_for_combatant(state, combatant)
        if pc:
            combat_resources.set_resource(state, pc, 'hp', combatant.hp,
                                          event_id=f'healing:{uuid.uuid4().hex}', reason='Combat healing')

    def refuse_planning(self, state: GroupState) -> dict[str, Any] | None:
        _require_managed(state)
        if state.combat.interaction:
            return {'ok': False, 'error': 'Resolve the current interaction before NPC planning'}
        return None

    def refuse_advance(self, state: GroupState) -> dict[str, Any] | None:
        _require_managed(state)
        if state.combat.interaction or any(not a.get('completed') for a in state.combat.actions.values()):
            return {'ok': False, 'error': 'Combat action or interaction is unresolved'}
        return None

    def round_wrapped(self, state: GroupState) -> None:
        battle = state.combat
        clock_id = f'{battle.combat_id}:round:{battle.round_number}'
        clock_record = battle.actions.setdefault(clock_id, {'action_id': clock_id, 'completed': True,
                                                           'clock_round': state.mechanical_round + 1})
        due = process_postcombat(state, logical_round=clock_record['clock_round'], event_id=clock_id)
        if not due.get('ok') or any(r.get('pending') for r in due.get('results', [])):
            raise combat.TimingBlocked({'error': due.get('error', 'Due continuing CON check'),
                                        'blocked_by': 'pending_check', 'retain_due': True})

    def check_timing_result(self, result: dict[str, Any]) -> None:
        if not result.get('ok'):
            raise combat.TimingBlocked({**result, 'blocked_by': 'needs_ruling', 'retain_due': True})

    def after_timing(self, state: GroupState) -> None:
        if state.combat.interaction:
            raise combat.TimingBlocked({'error': 'Resolve the due injury check before advancing',
                                        'blocked_by': 'pending_check', 'retain_due': True})

    def keep_rolls(self, restored: GroupState, snapshot: dict[str, Any], retained_rolls: dict[str, Any]) -> None:
        restored.combat.roll_receipts.update(retained_rolls)
        restored.combat.revision += len(set(retained_rolls) - set(snapshot['combat'].get('roll_receipts', {})))


MANAGED_OPS: combat.ModeOps = ManagedOps()
