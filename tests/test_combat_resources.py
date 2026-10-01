"""Provisional combat resources publish only through explicit settlement."""
import pytest

from app import combat_resources
from app.models import Character, Combatant, GroupState


def battle():
    character = Character('Ada', 'player', character_id='char:ada', hp=10, luck=50,
                          weapons={'Pistol': {'ammo': 6, 'ammo_max': 6}})
    state = GroupState('group', characters={'player': character}, characters_by_id={'char:ada': character})
    state.combat.active = True
    state.combat.order = [Combatant(name='Ada', character_id='char:ada', is_pc=True, hp=10, hp_max=10)]
    combat_resources.initialize_working_state(state, combat_id='battle:1', new_combat=True)
    return state, character


def test_working_damage_is_idempotent_and_survives_restart_without_publishing():
    state, character = battle()
    receipt = combat_resources.adjust_resource(state, character, 'hp', -3, event_id='hit:1', reason='attack')
    assert receipt['after'] == 7
    assert character.hp == 10
    assert combat_resources.effective_character(state, character).hp == 7
    restarted = GroupState.from_dict(state.to_dict())
    restored = restarted.characters_by_id['char:ada']
    assert combat_resources.adjust_resource(restarted, restored, 'hp', -3, event_id='hit:1', reason='attack') == receipt
    assert combat_resources.effective_character(restarted, restored).hp == 7
    assert len(restarted.combat.events) == 1


def test_settlement_publishes_absolute_values_once_and_retains_future_obligations():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -4, event_id='damage')
    combat_resources.adjust_resource(state, character, 'luck', -10, event_id='luck')
    combat_resources.adjust_ammo(state, character, 'Pistol', -1, event_id='shot')
    combat_resources.set_status_tag(state, character, 'major wound', True, event_id='tag')
    combat_resources.set_injury(state, character, {'major_wound': True, 'dying': True}, event_id='injury')
    obligation = {'obligation_id': 'dying:ada', 'combat_id': 'battle:1', 'character_id': 'char:ada',
                  'kind': 'dying', 'status': 'future', 'next_trigger': {'round': 2},
                  'stop_condition': 'stabilized', 'rule_source': {'rule': 'major wound'},
                  'processed_timings': ['round:1'], 'roll_receipts': {'con:1': {'roll': 20}}}
    preview = combat_resources.get_settlement(state, obligations=[obligation])
    receipt = combat_resources.commit_settlement(state, preview['settlement_id'])
    assert (character.hp, character.luck, character.weapons['Pistol']['ammo']) == (6, 40, 5)
    assert character.injury['dying']
    assert state.characters['player'] is state.characters_by_id['char:ada']
    assert state.postcombat_obligations == [obligation]
    assert not state.combat.active
    restarted = GroupState.from_dict(state.to_dict())
    assert combat_resources.commit_settlement(restarted, preview['settlement_id']) == receipt
    assert len(restarted.postcombat_obligations) == 1
    assert restarted.closed_combat_receipts['battle:1']['events']


def test_external_resource_changes_block_atomic_settlement_but_group_checkpoints_do_not():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -2, event_id='hit')
    preview = combat_resources.get_settlement(state)
    state.state_revision += 50
    character.luck = 49
    with pytest.raises(combat_resources.SettlementConflict):
        combat_resources.commit_settlement(state, preview['settlement_id'])
    assert character.hp == 10
    assert state.combat.active
    character.luck = 50
    assert combat_resources.commit_settlement(state, preview['settlement_id'])['status'] == 'committed'


def test_pending_or_stale_preview_does_not_publish_and_rollback_preserves_audit():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -2, event_id='hit')
    preview = combat_resources.get_settlement(state)
    state.pending_checks['player'] = {'type': 'con'}
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.commit_settlement(state, preview['settlement_id'])
    state.pending_checks.clear()
    combat_resources.adjust_resource(state, character, 'mp', -1, event_id='magic')
    with pytest.raises(combat_resources.SettlementConflict):
        combat_resources.commit_settlement(state, preview['settlement_id'])
    receipt = combat_resources.rollback_combat(state, event_id='cancel', reason='explicit Keeper ruling')
    assert (character.hp, character.mp) == (10, 10)
    assert receipt['status'] == 'rolled_back'
    assert len(receipt['events']) == 3
    assert not state.postcombat_obligations
    assert not state.combat.active
    assert combat_resources.rollback_combat(state, event_id='cancel', reason='explicit Keeper ruling') == receipt


def test_roll_receipt_is_reused_on_restart_without_new_random_draw():
    state, _ = battle()
    first = combat_resources.record_roll(state, 'action:1:attack', lambda: {'roll': 42, 'success': True})
    restarted = GroupState.from_dict(state.to_dict())
    def forbidden():
        raise AssertionError('A recovered receipt must not reroll')
    assert combat_resources.record_roll(restarted, 'action:1:attack', forbidden) == first


