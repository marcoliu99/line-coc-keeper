"""Real tool/transport seams preserve provisional resources and owned controls."""
import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest

from app import combat_resources, dice, keeper, legacy_commands
from app.commands.handlers import character as character_handler
from app.commands.handlers import combat as combat_handler
from app.keeper_tools import registry
from app.models import Character, Combatant, GroupState
from app.services import canonical_facts, turn_delivery


@pytest.fixture
def store(monkeypatch):
    character = Character('Ada', 'player', character_id='char:ada', hp=10, hp_max=10,
                          san=50, luck=50, mp=10, skills={'格鬥（鬥毆）': 50, '閃避': 30},
                          weapons={'.45 Automatic': {'ammo': 7, 'ammo_max': 7}})
    state = GroupState('wiring', active=True, timeline_id='timeline:wiring',
                       characters={'player': character}, characters_by_id={'char:ada': character})
    state.combat.active = True
    state.combat.order = [Combatant(name='Ada', character_id=character.character_id, is_pc=True, hp=10, hp_max=10)]
    combat_resources.initialize_working_state(state, combat_id='combat:wiring', new_combat=True)
    saved = {'state': GroupState.from_dict(state.to_dict()), 'writes': 0}
    def load(group_id):
        return GroupState.from_dict(saved['state'].to_dict())
    def save(current, **kwargs):
        saved['state'] = GroupState.from_dict(current.to_dict())
        saved['writes'] += 1
    for module in (keeper, legacy_commands, character_handler, combat_handler):
        monkeypatch.setattr(module, 'load_state', load)
        monkeypatch.setattr(module, 'save_state', save)
    monkeypatch.setattr(keeper, '_save_state_checked', save)
    return saved


def tool(store, name, args=None, actor='player'):
    return keeper._execute_tool(store['state'], name, args or {}, [], [], actor_id=actor)


def effective(store):
    state = store['state']
    return combat_resources.effective_character(state, state.characters_by_id['char:ada'])


@pytest.mark.parametrize(('field', 'delta', 'before'), [('mp', -2, 10), ('san', -3, 50), ('luck', -5, 50)])
def test_resource_tool_stable_events_apply_twice_for_distinct_operations_and_once_on_retry(store, field, delta, before):
    args = {'investigator': 'Ada', 'field': field, 'delta': delta, 'event_id': 'change:1', 'reason': 'verified consequence'}
    first = tool(store, 'adjust_character', args)
    retry = tool(store, 'adjust_character', args)
    assert first['ok'] and first['provisional']
    assert retry['value'] == first['value']
    assert getattr(effective(store), field) == before + delta
    assert getattr(store['state'].characters_by_id['char:ada'], field) == before
    second = tool(store, 'adjust_character', {**args, 'event_id': 'change:2'})
    assert second['value'] == before + 2 * delta
    assert len(store['state'].combat.events) == 2


def test_managed_repeatable_mutation_requires_explicit_identity(store):
    result = tool(store, 'adjust_character', {'investigator': 'Ada', 'field': 'mp', 'delta': -1})
    assert not result['ok']
    assert effective(store).mp == 10
    assert store['writes'] == 0


def test_ammo_reload_and_status_queries_are_effective_without_publishing(store):
    args = {'investigator': 'Ada', 'weapon': '.45 Automatic', 'delta': -2, 'event_id': 'ammo:1'}
    assert tool(store, 'adjust_ammo', args)['ammo'] == 5
    assert tool(store, 'adjust_ammo', args)['ammo'] == 5
    assert effective(store).weapons['.45 Automatic']['ammo'] == 5
    assert store['state'].characters['player'].weapons['.45 Automatic']['ammo'] == 7
    assert tool(store, 'adjust_ammo', {**args, 'reload_full': True, 'event_id': 'reload:1'})['ammo'] == 7
    added = tool(store, 'add_status_tag', {'investigator': 'Ada', 'tag': 'dazed', 'event_id': 'status:1'})
    assert added['status_tags'] == ['dazed'] and added['provisional']
    sheet = tool(store, 'get_character_sheet', {'investigator': 'Ada'})
    assert sheet['provisional'] and sheet['sheet']['status_tags'] == ['dazed']
    assert store['state'].characters['player'].status_tags == []
    removed = tool(store, 'remove_status_tag', {'investigator': 'Ada', 'tag': 'dazed', 'event_id': 'status:2'})
    assert removed['status_tags'] == []


