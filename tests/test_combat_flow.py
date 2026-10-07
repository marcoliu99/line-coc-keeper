"""Public managed action, injury, receipt and continuing-time regression tests."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from app import combat, combat_flow, combat_resources, dice
from app.models import Character, GroupState
from tests import combat_calls as calls

SOURCE = {'url': 'https://example.test/reviewed-scenario', 'revision': 'v1', 'sha256': 'abc', 'accessed': '2026-10-01'}


def check(tier='regular', value=60, roll=30):
    return dice.SkillCheckResult(value, roll, 0, 0, tier, tier not in ('fail', 'fumble'))


def battle(*, npc_first=False, autoroll=False, weapons=None, weapon_instances=None):
    pc = Character('Investigator', 'player', character_id='pc1', hp=10, hp_max=10,
                   dex=50 if npc_first else 80, luck=0, weapons=weapons or {}, weapon_instances=weapon_instances or {}, skills={'格鬥（鬥毆）': 60, '閃避': 40})
    state = GroupState(group_id='test', active=True, characters={'player': pc},
                       characters_by_id={'pc1': pc}, active_character_id_by_user={'player': 'pc1'},
                       autoroll_checks=autoroll)
    combat.begin_combat(state)
    card = combat.create_enemy_card(state, 'Cultist', dex=80 if npc_first else 40, hp=20,
                                   attacks=[{'id': 'claw', 'skill_value': 50, 'damage': '1d3', 'range_band': 'engaged'}],
                                   skills={'dodge': 20}, source=SOURCE)
    combat.add_enemy_card_to_combat(state, card.id)
    if npc_first:
        state.combat.current_index = 0
    return state, pc, state.combat.order[0 if npc_first else 1]


def finish(state, result=None, *, final=True):
    result = result or check()
    pending = state.pending_checks.pop('player')
    return combat_flow.on_authoritative_check_result(state, pending_entry=pending,
                                                     owner_id='player', result=result, final=final)


def declare(state, enemy, identity='attack'):
    return combat_flow.declare_action(state, action_id=identity, actor_id='pc:pc1',
                                      target_id=enemy.combatant_id, weapon_reference='unarmed', action_kind='melee')


def test_manual_attack_receipt_retry_restart_preserves_baseline():
    state, pc, enemy = battle()
    result = declare(state, enemy)
    assert result['phase'] == 'PLAYER_ROLL'
    with patch('app.dice.skill_check', return_value=check('fail', roll=80)), patch('app.dice.random.randint', return_value=2):
        completed = finish(state)
    assert completed['completed']
    assert enemy.hp == 18
    assert pc.hp == 10
    restored = GroupState.from_dict(state.to_dict())
    with patch('app.dice.random.randint') as rng:
        assert combat_flow.run_action(restored, 'attack') == completed
        assert declare(restored, restored.combat.order[1]) == completed
    rng.assert_not_called()
    assert not declare(restored, restored.combat.order[1], 'another')['ok']


def test_advance_receipt_retains_final_npc_wait_and_original_transition_after_reload():
    state, _, enemy = battle()
    declare(state, enemy)
    with patch('app.dice.skill_check', return_value=check('fail', roll=80)):
        assert finish(state, check('fail', roll=80))['completed']
        result = combat_flow.advance_combat(state, actor_id='pc:pc1', event_id='advance:opening')
    assert result['phase'] == 'PLAYER_CHOICE'
    receipt = next(e for e in state.combat.events if e['event_id'] == 'advance:opening')
    assert receipt['data']['transition']['ok']
    restored = GroupState.from_dict(deepcopy(state.to_dict()))
    before = deepcopy(restored.to_dict())
    with patch('app.dice.skill_check') as rng:
        assert combat_flow.advance_combat(restored, actor_id='pc:pc1', event_id='advance:opening') == result
    rng.assert_not_called()
    assert restored.to_dict() == before


def test_foreign_actor_and_stale_control_refuse_before_rng():
    state, _, enemy = battle()
    with patch('app.dice.random.randint') as rng:
        result = combat_flow.declare_action(state, action_id='bad', actor_id=enemy.combatant_id,
                                            target_id='pc:pc1', weapon_reference='unarmed')
        assert not result['ok']
        declare(state, enemy)
        pending = deepcopy(state.pending_checks['player'])
        pending['combat_context']['interaction_id'] = 'stale'
        assert not combat_flow.validate_pending_context(state, pending, 'player')['ok']
        with pytest.raises(ValueError):
            combat_flow.roll_pending_check(state, pending, 'player')
    rng.assert_not_called()


def test_actual_bot_roll_is_cached_even_before_result_callback():
    state, _, enemy = battle()
    declare(state, enemy)
    pending = state.pending_checks['player']
    with patch('app.dice.skill_check', return_value=check()) as rng:
        first = combat_flow.roll_pending_check(state, pending, 'player')
        restored = GroupState.from_dict(state.to_dict())
        second = combat_flow.roll_pending_check(restored, restored.pending_checks['player'], 'player')
    assert first == second
    assert rng.call_count == 1


def test_failed_attacker_can_be_hit_by_successful_fightback_normal_damage():
    state, pc, enemy = battle(npc_first=True)
    plan = calls.plan_enemy_turn(state, enemy.display_name)
    with patch('app.dice.skill_check', return_value=check('fail', roll=90)):
        result = calls.resolve_enemy_action(state, plan['plan_id'])
    assert result['phase'] == 'PLAYER_CHOICE'
    choice = state.combat.interaction['interaction_id']
    combat_flow.submit_choice(state, interaction_id=choice, owner_id='player', choice='counter')
    with patch('app.dice.random.randint', return_value=2) as rng:
        result = finish(state, check('extreme', roll=5))
    assert result['completed']
    assert result['result']['opposed'] == 'defender_wins'
    assert enemy.hp == 18
    assert pc.hp == 10
    assert rng.call_count == 1  # Extreme counter still normal, not max/impale.


@pytest.mark.parametrize(('damage', 'starting_hp', 'injury'), [
    (3, 2, {'unconscious': True, 'dying': False}),
    (6, 10, {'major_wound': True}),
    (6, 4, {'major_wound': True, 'unconscious': True, 'dying': True}),
    (10, 10, {'dead': True, 'unconscious': True, 'dying': False}),
])
def test_structured_single_hit_injury_includes_zero(damage, starting_hp, injury):
    state, pc, _ = battle()
    combat_resources.set_resource(state, pc, 'hp', starting_hp, event_id='setup')
    result = calls.managed_single_hit(state, pc, damage, event_id='hit', reason='Authorized incident')
    assert result['ok']
    effective = combat_resources.effective_character(state, pc)
    assert effective.hp == max(0, starting_hp - damage)
    for key, value in injury.items():
        assert effective.injury[key] == value
    assert pc.hp == 10
    assert calls.managed_single_hit(state, pc, damage, event_id='hit', reason='retry') == result
    if injury.get('dead'):
        assert not state.pending_checks
    elif injury.get('major_wound'):
        assert state.pending_checks['player']['skill'] == 'CON'


def test_injury_ownership_block_is_all_or_nothing():
    state, pc, _ = battle()
    state.pending_checks['player'] = {'type': 'skill', 'skill': 'Other'}
    before = state.to_dict()
    assert calls.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')['blocked_by'] == 'pending_check'
    assert state.to_dict() == before


def test_major_wound_failed_con_sets_effective_unconscious_only():
    state, pc, _ = battle()
    calls.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')
    result = finish(state, check('fail', roll=90))
    assert result['completed']
    assert combat_resources.effective_character(state, pc).injury['unconscious']
    assert not pc.injury


def test_dying_settlement_transfers_and_due_check_survives_restart():
    state, pc, _ = battle()
    combat_resources.set_resource(state, pc, 'hp', 4, event_id='setup')
    calls.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')
    finish(state)
    obligations = combat_flow.postcombat_obligations(state)
    assert obligations[0]['next_trigger']['round'] == 2
    preview = combat_resources.get_settlement(state, obligations=obligations)
    combat_resources.commit_settlement(state, preview['settlement_id'], obligations=obligations)
    assert pc.injury['dying']
    assert combat_flow.process_postcombat(state, logical_round=1, event_id='round1')['ok']
    result = combat_flow.process_postcombat(state, logical_round=2, event_id='round2')
    assert result['results'][0]['pending']
    restored = GroupState.from_dict(state.to_dict())
    assert not combat_flow.process_postcombat(restored, logical_round=3, event_id='skip')['ok']
    pending = restored.pending_checks.pop('player')
    assert combat_flow.validate_pending_context(restored, pending, 'player')['ok']
    combat_flow.on_authoritative_check_result(restored, pending_entry=pending, owner_id='player', result=check('fail'))
    assert restored.characters_by_id['pc1'].injury['dead']


def test_explicit_severity_effect_receipt_retry_and_transfer():
    state, pc, _ = battle()
    result = combat_flow.declare_effect(state, effect_id='acid', target_id='pc:pc1', severity_id='minor',
                                       scope='round', reason='Authorized acid exposure', stop_condition='Washed off')
    assert result['ok']
    with patch('app.dice.random.randint', return_value=2) as rng:
        first = calls.process_timing(state, 'round_end')
        assert calls.process_timing(state, 'round_end') == []
    assert rng.call_count == 1
    assert first[0]['hp_after'] == 8
    assert pc.hp == 10
    obligations = combat_flow.postcombat_obligations(state)
    assert obligations[0]['effect']['id'] == 'acid'
    assert obligations[0]['next_trigger']['round'] == 1
    assert not combat_flow.declare_effect(state, effect_id='poison', target_id='pc:pc1', severity_id='severe',
                                         scope='round', reason='Poison', stop_condition='Antidote', special_rule='poison')['ok']


def test_raw_outcome_is_rejected_for_managed_npc():
    state, pc, enemy = battle(npc_first=True)
    plan = calls.plan_enemy_turn(state, enemy.display_name)
    before = state.to_dict()
    assert not calls.resolve_enemy_action(state, plan['plan_id'], {'hit': True, 'damage': 10})['ok']
    assert state.to_dict() == before
    assert pc.hp == 10


def test_autoroll_major_wound_retains_unrelated_pending_control():
    state, pc, _ = battle(autoroll=True)
    state.pending_checks['player'] = {'type': 'sanity', 'skill_value': 40}
    with patch('app.dice.skill_check', return_value=check('fail')):
        result = calls.managed_single_hit(state, pc, 6, event_id='incident', reason='Authorized')
    assert result['ok']
    assert state.pending_checks['player'] == {'type': 'sanity', 'skill_value': 40}
    assert combat_resources.effective_character(state, pc).injury['unconscious']
    assert not pc.injury


def test_ranged_successful_dive_applies_penalty_instead_of_automatic_miss():
    state, pc, enemy = battle(npc_first=True)
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    card.source.update(attack_mode='single_shot', distance_yards=10, base_range_yards=20)
    card.attacks[0].range_band = 'near'
    card.attacks[0].ammo_or_uses = 3
    plan = calls.plan_enemy_turn(state)
    result = calls.resolve_enemy_action(state, plan['plan_id'])
    assert result['phase'] == 'PLAYER_CHOICE'
    assert not state.combat.roll_receipts  # Shot delayed until final dive/Luck.
    combat_flow.submit_choice(state, interaction_id=state.combat.interaction['interaction_id'], owner_id='player', choice='dive')
    with patch('app.dice.skill_check', return_value=check()) as shot, patch('app.dice.random.randint', return_value=2):
        result = finish(state, check())
    assert result['completed']
    assert result['result']['hit']
    assert shot.call_args.kwargs['penalty_dice'] == 1
    assert combat_resources.effective_character(state, pc).hp == 8
    assert pc.hp == 10
    assert card.attacks[0].ammo_or_uses == 2


def test_manual_luck_wait_retains_attack_and_no_damage_until_final():
    state, _, enemy = battle()
    declare(state, enemy)
    pending = state.pending_checks.pop('player')
    combat_flow.on_authoritative_check_result(state, pending_entry=pending, owner_id='player', result=check('fail'), final=False)
    assert state.combat.phase == 'LUCK_DECISION'
    assert enemy.hp == 20
    state.pending_luck_decisions['player'] = {**pending, 'original_tier': 'fail'}
    restored = GroupState.from_dict(state.to_dict())
    assert combat_flow.validate_pending_context(restored, restored.pending_luck_decisions['player'], 'player')['ok']
    final_pending = restored.pending_luck_decisions.pop('player')
    with patch('app.dice.skill_check', return_value=check('fail')), patch('app.dice.random.randint', return_value=2):
        completed = combat_flow.on_authoritative_check_result(restored, pending_entry=final_pending, owner_id='player', result=check())
    assert completed['completed']
    assert restored.combat.roll_receipts[next(k for k in restored.combat.roll_receipts if k.endswith(':attack'))]['tier'] == 'fail'
    assert restored.combat.actions['attack']['checks']['attack']['tier'] == 'regular'


def test_source_ruling_resumes_unknown_weapon_without_arbitrary_damage():
    state, _, enemy = battle()
    result = combat_flow.declare_action(state, action_id='unknown', actor_id='pc:pc1', target_id=enemy.combatant_id,
                                        weapon_reference='invented weapon')
    assert result['phase'] == 'NEEDS_RULING'
    result = combat_flow.resolve_ruling(state, action_id='unknown', event_id='ruling', reason='Verified human unarmed',
                                       decision='resume', weapon_reference='unarmed')
    assert result['phase'] == 'PLAYER_ROLL'
    assert state.pending_checks['player']['skill'] == '格鬥（鬥毆）'


def test_unsupported_special_cancellation_preserves_evidence():
    state, _, enemy = battle(npc_first=True)
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    from app.models import SpecialAbility
    card.abilities.append(SpecialAbility(id='spell', name='Spell'))
    plan = calls.plan_enemy_turn(state)
    result = calls.resolve_enemy_action(state, plan['plan_id'])
    assert result['phase'] == 'NEEDS_RULING'
    result = combat_flow.resolve_ruling(state, action_id='npc:' + plan['plan_id'], event_id='cancel',
                                       reason='Controller cancels unsupported special', decision='cancel')
    assert result['completed']
    assert state.combat.actions['npc:' + plan['plan_id']]['cancelled']
    assert card.abilities[0].usage == {}


def test_multi_target_effect_reserves_each_injury_check_then_one_wait_at_a_time():
    state, first, _ = battle()
    second = Character('Second', 'second-player', character_id='pc2', hp=10, hp_max=10)
    state.characters['second-player'] = second
    state.characters_by_id['pc2'] = second
    state.active_character_id_by_user['second-player'] = 'pc2'
    from app.models import Combatant, EffectState
    combat_resources.admit_character(state, second)
    state.combat.order.append(Combatant(name=second.name, character_id='pc2', combatant_id='pc:pc2', is_pc=True, hp=10, hp_max=10))
    state.combat.effects.append(EffectState(id='ceiling', target_id='__all__', timing='round_end', damage='6', remaining_rounds=1,
        save_or_check={'severity_id': 'severe', 'rule_source': SOURCE, 'stop_condition': 'Incident complete'}))
    results = calls.process_timing(state, 'round_end')
    assert all(r['ok'] for r in results)
    assert set(state.pending_checks) == {'player', 'second-player'}
    assert state.combat.interaction['owner_id'] == 'player'
    second_pending = state.pending_checks['second-player']
    assert not combat_flow.validate_pending_context(state, second_pending, 'second-player')['ok']
    finish(state)
    assert state.combat.interaction['owner_id'] == 'second-player'
    pending = state.pending_checks.pop('second-player')
    combat_flow.on_authoritative_check_result(state, pending_entry=pending, owner_id='second-player', result=check())
    assert state.combat.phase == 'READY'
    assert combat_resources.effective_character(state, first).hp == 4
    assert combat_resources.effective_character(state, second).hp == 4
    assert first.hp == second.hp == 10


def test_dying_settlement_after_current_round_end_retains_following_end_trigger():
    state, pc, _ = battle()
    combat_resources.set_resource(state, pc, 'hp', 4, event_id='setup')
    calls.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')
    finish(state)
    assert combat_flow.process_postcombat(state, logical_round=1, event_id='current-end')['ok']
    assert not state.pending_checks
    obligations = combat_flow.postcombat_obligations(state)
    assert obligations[0]['next_trigger']['round'] == 2
    preview = combat_resources.get_settlement(state, obligations=obligations)
    combat_resources.commit_settlement(state, preview['settlement_id'], obligations=obligations)
    assert combat_flow.process_postcombat(state, logical_round=2, event_id='following-end')['results'][0]['pending']


def test_system_action_identity_cannot_be_declared_or_run():
    state, _, enemy = battle()
    assert not declare(state, enemy, 'system:continuing-state')['ok']
    assert not combat_flow.run_action(state, 'system:continuing-state')['ok']
    assert 'obligation_baseline' in state.combat.actions['system:continuing-state']


def test_initiative_requires_exact_participants_and_no_pending_wait():
    state, _, enemy = battle()
    ids = [p.combatant_id for p in state.combat.order]
    assert not combat_flow.set_initiative(state, actor_ids=[ids[0]], event_id='bad', reason='Controller')['ok']
    assert combat_flow.set_initiative(state, actor_ids=list(reversed(ids)), event_id='order', reason='Controller')['ok']
    assert state.combat.order[state.combat.current_index].combatant_id == ids[0]
    declare(state, enemy)
    assert not combat_flow.set_initiative(state, actor_ids=ids, event_id='wait', reason='Controller')['ok']


def test_effect_block_retains_original_damage_receipt_and_no_partial_targets():
    state, pc, _ = battle()
    state.pending_checks['player'] = {'type': 'skill', 'skill': 'Other'}
    combat_flow.declare_effect(state, effect_id='acid', target_id='pc:pc1', severity_id='severe', scope='round',
                               reason='Reviewed acid', stop_condition='Removed')
    with patch('app.dice.random.randint', return_value=6) as rng:
        blocked = calls.process_timing(state, 'round_end')
        assert blocked[0]['blocked_by'] == 'pending_check'
        assert combat_resources.effective_character(state, pc).hp == 10
        state.pending_checks.clear()
        applied = calls.process_timing(state, 'round_end')
    assert rng.call_count == 1
    assert applied[0]['hp_after'] == 4
    assert pc.hp == 10


def test_turn_rollback_restores_earlier_effect_working_hp_and_events_but_retains_rolls():
    state, pc, _ = battle()
    state.combat.current_index = len(state.combat.order) - 1
    state.pending_checks['player'] = {'type': 'skill', 'skill': 'Other'}
    for identity, severity in [('small', 'minor'), ('large', 'severe')]:
        combat_flow.declare_effect(state, effect_id=identity, target_id='pc:pc1', severity_id=severity,
                                   scope='round', reason='Reviewed hazard', stop_condition='Removed')
    before = deepcopy(state.to_dict())
    with patch('app.dice.random.randint', side_effect=[2, 6]) as rng:
        result = calls.advance_turn(state)
    assert not result['ok']
    assert combat_resources.effective_character(state, pc).hp == 10
    assert state.combat.working_resources == before['combat']['working_resources']
    assert state.combat.events == before['combat']['events']
    assert state.combat.actions == before['combat']['actions']
    assert state.combat.processed_timings == before['combat']['processed_timings']
    assert state.combat.current_index == before['combat']['current_index']
    assert len(state.combat.roll_receipts) == 2
    assert rng.call_count == 2
    with patch('app.dice.random.randint') as retry_rng:
        assert not calls.advance_turn(state)['ok']
    retry_rng.assert_not_called()


def test_prior_committed_effect_rollback_restores_due_schedule_with_cached_draw():
    state, pc, _ = battle()
    combat_flow.declare_effect(state, effect_id='acid', target_id='pc:pc1', severity_id='minor', scope='round',
                               reason='Reviewed acid', stop_condition='Washed off')
    obligations = combat_flow.postcombat_obligations(state)
    preview = combat_resources.get_settlement(state, obligations=obligations)
    combat_resources.commit_settlement(state, preview['settlement_id'])
    combat.begin_combat(state)
    with patch('app.dice.random.randint', return_value=2) as rng:
        assert combat_flow.process_postcombat(state, logical_round=1, event_id='round1')['ok']
        assert combat_resources.effective_character(state, pc).hp == 8
        assert pc.hp == 10
        assert state.postcombat_obligations[0]['next_trigger']['round'] == 1
        combat_resources.rollback_combat(state, event_id='rollback', reason='Cancel new battle')
        assert state.mechanical_round == 1
        assert state.postcombat_obligations[0]['next_trigger']['round'] == 1
        assert not combat_flow.process_postcombat(state, logical_round=2, event_id='skip')['ok']
        assert combat_flow.process_postcombat(state, logical_round=1, event_id='catchup')['ok']
    assert rng.call_count == 1
    assert pc.hp == 8
    assert state.postcombat_obligations[0]['next_trigger']['round'] == 2


def test_prior_pending_dying_control_recovers_after_projected_resolution_and_rollback():
    state, pc, _ = battle()
    combat_resources.set_resource(state, pc, 'hp', 4, event_id='setup')
    calls.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')
    finish(state)
    preview = combat_resources.get_settlement(state, obligations=combat_flow.postcombat_obligations(state))
    combat_resources.commit_settlement(state, preview['settlement_id'])
    combat_flow.process_postcombat(state, logical_round=1, event_id='round1')
    combat_flow.process_postcombat(state, logical_round=2, event_id='round2')
    old_check_id = state.pending_checks['player']['check_id']
    combat.begin_combat(state)
    assert 'pc1' in state.combat.working_resources  # zero-HP participant still admitted
    with patch('app.dice.skill_check', return_value=check()) as rng:
        pending = state.pending_checks['player']
        result = combat_flow.roll_pending_check(state, pending, 'player')
        state.pending_checks.pop('player')
        combat_flow.on_authoritative_check_result(state, pending_entry=pending, owner_id='player', result=result)
        combat_resources.rollback_combat(state, event_id='rollback', reason='Cancel new battle')
        assert state.postcombat_obligations[0]['status'] == 'pending'
        recovered = combat_flow.process_postcombat(state, logical_round=2, event_id='recover')
        assert recovered['results'][0]['pending']
        assert state.pending_checks['player']['check_id'] != old_check_id
        pending = state.pending_checks['player']
        result = combat_flow.roll_pending_check(state, pending, 'player')
        state.pending_checks.pop('player')
        combat_flow.on_authoritative_check_result(state, pending_entry=pending, owner_id='player', result=result)
    assert rng.call_count == 1
    assert state.postcombat_obligations[0]['status'] == 'future'
    assert state.postcombat_obligations[0]['next_trigger']['round'] == 3


def medical_state(*, active_combat=False, autoroll=True):
    patient = Character('Patient', 'patient-player', character_id='patient', hp=0, hp_max=10, dex=20,
                        injury={'major_wound': True, 'dying': True, 'unconscious': True})
    healer = Character('Healer', 'healer-player', character_id='healer', dex=90, luck=0, skills={'急救': 70})
    obligation = {'obligation_id': 'old:dying:patient:injury1', 'combat_id': 'old', 'character_id': 'patient',
                  'kind': 'dying', 'status': 'future', 'next_trigger': {'round': 2, 'timing': 'round_end'},
                  'stop_condition': 'First Aid or death', 'rule_source': SOURCE, 'roll_receipts': {}, 'processed_timings': []}
    state = GroupState('medical', active=True, timeline_id='timeline', autoroll_checks=autoroll,
                       characters={'patient-player': patient, 'healer-player': healer},
                       characters_by_id={'patient': patient, 'healer': healer},
                       active_character_id_by_user={'patient-player': 'patient', 'healer-player': 'healer'},
                       postcombat_obligations=[obligation])
    if active_combat:
        combat.begin_combat(state)
    return state, patient, healer


def test_medical_check_binds_distinct_healer_patient_and_consumes_once():
    state, patient, healer = medical_state()
    with patch('app.dice.skill_check', return_value=check()) as rng:
        check_receipt = combat_flow.request_stabilization_check(state, healer_character_id='healer', character_id='patient',
                                                               event_id='firstaid', reason='Treat this dying patient')
    assert rng.call_count == 1
    source_check = check_receipt['check_id']
    result = combat_flow.stabilize_investigator(state, character_id='patient', source_check_id=source_check,
                                              event_id='stabilize', reason='Successful bound First Aid')
    assert result['ok']
    assert patient.injury == {'major_wound': True, 'dying': False, 'unconscious': True}
    assert patient.hp == 0
    assert combat_flow.stabilize_investigator(state, character_id='patient', source_check_id=source_check,
                                             event_id='stabilize', reason='retry') == result
    patient.injury['dying'] = True
    state.postcombat_obligations[0]['status'] = 'future'
    state.postcombat_obligations[0]['obligation_id'] = 'old:dying:patient:injury2'
    assert not combat_flow.stabilize_investigator(state, character_id='patient', source_check_id=source_check,
                                                event_id='reused', reason='New injury')['ok']
    assert not combat_flow.stabilize_investigator(state, character_id='healer', source_check_id=source_check,
                                                event_id='other-patient', reason='Wrong patient')['ok']
    assert healer.hp == 10


def test_historic_unbound_firstaid_success_cannot_stabilize_later_injury():
    state, patient, _ = medical_state()
    state.resolved_check_events.append({'check_id': 'historic', 'timeline_id': 'timeline', 'skill': '急救', 'success': True})
    assert not combat_flow.stabilize_investigator(state, character_id='patient', source_check_id='historic',
                                                event_id='stabilize', reason='Old success')['ok']
    assert patient.injury['dying']


def test_medical_action_obeys_current_actor_wait_and_consumes_turn():
    state, patient, _ = medical_state(active_combat=True, autoroll=False)
    assert not combat_flow.request_stabilization_check(state, healer_character_id='patient', character_id='patient',
                                                       event_id='wrong', reason='Not healer')['ok']
    result = combat_flow.request_stabilization_check(state, healer_character_id='healer', character_id='patient',
                                                    event_id='medical', reason='Treat dying patient')
    assert result['phase'] == 'PLAYER_ROLL'
    assert not combat_flow.request_stabilization_check(state, healer_character_id='healer', character_id='patient',
                                                       event_id='second', reason='Extra turn')['ok']
    pending = state.pending_checks.pop('healer-player')
    result = combat_flow.on_authoritative_check_result(state, pending_entry=pending, owner_id='healer-player', result=check())
    assert result['completed']
    stabilized = combat_flow.stabilize_investigator(state, character_id='patient', source_check_id=pending['check_id'],
                                                   event_id='stabilize', reason='Bound First Aid')
    assert stabilized['ok']
    assert not combat_resources.effective_character(state, patient).injury['dying']
    assert patient.injury['dying']  # remains provisional until settlement


def test_rolled_back_medical_success_cannot_publish_later_stabilization():
    state, patient, _ = medical_state(active_combat=True, autoroll=False)
    combat_flow.request_stabilization_check(state, healer_character_id='healer', character_id='patient',
                                           event_id='medical', reason='Treat dying patient')
    pending = state.pending_checks.pop('healer-player')
    combat_flow.on_authoritative_check_result(state, pending_entry=pending, owner_id='healer-player', result=check())
    combat_resources.rollback_combat(state, event_id='rollback', reason='Cancel provisional treatment')
    assert not combat_flow.stabilize_investigator(state, character_id='patient', source_check_id=pending['check_id'],
                                                event_id='stabilize', reason='Rolled-back success')['ok']
    assert patient.injury['dying']


def test_second_battle_same_enemy_never_reuses_closed_retained_order():
    state, pc, enemy = battle()
    original_id = state.combat.combat_id
    preview = combat_resources.get_settlement(state, obligations=combat_flow.postcombat_obligations(state))
    combat_resources.commit_settlement(state, preview['settlement_id'])
    added = combat.add_combatant(state, enemy.name, 40, 20, attacks=[{'id': 'claw', 'damage': '1d3'}], source=SOURCE)
    assert state.combat.active
    assert state.combat.combat_id != original_id
    assert not added.reused
    assert added.combatant is not enemy
    assert pc.hp == 10


def test_incident_damage_uses_own_identity_even_after_shared_timing_processed():
    state, pc, _ = battle()
    assert calls.process_timing(state, 'round_end', 'pc:pc1') == []
    with patch('app.dice.random.randint', return_value=2) as rng:
        result = combat_flow.declare_effect(state, effect_id='incident', target_id='pc:pc1', severity_id='minor',
                                           scope='incident', reason='Reviewed incident', stop_condition='Incident complete')
        duplicate = combat_flow.declare_effect(state, effect_id='incident', target_id='pc:pc1', severity_id='minor',
                                              scope='incident', reason='retry', stop_condition='Incident complete')
    assert result == duplicate
    assert rng.call_count == 1
    assert combat_resources.effective_character(state, pc).hp == 8
    assert state.combat.effects == []


def test_blocked_incident_retries_same_draw_and_cannot_settle_due_hazard():
    state, pc, _ = battle()
    state.pending_checks['player'] = {'type': 'skill', 'check_id': 'other'}
    with patch('app.dice.random.randint', return_value=6) as rng:
        result = combat_flow.declare_effect(state, effect_id='incident', target_id='pc:pc1', severity_id='severe',
                                           scope='incident', reason='Reviewed incident', stop_condition='Incident complete')
        assert result['blocked_by'] == 'pending_check'
        assert combat_resources.effective_character(state, pc).hp == 10
        with pytest.raises(combat_resources.CombatAdmissionError):
            combat_flow.postcombat_obligations(state)
        state.pending_checks.clear()
        applied = combat_flow.run_effect(state, 'incident')
    assert applied['ok']
    assert rng.call_count == 1
    assert combat_resources.effective_character(state, pc).hp == 4


def test_correction_reconciliation_is_explicit_injury_review_without_reroll():
    state, pc, _ = battle()
    calls.managed_single_hit(state, pc, 10, event_id='fatal', reason='Initial ruling')
    original_rolls = deepcopy(state.combat.roll_receipts)
    combat_resources.correct_event(state, 'fatal:hp', event_id='correction', changes={'after': 8}, reason='Verified smaller hit')
    assert state.combat.phase == 'NEEDS_RULING'
    with patch('app.dice.random.randint') as rng:
        result = combat_flow.reconcile_correction(state, event_id='review', reason='Review corrected patient injury',
            injury_by_character={'pc1': {}}, acknowledge_action_ids=list(state.combat.actions))
    assert result['ok']
    assert state.combat.roll_receipts == original_rolls
    assert combat_resources.effective_character(state, pc).hp == 8
    assert not combat_resources.effective_character(state, pc).injury
    assert state.combat.phase == 'READY'
    rng.assert_not_called()


@pytest.mark.parametrize(('distance', 'base', 'allowed'), [(21, 20, True), (81, 20, False), (float('nan'), 20, False), (1, 0, False)])
def test_npc_ranged_trusted_range_difficulty_or_refusal_before_draw(distance, base, allowed):
    state, _, enemy = battle(npc_first=True)
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    card.source.update(attack_mode='single_shot', distance_yards=distance, base_range_yards=base)
    card.attacks[0].ammo_or_uses = 3
    card.attacks[0].range_band = 'near'
    plan = calls.plan_enemy_turn(state)
    with patch('app.dice.random.randint') as rng:
        result = calls.resolve_enemy_action(state, plan['plan_id'])
    assert result['ok'] == allowed
    if allowed:
        assert state.combat.actions['npc:' + plan['plan_id']]['difficulty'] == 'hard'
    else:
        assert result['phase'] == 'NEEDS_RULING'
    assert card.attacks[0].ammo_or_uses == 3
    rng.assert_not_called()


@pytest.mark.parametrize('ownership', ['missing', 'inventory', 'instance'])
def test_ammo_free_player_weapon_requires_owned_evidence(ownership):
    state, _, enemy = battle(
        weapons={'large club': {}} if ownership == 'inventory' else None,
        weapon_instances={'large club': {'definition_id': 'i.weapon.club-large'}} if ownership == 'instance' else None,
    )
    with patch('app.dice.skill_check') as rng:
        result = combat_flow.declare_action(state, action_id='club', actor_id='pc:pc1',
            target_id=enemy.combatant_id, weapon_reference='large club')
    rng.assert_not_called()
    assert result['phase'] == ('NEEDS_RULING' if ownership == 'missing' else 'PLAYER_ROLL')
    if ownership == 'missing':
        replay = combat_flow.resolve_ruling(state, action_id='club', event_id='retry:club',
            reason='Same unsupported weapon', decision='resume')
        assert replay['phase'] == 'NEEDS_RULING'
        assert not state.pending_checks


@pytest.mark.parametrize('mapped', [False, True])
def test_ammo_free_npc_weapon_requires_matching_reviewed_attack(mapped):
    state, _, enemy = battle(npc_first=True)
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    card.skills['fighting-brawl'] = 99  # Skill alone does not authorize a club.
    card.source['damage_bonus'] = '1d4'
    if mapped:
        card.attacks[0].id = 'i.weapon.club-large'
    with patch('app.dice.skill_check', return_value=check('fail', value=50, roll=90)) as rng:
        result = combat_flow.declare_action(state, action_id='npc:club', actor_id=enemy.combatant_id,
            target_id='pc:pc1', weapon_reference='large club')
    assert result['phase'] == ('PLAYER_CHOICE' if mapped else 'NEEDS_RULING')
    assert rng.call_count == int(mapped)
    if mapped:
        assert state.combat.actions['npc:club']['skill_value'] == card.attacks[0].skill_value
    else:
        assert not state.combat.roll_receipts
        assert combat_flow.resolve_ruling(state, action_id='npc:club', event_id='retry:npcclub',
            reason='Skill still does not establish weapon', decision='resume')['phase'] == 'NEEDS_RULING'


def test_verified_npc_ruling_resumes_missing_damage_bonus_from_card():
    state, _, enemy = battle(npc_first=True)
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    card.stats = {}
    card.source.pop('damage_bonus', None)
    card.attacks[0].id = 'i.weapon.club-large'
    result = combat_flow.declare_action(state, action_id='npc:club', actor_id=enemy.combatant_id,
        target_id='pc:pc1', weapon_reference='large club')
    assert result['phase'] == 'NEEDS_RULING'
    card.source['damage_bonus'] = '1d4'
    with patch('app.dice.skill_check', return_value=check('fail', roll=90)) as rng:
        result = combat_flow.resolve_ruling(state, action_id='npc:club', event_id='ruling:npcclub',
            reason='Verified scenario DB', decision='resume')
        restored = GroupState.from_dict(deepcopy(state.to_dict()))
        assert combat_flow.resolve_ruling(restored, action_id='npc:club', event_id='ruling:npcclub',
            reason='Verified scenario DB', decision='resume') == result
    assert rng.call_count == 1
    assert result['phase'] == 'PLAYER_CHOICE'
    assert state.combat.actions['npc:club']['db'] == '1d4'


def test_verified_npc_single_shot_ruling_resumes_missing_distance():
    from app import combat_rules
    weapon = next(w for w in combat_rules.weapon_catalog() if w.attack_mode == 'single_shot'
                  and w.db_policy == 'none' and not w.ruling_reason and not w.distance_bands)
    state, _, enemy = battle(npc_first=True)
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    card.attacks[0].id = weapon.id
    card.attacks[0].ammo_or_uses = 3
    result = combat_flow.declare_action(state, action_id='npc:shot', actor_id=enemy.combatant_id,
        target_id='pc:pc1', weapon_reference=weapon.id, action_kind='single_shot')
    assert result['phase'] == 'NEEDS_RULING'
    with patch('app.dice.skill_check') as rng:
        result = combat_flow.resolve_ruling(state, action_id='npc:shot', event_id='ruling:shot',
            reason='Verified distance', decision='resume', distance_yards=1)
    rng.assert_not_called()  # Player first chooses whether to dive.
    assert result['phase'] == 'PLAYER_CHOICE'
    assert state.combat.actions['npc:shot']['npc_attack_id'] == weapon.id
    assert card.attacks[0].ammo_or_uses == 3


def test_unknown_effect_stop_rejected_and_recorded_stop_replayed_after_reload():
    state, _, _ = battle()
    assert combat_flow.declare_effect(state, effect_id='hazard', target_id='pc:pc1', severity_id='minor',
        scope='round', reason='Reviewed hazard', stop_condition='leave')['ok']
    before = deepcopy(state.to_dict())
    for effect_id, source in [('typo', state.combat.combat_id), ('hazard', 'wrong:source')]:
        result = combat_flow.stop_effect(state, effect_id=effect_id, combat_id=source,
            event_id='stop:missing', reason='Source must match')
        assert not result['ok']
        assert state.to_dict() == before
    result = combat_flow.stop_effect(state, effect_id='hazard', event_id='stop:hazard', reason='Left')
    assert result['ok'] and not state.combat.effects
    restored = GroupState.from_dict(deepcopy(state.to_dict()))
    before = deepcopy(restored.to_dict())
    assert combat_flow.stop_effect(restored, effect_id='hazard', event_id='stop:hazard', reason='Left') == result
    assert restored.to_dict() == before
    assert not combat_flow.stop_effect(restored, effect_id='typo', event_id='stop:hazard', reason='Left')['ok']


def test_a_pending_settlement_preview_refuses_advancing_and_declaring_and_can_still_be_confirmed():
    state, _, enemy = battle()
    enemy.hp = 0
    enemy.defeated = True
    combat.card_for(state, enemy).hp = 0
    preview = combat_resources.get_settlement(state)
    settlement_id = preview['settlement_id']
    revision = state.combat.revision
    round_number = state.combat.round_number
    advanced = combat_flow.advance_combat(state, actor_id='pc:pc1', event_id='adv:1', skip=True)
    declared = declare(state, enemy)
    for refused in (advanced, declared):
        assert not refused['ok'] and refused['settlement_id'] == settlement_id
        assert 'confirm_combat_settlement' in refused['error']
    assert (state.combat.revision, state.combat.round_number) == (revision, round_number)
    assert combat_resources.commit_settlement(state, settlement_id)['status'] == 'committed'