def test_appended_correction_replays_deltas_and_retains_original_rolls():
    state, character = battle()
    combat_resources.record_roll(state, 'attack:1', {'roll': 20})
    combat_resources.adjust_resource(state, character, 'hp', -3, event_id='hit:1')
    combat_resources.adjust_resource(state, character, 'hp', -2, event_id='hit:2')
    correction = combat_resources.correct_event(state, 'hit:1', event_id='correct:1', changes={'amount': -5}, reason='armor correction')
    assert combat_resources.effective_character(state, character).hp == 3
    assert character.hp == 10
    assert state.combat.events[0]['data']['after'] == 7
    assert state.combat.events[-1]['kind'] == 'correction'
    assert state.combat.roll_receipts['attack:1'] == {'roll': 20}
    assert combat_resources.correct_event(state, 'hit:1', event_id='correct:1', changes={'amount': -5}, reason='armor correction') == correction


def test_all_resources_edits_ammo_and_status_remain_working_only_and_are_clamped():
    state, character = battle()
    edited = combat_resources.effective_character(state, character)
    edited.san = 40
    edited.mp = 3
    edited.luck = 100
    edited.weapons['Pistol']['ammo'] = 4
    edited.status_tags = ['unconscious']
    edited.injury = {'unconscious': True}
    receipt = combat_resources.reconcile_effective_character(state, edited, event_id='edit:1', reason='battle effects')
    effective = combat_resources.effective_character(state, character)
    assert (effective.san, effective.mp, effective.luck, effective.weapons['Pistol']['ammo']) == (40, 3, 99, 4)
    assert effective.status_tags == ['unconscious']
    assert (character.san, character.mp, character.luck, character.weapons['Pistol']['ammo']) == (50, 10, 50, 6)
    assert combat_resources.reconcile_effective_character(state, edited, event_id='edit:1') == receipt
    combat_resources.adjust_ammo(state, character, 'Pistol', event_id='reload', reload_full=True)
    combat_resources.adjust_resource(state, character, 'hp', -100, event_id='big hit')
    assert combat_resources.effective_character(state, character).weapons['Pistol']['ammo'] == 6
    assert combat_resources.effective_character(state, character).hp == 0


def test_legacy_active_battle_fails_closed_and_retains_pending_and_history():
    state = GroupState('group')
    state.combat.active = True
    state.combat.round_number = 4
    state.pending_checks['player'] = {'roll_id': 'old-roll'}
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.initialize_working_state(state)
    assert state.combat.round_number == 4
    assert state.pending_checks['player']['roll_id'] == 'old-roll'
    assert not state.combat.baseline_resources


def test_npc_only_battle_can_be_managed_and_late_pc_admission_keeps_history():
    state = GroupState('group')
    state.combat.order = [Combatant(name='Cultist', hp=6, hp_max=6)]
    combat_resources.initialize_working_state(state, combat_id='npc-battle', new_combat=True)
    combat_resources.record_roll(state, 'npc-hit', {'roll': 33})
    character = Character('Ada', 'player', character_id='char:ada')
    state.characters['player'] = character
    state.characters_by_id['char:ada'] = character
    combat_resources.admit_character(state, character)
    state.combat.order.append(Combatant(name='Ada', is_pc=True, character_id='char:ada'))
    assert state.combat.combat_id == 'npc-battle'
    assert state.combat.roll_receipts['npc-hit']['roll'] == 33
    assert combat_resources.effective_character(state, character).hp == 10


def test_future_obligations_are_preview_bound_and_due_work_cannot_settle():
    state, character = battle()
    combat_resources.set_injury(state, character, {'major_wound': True, 'dying': True}, event_id='injury')
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.get_settlement(state)
    obligation = {'obligation_id': 'dying:ada', 'combat_id': 'battle:1', 'character_id': 'char:ada',
                  'kind': 'dying', 'status': 'future', 'next_trigger': {'round': 2},
                  'stop_condition': 'stabilized', 'rule_source': {'rule': 'major wound'}}
    preview = combat_resources.get_settlement(state, obligations=[obligation])
    modified = {**obligation, 'next_trigger': {'round': 3}}
    with pytest.raises(combat_resources.SettlementConflict):
        combat_resources.commit_settlement(state, preview['settlement_id'], obligations=[modified])
    state.pending_luck_decisions['player'] = {'check_id': 'last check'}
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.commit_settlement(state, preview['settlement_id'])
    assert character.injury == {}