def test_sanity_autoroll_uses_working_san_and_retains_baseline(store):
    store['state'].autoroll_checks = True
    combat_resources.adjust_resource(store['state'], store['state'].characters['player'], 'san', -5, event_id='san:start')
    with patch.object(dice, 'skill_check', return_value=dice.SkillCheckResult(
        skill_value=45, roll=1, bonus_dice=0, penalty_dice=0, tier='critical', success=True, required_tier='regular')):
        outcome = tool(store, 'sanity_check', {'investigator': 'Ada', 'loss_success': '1', 'loss_failure': '2'})
    assert outcome['ok'], outcome
    assert outcome['current_san'] == 45 and outcome['san_after'] == 44
    assert effective(store).san == 44 and store['state'].characters['player'].san == 50
    event = store['state'].resolved_check_events[-1]
    assert event['provisional'] and event['state_effects'] == []
    assert event['provisional_state_effects'][0]['delta'] == -1


def test_manual_sanity_and_noncombat_luck_during_battle_reconcile_working_only(store):
    outcome = tool(store, 'sanity_check', {'investigator': 'Ada', 'loss_success': '1', 'loss_failure': '2'})
    assert outcome['pending']
    with patch.object(dice, 'skill_check', return_value=dice.SkillCheckResult(
        skill_value=50, roll=1, bonus_dice=0, penalty_dice=0, tier='critical', success=True, required_tier='regular')):
        resolved = legacy_commands._resolve_check_deterministically('wiring', 'player', '/coc check')
    assert resolved.should_finalize
    assert effective(store).san == 49 and store['state'].characters['player'].san == 50
    state = store['state']
    state.pending_luck_decisions['player'] = {
        'decision_id': 'ordinary:luck', 'check_id': 'ordinary:check', 'timeline_id': state.timeline_id,
        'skill_name': '閃避', 'display_label': None, 'value': 30, 'roll': 35,
        'bonus_dice': 0, 'penalty_dice': 0, 'original_tier': 'fail', 'difficulty': 'regular',
        'options': [{'tier': 'regular', 'cost': 5}],
    }
    result = legacy_commands._resolve_luck_decision_deterministically('wiring', 'player', 'regular')
    assert result.should_finalize
    assert effective(store).luck == 45 and store['state'].characters['player'].luck == 50


@pytest.mark.parametrize('name,args', [
    ('apply_combat_damage', {'target': 'Ada', 'raw_damage': 3}),
    ('apply_final_combat_damage', {'target': 'Ada', 'final_damage': 3}),
    ('damage_combatant', {'name': 'Ada', 'delta': -3}),
    ('resolve_enemy_action', {'action_id': 'injected', 'hit': True, 'damage': 3}),
])
def test_legacy_raw_outcomes_cannot_authorize_managed_damage(store, name, args):
    before = deepcopy(store['state'].to_dict())
    result = tool(store, name, args)
    assert not result['ok']
    assert store['state'].to_dict() == before
    assert store['writes'] == 0


def test_source_severity_tool_does_not_guess_environment(store):
    assert tool(store, 'get_damage_severity', {'severity_id': 'deadly'})['definition']['damage'] == '2d10'
    assert not tool(store, 'get_damage_severity', {'severity_id': 'fire'})['ok']


def test_controller_tools_available_without_human_kp_registration():
    for name in ('preview_combat_settlement', 'confirm_combat_settlement', 'rollback_combat',
                 'correct_combat_event', 'reconcile_combat_baseline', 'change_combat_initiative',
                 'declare_combat_effect', 'process_postcombat_obligations'):
        assert name in {schema['name'] for schema in keeper._tools_for_speaker_role('player')}
        assert not registry.REGISTRY[name].kp_assistant


def test_player_cannot_directly_confirm_rollback_or_clear_battle(store):
    async def run():
        replies = []
        async def reply(message):
            replies.append(message)
        for action in ('confirm', 'rollback', 'damage', 'end', 'next'):
            await combat_handler.handle_combat_command('wiring', reply, ['/coc', 'combat', action], user_id='player')
            assert store['state'].combat.active
        assert store['writes'] == 0 and len(replies) == 5
    asyncio.run(run())


