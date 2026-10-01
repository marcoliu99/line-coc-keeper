"""Public managed action, injury, receipt and continuing-time regression tests."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from app import combat, combat_flow, combat_resources, dice
from app.models import Character, GroupState

SOURCE = {'url': 'https://example.test/reviewed-scenario', 'revision': 'v1', 'sha256': 'abc', 'accessed': '2026-10-01'}


def check(tier='regular', value=60, roll=30):
    return dice.SkillCheckResult(value, roll, 0, 0, tier, tier not in ('fail', 'fumble'))


def battle(*, npc_first=False, autoroll=False):
    pc = Character('Investigator', 'player', character_id='pc1', hp=10, hp_max=10,
                   dex=50 if npc_first else 80, luck=0, skills={'格鬥（鬥毆）': 60, '閃避': 40})
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
    plan = combat.plan_enemy_turn(state, enemy.display_name)
    with patch('app.dice.skill_check', return_value=check('fail', roll=90)):
        result = combat.resolve_enemy_action(state, plan['plan_id'])
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
    result = combat.managed_single_hit(state, pc, damage, event_id='hit', reason='Authorized incident')
    assert result['ok']
    effective = combat_resources.effective_character(state, pc)
    assert effective.hp == max(0, starting_hp - damage)
    for key, value in injury.items():
        assert effective.injury[key] == value
    assert pc.hp == 10
    assert combat.managed_single_hit(state, pc, damage, event_id='hit', reason='retry') == result
    if injury.get('dead'):
        assert not state.pending_checks
    elif injury.get('major_wound'):
        assert state.pending_checks['player']['skill'] == 'CON'


def test_injury_ownership_block_is_all_or_nothing():
    state, pc, _ = battle()
    state.pending_checks['player'] = {'type': 'skill', 'skill': 'Other'}
    before = state.to_dict()
    assert combat.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')['blocked_by'] == 'pending_check'
    assert state.to_dict() == before


def test_major_wound_failed_con_sets_effective_unconscious_only():
    state, pc, _ = battle()
    combat.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')
    result = finish(state, check('fail', roll=90))
    assert result['completed']
    assert combat_resources.effective_character(state, pc).injury['unconscious']
    assert not pc.injury


def test_dying_settlement_transfers_and_due_check_survives_restart():
    state, pc, _ = battle()
    combat_resources.set_resource(state, pc, 'hp', 4, event_id='setup')
    combat.managed_single_hit(state, pc, 6, event_id='hit', reason='Incident')
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


def test_legacy_admission_requires_explicit_pending_empty_closure():
    state, pc, _ = battle()
    state.combat.pipeline_version = ''
    state.combat.combat_id = ''
    state.combat.working_resources = state.combat.baseline_resources = {}
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat.start_combat(state)
    state.pending_checks['player'] = {'type': 'skill'}
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat.close_legacy_combat(state, event_id='closure', reason='Controller closes old history')
    state.pending_checks.clear()
    receipt = combat.close_legacy_combat(state, event_id='closure', reason='Controller closes old history')
    assert receipt['legacy_state']['active']
    assert pc.hp == 10
    combat.begin_combat(state)
    assert state.combat.pipeline_version == combat_resources.PIPELINE_VERSION


def test_explicit_severity_effect_receipt_retry_and_transfer():
    state, pc, _ = battle()
    result = combat_flow.declare_effect(state, effect_id='acid', target_id='pc:pc1', severity_id='minor',
                                       scope='round', reason='Authorized acid exposure', stop_condition='Washed off')
    assert result['ok']
    with patch('app.dice.random.randint', return_value=2) as rng:
        first = combat.process_timing(state, 'round_end')
        assert combat.process_timing(state, 'round_end') == []
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
    plan = combat.plan_enemy_turn(state, enemy.display_name)
    before = state.to_dict()
    assert not combat.resolve_enemy_action(state, plan['plan_id'], {'hit': True, 'damage': 10})['ok']
    assert state.to_dict() == before
    assert pc.hp == 10