def test_corrected_prior_action_requires_reconciliation_of_later_choices():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -2, event_id='hit')
    combat_resources.record_event(state, 'later-choice', 'action', data={'target': 'char:ada'})
    combat_resources.correct_event(state, 'hit', event_id='correction', changes={'amount': -10}, reason='correct damage')
    assert state.combat.phase == 'NEEDS_RULING'
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.get_settlement(state)
    assert len(state.combat.events) == 3


def test_future_tracking_survives_another_battle_without_reset():
    from app.models import CombatState
    state, _ = battle()
    obligation = {'obligation_id': 'effect:old', 'combat_id': 'battle:1', 'character_id': 'char:ada',
                  'kind': 'effect', 'status': 'future', 'next_trigger': {'round': 5},
                  'stop_condition': 'cured', 'rule_source': {'rule': 'poison'},
                  'processed_timings': ['tick:1'], 'roll_receipts': {'tick:1': {'roll': 2}}}
    preview = combat_resources.get_settlement(state, obligations=[obligation])
    combat_resources.commit_settlement(state, preview['settlement_id'])
    state.combat = CombatState(order=[Combatant(name='Ada', is_pc=True, character_id='char:ada')])
    combat_resources.initialize_working_state(state, combat_id='battle:2', new_combat=True)
    restarted = GroupState.from_dict(state.to_dict())
    assert restarted.postcombat_obligations == [obligation]
    assert len(restarted.closed_combat_receipts) == 1
    assert restarted.combat.combat_id == 'battle:2'
    restarted.postcombat_obligations[0]['status'] = 'due'
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.get_settlement(restarted)


def test_another_combat_cannot_reinitialize_unclosed_reservations():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -2, event_id='hit')
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.initialize_working_state(state, combat_id='battle:other', new_combat=True)
    assert state.combat.combat_id == 'battle:1'
    assert combat_resources.effective_character(state, character).hp == 8


def test_future_effect_transfer_is_required_and_does_not_reset_existing_receipt():
    from app.models import EffectState
    state, _ = battle()
    state.combat.effects.append(EffectState(id='fire', target_id='char:ada', damage='1d6', remaining_rounds=2))
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.get_settlement(state)
    obligation = {'obligation_id': 'fire:ada', 'combat_id': 'battle:1', 'character_id': 'char:ada',
                  'kind': 'effect', 'status': 'future', 'next_trigger': {'mechanical_round': 2},
                  'stop_condition': 'extinguished', 'rule_source': {'rule': 'fire'},
                  'effect': {'id': 'fire'}, 'processed_timings': ['fire:round:1']}
    state.postcombat_obligations.append(obligation)
    altered = {**obligation, 'processed_timings': []}
    with pytest.raises(combat_resources.SettlementConflict):
        combat_resources.get_settlement(state, obligations=[altered])
    preview = combat_resources.get_settlement(state)
    combat_resources.commit_settlement(state, preview['settlement_id'])
    assert state.postcombat_obligations[0]['processed_timings'] == ['fire:round:1']


def test_non_durable_roll_and_unsupported_ammo_do_not_change_state():
    state, character = battle()
    with pytest.raises(TypeError):
        combat_resources.record_roll(state, 'bad-roll', {'value': object()})
    with pytest.raises(ValueError):
        combat_resources.adjust_ammo(state, character, 'Knife', -1, event_id='wrong-weapon')
    assert not state.combat.events
    assert not state.combat.roll_receipts


def test_duplicate_settlement_with_old_id_does_not_publish_a_new_battle():
    from app.models import CombatState
    state, character = battle()
    old_preview = combat_resources.get_settlement(state)
    old_receipt = combat_resources.commit_settlement(state, old_preview['settlement_id'])
    state.combat = CombatState(order=[Combatant(name='Ada', is_pc=True, character_id='char:ada')])
    combat_resources.initialize_working_state(state, combat_id='battle:2', new_combat=True)
    combat_resources.adjust_resource(state, character, 'hp', -4, event_id='new-hit')
    assert combat_resources.commit_settlement(state, old_preview['settlement_id']) == old_receipt
    assert state.combat.active
    assert character.hp == 10
    assert combat_resources.effective_character(state, character).hp == 6


def test_explicit_conflict_reconciliation_creates_fresh_preview_and_audit():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -3, event_id='hit')
    old = combat_resources.get_settlement(state)
    character.hp = 9
    with pytest.raises(combat_resources.SettlementConflict):
        combat_resources.commit_settlement(state, old['settlement_id'])
    combat_resources.reconcile_baseline(state, character, event_id='reconcile',
                                       decision='keep_working', reason='Keeper confirms battle HP over external edit')
    fresh = combat_resources.get_settlement(state)
    assert fresh['settlement_id'] != old['settlement_id']
    assert fresh['baseline']['char:ada']['hp'] == 9
    assert fresh['final']['char:ada']['hp'] == 7
    combat_resources.commit_settlement(state, fresh['settlement_id'])
    assert character.hp == 7
    assert state.closed_combat_receipts['battle:1']['events'][-1]['data']['decision'] == 'keep_working'