def test_sheet_is_provisional_and_character_replacement_is_guarded(store):
    async def run():
        replies = []
        async def reply(message):
            replies.append(message)
        async def dm(*args):
            pass
        combat_resources.adjust_resource(store['state'], store['state'].characters['player'], 'hp', -2, event_id='hit')
        await character_handler.handle_character_command('wiring', 'player', reply, dm, ['/coc', 'sheet'])
        assert '暫定' in replies[-1] and '8/10' in replies[-1]
        assert not await character_handler.handle_character_command('wiring', 'player', reply, dm, ['/coc', 'retire'])
        assert store['state'].characters['player'].hp == 10
    asyncio.run(run())


def test_provisional_delivery_and_canonical_projection_remain_distinct(store):
    observation = turn_delivery.observe_tool('adjust_character', {'ok': True, 'investigator': 'Ada',
        'field': 'hp', 'value': 8, 'provisional': True}, 1)
    assert '暫定' in observation.public_text
    event = {'event_id': 'check:1', 'timeline_id': store['state'].timeline_id,
             'provisional': True, 'combat_id': 'combat:wiring', 'outcome': '成功'}
    store['state'].resolved_check_events.append(event)
    assert canonical_facts.requirements(store['state']).committed_events == ()
    store['state'].closed_combat_receipts['combat:wiring'] = {'status': 'rolled_back'}
    assert not canonical_facts.committed_check_event(store['state'], event)
    store['state'].closed_combat_receipts['combat:wiring']['status'] = 'committed'
    assert canonical_facts.committed_check_event(store['state'], event)


def add_reviewed_enemy(store, *, npc_first=False):
    from app import combat
    state = store['state']
    state.characters['player'].skills['firearms-handgun'] = 50
    state.characters_by_id['char:ada'].skills['firearms-handgun'] = 50
    card = combat.create_enemy_card(state, 'Cultist', dex=80 if npc_first else 20, hp=20,
        attacks=[{'id': 'claw', 'skill_value': 50, 'damage': '1d3', 'range_band': 'engaged'}],
        skills={'dodge': 20}, source={'url': 'https://example.test/scenario', 'revision': 'reviewed-v1',
                                     'sha256': 'abc', 'attack_mode': 'melee', 'extreme_rule': 'maximum'})
    combat.add_enemy_card_to_combat(state, card.id)
    state.combat.current_index = 0
    return next(p for p in state.combat.order if p.side == 'enemy')


def test_managed_manual_roll_luck_retains_context_and_never_uses_legacy_ranged_rng(store):
    from app import combat_flow
    enemy = add_reviewed_enemy(store)
    actor = next(p for p in store['state'].combat.order if p.is_pc)
    declare = {'action_id': 'shot:1', 'actor_id': actor.combatant_id, 'target_id': enemy.combatant_id,
               'weapon_reference': '.45 Automatic', 'action_kind': 'single_shot', 'distance_yards': 5}
    failed = dice.SkillCheckResult(50, 55, 0, 0, 'fail', False, 'regular')
    with patch.object(dice, 'skill_check', return_value=failed) as rolls:
        declared = tool(store, 'declare_combat_action', declare)
        assert declared['ok'], declared
        pending_before = deepcopy(store['state'].pending_checks['player'])
        assert pending_before['combat_context']['check_role'] == 'attack'
        result = legacy_commands._resolve_check_deterministically('wiring', 'player', '/coc check')
        assert result.decision_id
        decision = store['state'].pending_luck_decisions['player']
        assert decision['combat_context'] == pending_before['combat_context']
        assert decision['check_id'] == pending_before['check_id']
        assert store['state'].combat.phase == 'LUCK_DECISION'
        assert len(store['state'].combat.roll_receipts) == 2  # NPC dive and investigator attack.
        with patch.object(legacy_commands, '_resolve_ranged_defense_outcome', side_effect=AssertionError('extra RNG')):
            with patch.object(dice.random, 'randint', return_value=2):
                finalized = legacy_commands._resolve_luck_decision_deterministically('wiring', 'player', 'regular')
        assert finalized.should_finalize
        assert rolls.call_count == 2
    assert effective(store).luck == 45
    assert effective(store).weapons['.45 Automatic']['ammo'] == 6
    assert store['state'].characters['player'].luck == 50
    assert store['state'].characters['player'].weapons['.45 Automatic']['ammo'] == 7
    assert store['state'].combat.actions['shot:1']['completed']
    # Retrying the durable action never rolls or consumes more ammunition.
    with patch.object(dice, 'skill_check', side_effect=AssertionError('reroll')):
        assert tool(store, 'run_combat_action', {'action_id': 'shot:1'})['completed']
    assert effective(store).weapons['.45 Automatic']['ammo'] == 6
    assert combat_flow.validate_pending_context(store['state'], pending_before, 'player')['ok'] is False


