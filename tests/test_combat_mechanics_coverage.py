# ruff: noqa: F811  (the combat harness fixture is imported, then used by name)
"""The combat and mechanics paths the Camp Sunny soak never exercised, driven through the real tools and buttons.

Each test follows one mechanic from the player's action to its settled state and checks: the right owner is asked,
the right resource changes by the right amount, the pending control is consumed, the tools ran in the right order,
a replayed button changes nothing, and what the player is told matches the state. Dice are scripted; storage, the
combat engine, the check engine and the button handlers are the real ones.
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from app import config, dice
from app.models import Combatant
from app.services import turn_delivery
from tests.test_combat_state_machine_integration import (  # noqa: F401  (battle is a fixture)
    Battle,
    battle,
    result,
)


@pytest.fixture(autouse=True)
def explicit_advance():
    """These scenarios drive initiative by hand; the engine's own advance after a settled action is covered in
    tests/test_combat_turn_friction.py."""
    with patch.object(config, "COMBAT_AUTO_ADVANCE", False):
        yield


RAW_TIER = re.compile(r"(?<![A-Za-z])(?:fumble|fail|regular|hard|extreme|critical)(?![A-Za-z])")


def enemy_of(state) -> Combatant:
    return next(c for c in state.combat.order if c.side == 'enemy')


def resolve_player_roll(battle: Battle, outcomes: list, *, damage_roll: int = 2) -> None:
    """Press the owner's pending check button, skip any Luck offer, then replay both buttons.

    The replay is part of the helper so that every scenario that uses it proves a stale or foreign press changes
    nothing: no new dice, no new damage, no change to the stored state.
    """
    pending = battle.load().pending_checks['player']
    with patch.object(dice, 'skill_check', side_effect=outcomes) as rng, \
            patch('app.dice.random.randint', return_value=damage_roll) as damage_dice:
        battle.check(pending['check_id'])
        decision = battle.load().pending_luck_decisions.get('player')
        if decision:
            battle.luck(decision['decision_id'])
        settled, draws, rolled = battle.load().to_dict(), rng.call_count, damage_dice.call_count
        battle.check(pending['check_id'])
        battle.check(pending['check_id'], clicker='intruder')
        if decision:
            battle.luck(decision['decision_id'])
        assert (rng.call_count, damage_dice.call_count) == (draws, rolled)
        assert battle.load().to_dict() == settled


def npc_attacks(battle: Battle, attack_tier: str = 'regular') -> str:
    """The first enemy attacks Ada; returns the interaction id of her defence choice."""
    with patch.object(dice, 'skill_check', return_value=result(roll=20, tier=attack_tier, value=50)):
        started = battle.start(npc_first=True)['opening_enemy_turn']
    assert started['phase'] == 'PLAYER_CHOICE'
    pending = battle.load().pending_checks['player']
    assert pending['combat_context']['check_role'] == 'defense_choice'
    return battle.load().combat.interaction['interaction_id']


# --- 1 player melee attack, 5 weapon damage -----------------------------------------------------------

def test_a_player_melee_hit_damages_the_enemy_once_and_a_replayed_button_changes_nothing(battle):
    battle.start()
    battle.declare('hit:1')
    pending = battle.load().pending_checks['player']
    assert pending['combat_context']['check_role'] == 'attack' and pending['combat_context']['action_id'] == 'hit:1'
    with patch.object(dice, 'skill_check', side_effect=[result(roll=10, tier='hard'), result(roll=90, tier='fail', value=40)]) as rng, \
            patch('app.dice.random.randint', return_value=2) as damage_dice:
        battle.check(pending['check_id'])
        after = battle.load()
        assert after.combat.actions['hit:1']['completed'] and 'player' not in after.pending_checks
        assert enemy_of(after).hp == 18 and battle.effective().hp == 10
        receipt = after.combat.actions['hit:1']['result']['damage_receipt']
        assert receipt['weapon_roll']['rolls'] == [2] and receipt['total'] == 2
        draws, dice_calls, snapshot = rng.call_count, damage_dice.call_count, after.to_dict()
        battle.check(pending['check_id'])
        battle.check(pending['check_id'], clicker='intruder')
        assert (rng.call_count, damage_dice.call_count) == (draws, dice_calls) and battle.load().to_dict() == snapshot
    assert 'hit' in after.combat.actions['hit:1']['result'] and after.combat.actions['hit:1']['result']['hit'] is True


def test_a_player_miss_changes_no_hit_points(battle):
    battle.start()
    battle.declare('miss:1')
    resolve_player_roll(battle, [result(roll=95, tier='fail', value=60), result(roll=10, tier='hard', value=40)])
    after = battle.load()
    assert after.combat.actions['miss:1']['completed'] and enemy_of(after).hp == 20 and battle.effective().hp == 10


# --- 2 defender dodge, 4 NPC attack -------------------------------------------------------------------

def test_a_failed_dodge_costs_the_defender_the_damage_once_and_only_to_her(battle):
    wait_id = npc_attacks(battle)
    chosen = battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'dodge'})
    assert chosen['phase'] == 'PLAYER_ROLL'
    pending = battle.load().pending_checks['player']
    assert pending['combat_context']['action_id'].startswith('npc:')
    with patch.object(dice, 'skill_check', return_value=result(roll=90, tier='fail', value=40)) as rng, \
            patch('app.dice.random.randint', return_value=2) as damage_dice:
        battle.check(pending['check_id'])
        offered = battle.load()
        assert offered.combat.phase == 'LUCK_DECISION' and battle.effective().hp == 10  # nothing lands before Luck is decided
        battle.luck(offered.pending_luck_decisions['player']['decision_id'])
        after = battle.load()
        assert battle.effective().hp == 8 and after.characters_by_id['char:ada'].hp == 10  # working copy only until settled
        assert enemy_of(after).hp == 20 and 'player' not in after.pending_checks and not after.pending_luck_decisions
        draws, rolled, snapshot = rng.call_count, damage_dice.call_count, after.to_dict()
        battle.check(pending['check_id'])
        assert (rng.call_count, damage_dice.call_count) == (draws, rolled) and battle.load().to_dict() == snapshot


def test_a_successful_dodge_costs_nothing(battle):
    wait_id = npc_attacks(battle)
    battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'dodge'})
    resolve_player_roll(battle, [result(roll=5, tier='hard', value=40)])
    assert battle.effective().hp == 10 and 'player' not in battle.load().pending_checks


def test_only_the_defender_may_choose_how_to_defend(battle):
    wait_id = npc_attacks(battle)
    before = battle.load().to_dict()
    with pytest.raises(ValueError, match='owned check investigator binding'):
        battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'dodge'}, actor='intruder')
    assert battle.load().to_dict() == before


# --- 3 fight back --------------------------------------------------------------------------------------

def test_a_winning_fight_back_hurts_the_attacker_and_spares_the_defender(battle):
    wait_id = npc_attacks(battle)
    chosen = battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'counter'})
    assert chosen['phase'] == 'PLAYER_ROLL'
    resolve_player_roll(battle, [result(roll=4, tier='extreme', value=60)], damage_roll=2)
    after = battle.load()
    assert enemy_of(after).hp == 18 and battle.effective().hp == 10 and 'player' not in after.pending_checks


def test_a_losing_fight_back_is_hit_for_the_normal_damage(battle):
    wait_id = npc_attacks(battle)
    battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'counter'})
    resolve_player_roll(battle, [result(roll=95, tier='fail', value=60)], damage_roll=3)
    after = battle.load()
    assert battle.effective().hp == 7 and enemy_of(after).hp == 20


# --- 6 impale, 7 ammunition ----------------------------------------------------------------------------

def test_an_extreme_shot_impales_spends_one_round_and_the_replay_spends_none(battle):
    battle.start()
    battle.declare('shot:1', weapon='.45 Automatic', action_kind='single_shot', distance_yards=5)
    pending = battle.load().pending_checks['player']
    with patch.object(dice, 'skill_check', side_effect=[result(roll=2, tier='extreme'), result(roll=90, tier='fail', value=40)]) as rng, \
            patch('app.dice.random.randint', return_value=2) as damage_dice:
        battle.check(pending['check_id'])
        state = battle.load()
        if state.pending_luck_decisions:
            battle.luck(state.pending_luck_decisions['player']['decision_id'])
        after = battle.load()
        receipt = after.combat.actions['shot:1']['result']['damage_receipt']
        assert receipt['impaling'] is True and receipt['max_weapon_damage'] == 12
        assert receipt['total'] == receipt['max_weapon_damage'] + receipt['reroll']['total'] == 16
        assert enemy_of(after).hp == 4 and battle.effective().weapons['.45 Automatic']['ammo'] == 6
        draws, rolled = rng.call_count, damage_dice.call_count
        battle.check(pending['check_id'])
        assert (rng.call_count, damage_dice.call_count) == (draws, rolled)
        assert battle.effective().weapons['.45 Automatic']['ammo'] == 6 and enemy_of(battle.load()).hp == 4


def test_an_ordinary_hit_does_not_impale(battle):
    battle.start()
    battle.declare('shot:2', weapon='.45 Automatic', action_kind='single_shot', distance_yards=5)
    resolve_player_roll(battle, [result(roll=20, tier='hard'), result(roll=90, tier='fail', value=40)])
    receipt = battle.load().combat.actions['shot:2']['result']['damage_receipt']
    assert not receipt.get('impaling') and receipt['total'] == 4  # 1d10+2 with every die scripted to 2


def test_a_shot_with_no_range_needs_a_ruling_before_any_ammo_is_spent(battle):
    battle.start()
    with patch.object(dice, 'skill_check') as rng:
        rejected = battle.declare('shot:3', weapon='.45 Automatic', action_kind='single_shot')
    assert not rejected['ok'] and rng.call_count == 0 and battle.effective().weapons['.45 Automatic']['ammo'] == 7


# --- 8 major wound ---------------------------------------------------------------------------------------

def test_a_blow_of_half_her_hit_points_registers_one_con_check_for_her_alone(battle):
    battle.start()
    hit = battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -6,
                                          'event_id': 'trap:1', 'reason': 'falling beam'})
    assert hit['ok'] and hit.get('major_wound') and battle.effective().hp == 4
    pending = battle.load().pending_checks['player']
    assert pending['skill'] == 'CON' and pending['timeline_id'] == battle.load().timeline_id
    repeat = battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -6,
                                             'event_id': 'trap:1', 'reason': 'falling beam'})
    assert repeat['ok'] and battle.effective().hp == 4 and battle.load().pending_checks['player']['check_id'] == pending['check_id']
    with patch.object(dice, 'skill_check', return_value=result(roll=95, tier='fail', value=50)):
        battle.check(pending['check_id'])
        state = battle.load()
        if state.pending_luck_decisions:
            battle.luck(state.pending_luck_decisions['player']['decision_id'])
    after = battle.load()
    assert 'player' not in after.pending_checks and battle.effective().injury['unconscious']
    assert battle.effective().hp == 4


def test_a_blow_below_the_threshold_registers_no_con_check(battle):
    battle.start()
    battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -4, 'event_id': 'scratch:1', 'reason': 'x'})
    assert battle.effective().hp == 6 and 'player' not in battle.load().pending_checks


# --- 9 combat termination ----------------------------------------------------------------------------------

def test_settling_commits_the_working_resources_once_and_closes_the_battle(battle):
    battle.start()
    battle.declare('shot:9', weapon='.45 Automatic', action_kind='single_shot', distance_yards=5)
    resolve_player_roll(battle, [result(roll=20, tier='hard'), result(roll=90, tier='fail', value=40)])
    battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -2, 'event_id': 'graze:1', 'reason': 'x'})
    before = battle.load()
    assert before.combat.active and before.characters_by_id['char:ada'].hp == 10
    preview = battle.tool('preview_combat_settlement')['preview']
    arguments = {'combat_id': before.combat.combat_id, 'settlement_id': preview['settlement_id'], 'reason': 'finished'}
    settled = battle.tool('confirm_combat_settlement', arguments)
    assert settled['ok']
    after = battle.load()
    ada = after.characters_by_id['char:ada']
    assert not after.combat.active and ada.hp == 8 and ada.weapons['.45 Automatic']['ammo'] == 6
    assert battle.tool('confirm_combat_settlement', arguments)['receipt'] == settled['receipt']
    assert battle.load().to_dict() == after.to_dict()
    public = turn_delivery.observe_tool('confirm_combat_settlement', settled, 1)
    assert public.audience == 'public' and public.success


def test_settling_a_battle_every_investigator_lost_ends_the_scenario(battle):
    # House rule: with every investigator at 0 HP there is no one to play on, so the settlement ends the scenario
    # instead of leaving the Keeper to start another fight with no player turn in it.
    battle.start()
    for step, delta in enumerate((-4, -4, -2)):  # each blow under the major-wound threshold
        battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': delta,
                                         'event_id': f'blow:{step}', 'reason': 'x'})
    before = battle.load()
    preview = battle.tool('preview_combat_settlement')['preview']
    settled = battle.tool('confirm_combat_settlement', {'combat_id': before.combat.combat_id,
                                                        'settlement_id': preview['settlement_id'], 'reason': 'all down'})
    assert settled['ok'] and '劇本到此結束' in settled['scenario_ended']
    retried = battle.tool('confirm_combat_settlement', {'combat_id': before.combat.combat_id,
                                                        'settlement_id': preview['settlement_id'], 'reason': 'all down'})
    assert retried['receipt'] == settled['receipt'] and retried['scenario_ended'] == settled['scenario_ended']
    after = battle.load()
    assert not after.active and after.characters_by_id['char:ada'].hp == 0


# --- narration matches the state -------------------------------------------------------------------------

def test_the_result_text_the_player_receives_matches_the_state_and_shows_no_raw_tier(battle):
    """The deterministic text (roll line, damage summary). The Keeper's prose is not exercised here: ``run_turn`` is
    stubbed by the shared combat harness."""
    battle.start()
    battle.declare('hit:7')
    resolve_player_roll(battle, [result(roll=10, tier='hard'), result(roll=90, tier='fail', value=40)])
    told = "\n".join([*battle.notifications, *battle.messages])
    assert '困難成功' in told and not RAW_TIER.search(told)
    after = battle.load()
    damage = after.combat.actions['hit:7']['result']['damage']
    assert damage['hp_after'] == enemy_of(after).hp == 18
    assert str(damage['final_damage']) in damage['public_summary'] and enemy_of(after).display_name in damage['public_summary']


# --- 10 a pushed roll outside combat -------------------------------------------------------------------------

def test_a_failed_check_can_be_pushed_and_the_pushed_roll_is_final(battle):
    """The engine registers a pushed check and makes its result final (no Luck). How many times a roll may be pushed
    is the Keeper's rule, not enforced here."""
    first = battle.tool('skill_check', {'investigator': 'Ada', 'skill': '閃避', 'action_context': '跳過缺口'})
    assert first['ok'] and first['pending'] is True
    first_id = battle.load().pending_checks['player']['check_id']
    with patch.object(dice, 'skill_check', return_value=result(roll=90, tier='fail', value=40)):
        battle.check(first_id)
        offered = battle.load()
        assert offered.pending_luck_decisions['player']['check_id'] == first_id  # an ordinary failure may still buy Luck
        battle.luck(offered.pending_luck_decisions['player']['decision_id'])
    assert not battle.load().pending_checks and not battle.load().pending_luck_decisions and battle.effective().luck == 50

    pushed = battle.tool('skill_check', {'investigator': 'Ada', 'skill': '閃避', 'pushed': True,
                                         'action_context': '孤注一擲，縱身躍過缺口'})
    assert pushed['ok'] and pushed['pending'] is True
    pending = battle.load().pending_checks['player']
    assert pending['pushed'] is True and pending['check_id'] != first_id
    with patch.object(dice, 'skill_check', return_value=result(roll=95, tier='fail', value=40)) as rng:
        battle.check(pending['check_id'])
        final = battle.load()
        assert not final.pending_luck_decisions and 'player' not in final.pending_checks  # final: no Luck, nothing left to answer
        assert battle.effective().luck == 50
        draws, snapshot = rng.call_count, final.to_dict()
        battle.check(pending['check_id'])
        assert rng.call_count == draws and battle.load().to_dict() == snapshot