def test_reconciliation_replay_does_not_apply_prior_deltas_twice():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -3, event_id='old-hit')
    character.hp = 9
    combat_resources.reconcile_baseline(state, character, event_id='reconcile', decision='adopt_persistent', reason='accepted external correction')
    combat_resources.adjust_resource(state, character, 'hp', -2, event_id='new-hit')
    combat_resources.correct_event(state, 'new-hit', event_id='fix-new', changes={'amount': -4}, reason='updated damage')
    assert combat_resources.effective_character(state, character).hp == 5
    assert state.combat.events[1]['data']['baseline_before']['hp'] == 10
    combat_resources.correct_event(state, 'old-hit', event_id='fix-old', changes={'amount': -5}, reason='historical correction')
    assert state.combat.phase == 'NEEDS_RULING'
    assert state.combat.events[0]['data']['amount'] == -3


def test_group_mechanical_clock_survives_state_serialization_and_new_battle():
    state, _ = battle()
    state.mechanical_round = 17
    restarted = GroupState.from_dict(state.to_dict())
    assert restarted.mechanical_round == 17
    assert restarted.combat.combat_id == 'battle:1'


@pytest.mark.parametrize(('original_damage', 'corrected_damage', 'injury'), [
    (10, 2, {'dead': True}),
    (2, 10, {}),
])
def test_corrected_hp_cannot_publish_stale_injury_in_either_direction(original_damage, corrected_damage, injury):
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -original_damage, event_id='hit')
    combat_resources.set_injury(state, character, injury, event_id='injury-result')
    combat_resources.set_status_tag(state, character, 'dead', bool(injury.get('dead')), event_id='status-result')
    combat_resources.correct_event(state, 'hit', event_id='correction', changes={'amount': -corrected_damage},
                                   reason='source-backed corrected damage')
    assert combat_resources.effective_character(state, character).hp == 10 - corrected_damage
    assert state.combat.phase == 'NEEDS_RULING'
    assert state.combat.interaction['requires_injury_reconciliation']
    assert state.combat.interaction['character_id'] == 'char:ada'
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.get_settlement(state)
    assert state.combat.events[0]['data']['amount'] == -original_damage
    assert character.hp == 10


def test_corrected_fatal_damage_without_prior_injury_event_still_requires_rule_reconciliation():
    state, character = battle()
    combat_resources.adjust_resource(state, character, 'hp', -2, event_id='hit')
    combat_resources.correct_event(state, 'hit', event_id='fatal-correction', changes={'amount': -10},
                                   reason='verified actual damage')
    assert state.combat.phase == 'NEEDS_RULING'
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_resources.get_settlement(state)


def test_rollback_invalidates_only_controls_owned_by_current_battle():
    state, _ = battle()
    state.pending_checks['player'] = {'check_id': 'old-dying', 'postcombat_context': {
        'obligation_id': 'old:dying', 'round': 7}}
    state.pending_luck_decisions['player'] = {'decision_id': 'battle-luck', 'combat_context': {'combat_id': 'battle:1'}}
    receipt = combat_resources.rollback_combat(state, event_id='rollback', reason='cancel current battle')
    assert state.pending_checks['player']['check_id'] == 'old-dying'
    assert 'player' not in state.pending_luck_decisions
    assert receipt['pending_luck_decisions']['player']['decision_id'] == 'battle-luck'
    assert 'pending_checks' not in receipt
    restored = GroupState.from_dict(state.to_dict())
    assert restored.pending_checks['player']['postcombat_context']['obligation_id'] == 'old:dying'


def test_rollback_preserves_unrelated_or_different_battle_controls_for_same_owner():
    state, _ = battle()
    state.pending_checks['player'] = {'check_id': 'unrelated-skill'}
    state.pending_luck_decisions['player'] = {'decision_id': 'old-luck', 'combat_context': {'combat_id': 'battle:old'}}
    combat_resources.rollback_combat(state, event_id='rollback', reason='cancel current battle')
    assert state.pending_checks['player']['check_id'] == 'unrelated-skill'
    assert state.pending_luck_decisions['player']['decision_id'] == 'old-luck'


def test_rollback_cannot_erase_a_prior_obligation_control_even_if_it_also_has_combat_context():
    state, _ = battle()
    state.pending_checks['player'] = {'check_id': 'old-dying', 'combat_context': {'combat_id': 'battle:1'},
                                      'postcombat_context': {'obligation_id': 'old:dying', 'round': 7}}
    combat_resources.rollback_combat(state, event_id='rollback', reason='cancel current battle')
    assert state.pending_checks['player']['check_id'] == 'old-dying'