def test_managed_autoroll_has_same_luck_bridge_and_stale_control_consumes_nothing(store):
    enemy = add_reviewed_enemy(store)
    store['state'].autoroll_checks = True
    actor = next(p for p in store['state'].combat.order if p.is_pc)
    failed = dice.SkillCheckResult(50, 55, 0, 0, 'fail', False, 'regular')
    with patch.object(dice, 'skill_check', return_value=failed):
        result = tool(store, 'declare_combat_action', {'action_id': 'attack:auto', 'actor_id': actor.combatant_id,
            'target_id': enemy.combatant_id, 'weapon_reference': 'unarmed'})
    assert result['ok'] and result['phase'] == 'LUCK_DECISION'
    decision = store['state'].pending_luck_decisions['player']
    assert decision['combat_context']['check_role'] == 'attack'
    decision['combat_context']['interaction_id'] = 'stale'
    before = deepcopy(store['state'].to_dict())
    with patch.object(dice, 'skill_check', side_effect=AssertionError('stale roll')):
        rejected = legacy_commands._resolve_luck_decision_deterministically('wiring', 'player', 'regular')
    assert not rejected.should_finalize
    assert store['state'].to_dict() == before


def test_bot_settlement_has_no_human_kp_gate_and_old_confirmation_retry_is_safe_in_next_battle(store):
    from app import combat
    assert not store['state'].kp_assistant_user_id
    assert tool(store, 'adjust_character', {'investigator': 'Ada', 'field': 'mp', 'delta': -2, 'event_id': 'magic'})['ok']
    preview = tool(store, 'preview_combat_settlement')
    assert preview['ok'], preview
    args = {'combat_id': 'combat:wiring', 'settlement_id': preview['preview']['settlement_id'], 'reason': 'Keeper reviewed final resources'}
    confirmation = tool(store, 'confirm_combat_settlement', args)
    assert confirmation['ok'] and store['state'].characters['player'].mp == 8
    assert not store['state'].combat.active
    combat.begin_combat(store['state'])
    before = deepcopy(store['state'].to_dict())
    retry = tool(store, 'confirm_combat_settlement', args)
    assert retry['ok'] and retry['receipt']['settlement_id'] == args['settlement_id']
    assert store['state'].to_dict() == before


def test_rollback_retry_after_new_battle_returns_source_receipt_without_touching_it(store):
    from app import combat
    args = {'combat_id': 'combat:wiring', 'event_id': 'rollback:1', 'reason': 'Explicit cancellation'}
    assert tool(store, 'rollback_combat', args)['ok']
    combat.begin_combat(store['state'])
    before = deepcopy(store['state'].to_dict())
    assert tool(store, 'rollback_combat', args)['receipt']['combat_id'] == 'combat:wiring'
    assert store['state'].to_dict() == before


def test_managed_hp_adjustment_uses_owned_injury_hook_and_status_query_is_provisional(store):
    result = tool(store, 'adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -6,
                                            'event_id': 'incident:1', 'reason': 'Verified scenario incident'})
    assert result['ok'] and result['major_wound']
    assert store['state'].characters['player'].hp == 10
    assert effective(store).hp == 4 and effective(store).injury['major_wound']
    assert store['state'].pending_checks['player']['allow_luck'] is False
    assert store['state'].pending_checks['player']['combat_context']['check_role'] == 'injury'
    status = tool(store, 'get_combat_status')
    assert status['provisional'] and '暫定' in status['status']
    with patch.object(dice, 'skill_check', return_value=dice.SkillCheckResult(50, 90, 0, 0, 'fail', False)):
        resolved = legacy_commands._resolve_check_deterministically('wiring', 'player', '/coc check CON')
    assert resolved.should_finalize
    assert effective(store).injury['unconscious']
    assert store['state'].characters['player'].injury == {}
