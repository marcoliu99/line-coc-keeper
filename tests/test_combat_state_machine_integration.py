"""Actual SQLite/tool/player-control boundaries for provisional combat."""
from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app import combat_resources, config, db, dice, prompt_builder
from app.agents import supervisor
from app.commands import router
from app.commands.handlers.buttons import ButtonIO
from app.keeper_tools import registry
from app.models import Character, Combatant, CombatState, GroupState
from app.repositories import group_state

GROUP = 'combat-integration'
SOURCE = {'url': 'https://example.test/reviewed-scenario', 'revision': 'fixture-v1',
          'sha256': 'a' * 64, 'attack_mode': 'melee', 'extreme_rule': 'maximum'}


@dataclass
class Battle:
    messages: list[str] = field(default_factory=list)
    notifications: list[str] = field(default_factory=list)
    published_states: list[dict] = field(default_factory=list)
    restored_controls: list[Any] = field(default_factory=list)
    tool_count: int = 0

    def load(self) -> GroupState:
        return group_state.load_state(GROUP)

    def tool(self, name: str, arguments: dict | None = None, *, actor: str = 'player') -> dict:
        self.tool_count += 1
        current = self.load()
        call = registry.ToolCall(current, arguments or {}, [], [], 'player', name, actor_id=actor)
        return registry.REGISTRY[name].handler(call)

    def effective(self, character_id: str = 'char:ada') -> Character:
        current = self.load()
        return combat_resources.effective_character(current, current.characters_by_id[character_id])

    async def reply(self, message: str) -> None:
        self.messages.append(message)
        self.published_states.append(self.load().to_dict())

    async def notify(self, message: str) -> None:
        self.notifications.append(message)

    async def acknowledge(self) -> None:
        pass

    async def send_dm(self, _owner: str, message: str) -> None:
        await self.reply(message)

    async def send_image(self, *_args) -> None:
        pass

    async def restore(self, _checks, _luck, intents) -> None:
        self.restored_controls.append(intents)

    def io(self) -> ButtonIO:
        return ButtonIO(self.notify, self.acknowledge, self.reply, self.send_dm,
                        self.send_image, self.send_image, self.restore)

    def check(self, check_id: str, *, option: str = '', clicker: str = 'player', owner: str = 'player') -> None:
        asyncio.run(router.handle_check_button(GROUP, clicker, owner, option, check_id, self.io()))

    def luck(self, decision_id: str, *, choice: str = 'skip', owner: str = 'player') -> None:
        asyncio.run(router.handle_luck_button(GROUP, owner, owner, choice, decision_id, self.io()))

    def start(self, *, npc_first: bool = False) -> dict:
        return self.tool('initialize_combat', {'enemies': [{
            'name': 'Cultist', 'dex': 90 if npc_first else 40, 'hp': 20,
            'skills': {'Dodge': 40},
            'attacks': [{'id': 'claw', 'skill_name': 'Brawl', 'skill_value': 50,
                         'damage': '1d3', 'range_band': 'engaged'}], 'source': SOURCE,
        }]})

    def carry_hazard(self) -> str:
        self.start()
        identity = self.load().combat.combat_id
        assert self.tool('declare_combat_effect', {
            'combat_id': identity, 'effect_id': 'carried:hazard', 'target_id': 'pc:char:ada',
            'severity_id': 'minor', 'scope': 'round', 'stop_condition': 'leave reviewed hazard',
            'reason': 'controller selected reviewed generic severity'})['ok']
        preview = self.tool('preview_combat_settlement')['preview']
        assert self.tool('confirm_combat_settlement', {
            'combat_id': identity, 'settlement_id': preview['settlement_id'],
            'reason': 'controller carries future hazard'})['ok']
        return identity

    def declare(self, action_id: str = 'attack:1', *, weapon: str = 'unarmed',
                action_kind: str = 'melee', **kwargs) -> dict:
        current = self.load()
        target = next(p for p in current.combat.order if p.side == 'enemy')
        return self.tool('declare_combat_action', {'action_id': action_id, 'actor_id': 'pc:char:ada',
                         'target_id': target.combatant_id, 'weapon_reference': weapon,
                         'action_kind': action_kind, **kwargs})


@pytest.fixture
def battle(tmp_path, monkeypatch):
    with monkeypatch.context() as scoped:
        scoped.setattr(config, 'DB_PATH', tmp_path / 'state.db')
        scoped.setattr(config, 'DATA_DIR', tmp_path / 'groups')
        scoped.setattr(config, 'BACKUP_DIR', tmp_path / 'backups')
        # Exercise the owner's normal initialization on an isolated database;
        # never replace repository loading, saves, mirrors or mutation admission.
        importlib.reload(db)
        scoped.setattr(supervisor, 'run_turn', AsyncMock(return_value=('Recorded result.', [], [])))
        character = Character('Ada', 'player', character_id='char:ada', dex=80, hp=10, hp_max=10,
                              luck=50, san=50, mp=10,
                              skills={'格鬥（鬥毆）': 60, '閃避': 40, '射擊（手槍）': 60},
                              weapons={'.45 Automatic': {'ammo': 7, 'ammo_max': 7}})
        state = GroupState(GROUP, active=True, timeline_id='timeline:integration',
                           characters={'player': character}, characters_by_id={'char:ada': character},
                           active_character_id_by_user={'player': 'char:ada'})
        group_state.save_state(state)
        yield Battle()
    importlib.reload(db)


def result(*, roll: int = 30, tier: str = 'regular', value: int = 60):
    return dice.SkillCheckResult(value, roll, 0, 0, tier, tier not in ('fail', 'fumble'))


def test_real_tool_resource_checkpoint_preserves_committed_character_and_mirrors(battle):
    assert battle.start()['ok']
    arguments = {'investigator': 'Ada', 'field': 'mp', 'delta': -2,
                 'event_id': 'spell:1', 'reason': 'reviewed battle spell'}
    first = battle.tool('adjust_character', arguments)
    assert first['ok'] and first['provisional']
    battle.tool('adjust_character', arguments)
    reloaded = battle.load()
    assert reloaded.characters_by_id['char:ada'].mp == 10
    assert battle.effective().mp == 8
    assert db.get_json('characters', f'{GROUP}:char:ada')['sheet']['mp'] == 10
    assert db.get_json('characters', f'{GROUP}:player')['sheet']['mp'] == 10
    assert len([e for e in reloaded.combat.events if e['event_id'] == 'spell:1']) == 1


def test_public_check_button_saves_receipt_before_reply_and_stale_retry_never_rerolls(battle):
    assert battle.start()['ok']
    declaration = battle.declare()
    assert declaration['phase'] == 'PLAYER_ROLL'
    pending = battle.load().pending_checks['player']
    with patch.object(dice, 'skill_check', side_effect=[result(), result(roll=80, tier='fail', value=20)]) as rng, \
         patch('app.dice.random.randint', return_value=2):
        battle.check(pending['check_id'])
        checkpoint = battle.load()
        assert checkpoint.combat.phase == 'LUCK_DECISION'
        assert not checkpoint.combat.actions['attack:1']['completed']
        decision = checkpoint.pending_luck_decisions['player']
        assert battle.published_states[0]['combat']['phase'] == 'LUCK_DECISION'
        battle.luck(decision['decision_id'])
        checkpoint = battle.load()
        draws = rng.call_count
        assert checkpoint.combat.actions['attack:1']['completed']
        assert checkpoint.combat.roll_receipts
        assert battle.published_states
        assert any(s['combat']['actions']['attack:1']['completed'] for s in battle.published_states)
        battle.check(pending['check_id'])
        assert rng.call_count == draws
    assert battle.notifications
    assert battle.load().combat.roll_receipts == checkpoint.combat.roll_receipts
    assert battle.load().characters_by_id['char:ada'].hp == 10


def test_atomic_settlement_updates_both_mirrors_and_old_receipt_cannot_close_new_battle(battle):
    assert battle.start()['ok']
    battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'mp', 'delta': -3,
                'event_id': 'spell:commit', 'reason': 'reviewed battle spell'})
    before = battle.load()
    preview = battle.tool('preview_combat_settlement')['preview']
    assert battle.load().combat.active
    assert battle.load().characters_by_id['char:ada'].mp == 10
    arguments = {'combat_id': before.combat.combat_id, 'settlement_id': preview['settlement_id'],
                 'reason': 'bot controller reviewed absolute settlement'}
    settled = battle.tool('confirm_combat_settlement', arguments)
    assert settled['ok'] and not settled['provisional']
    committed = battle.load()
    assert not committed.combat.active
    assert committed.characters_by_id['char:ada'].mp == 7
    for identity in ('char:ada', 'player'):
        assert db.get_json('characters', f'{GROUP}:{identity}')['sheet']['mp'] == 7
    assert battle.tool('confirm_combat_settlement', arguments)['receipt'] == settled['receipt']
    assert battle.start()['ok']
    assert battle.load().combat.active
    assert battle.load().combat.combat_id != arguments['combat_id']
    new_battle = battle.load().to_dict()
    assert battle.tool('confirm_combat_settlement', arguments)['receipt'] == settled['receipt']
    assert battle.load().to_dict() == new_battle


def test_other_owner_check_click_cannot_draw_or_consume_player_control(battle):
    battle.start()
    battle.declare()
    before = battle.load().to_dict()
    check_id = before['pending_checks']['player']['check_id']
    with patch.object(dice, 'skill_check') as rng:
        battle.check(check_id, clicker='intruder')
        rng.assert_not_called()
    assert battle.load().to_dict() == before
    assert battle.notifications


@pytest.mark.parametrize(('tool_name', 'arguments'), [
    ('get_weapon_definition', {'reference': 'unarmed'}),
    ('get_damage_severity', {'severity_id': 'minor'}),
    ('get_damage_severity', {'severity_id': 'moderate'}),
    ('get_damage_severity', {'severity_id': 'severe'}),
    ('get_damage_severity', {'severity_id': 'deadly'}),
    ('get_damage_severity', {'severity_id': 'terminal'}),
    ('get_damage_severity', {'severity_id': 'splat'}),
])
def test_reviewed_catalog_lookup_is_reachable_and_read_only(battle, tool_name, arguments):
    before = battle.load().to_dict()
    resolved = battle.tool(tool_name, arguments)
    assert resolved['ok'] and resolved['status'] == 'resolved'
    assert resolved['definition']
    assert battle.load().to_dict() == before


def test_severity_lookup_does_not_guess_damage_from_prose(battle):
    before = battle.load().to_dict()
    with patch.object(dice, 'roll_expression') as rng:
        unresolved = battle.tool('get_damage_severity', {'severity_id': 'fire'})
        rng.assert_not_called()
    assert not unresolved['ok']
    assert battle.load().to_dict() == before


def test_external_character_conflict_blocks_absolute_commit_until_explicit_reconciliation(battle):
    battle.start()
    battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'mp', 'delta': -3,
                'event_id': 'spell:conflict', 'reason': 'reviewed battle spell'})
    preview = battle.tool('preview_combat_settlement')['preview']
    external = battle.load()
    external.characters_by_id['char:ada'].mp = 9
    external.characters['player'].mp = 9
    group_state.save_state(external)
    arguments = {'combat_id': external.combat.combat_id, 'settlement_id': preview['settlement_id'],
                 'reason': 'reviewed settlement'}
    with pytest.raises(ValueError, match='Persistent participant resources changed'):
        battle.tool('confirm_combat_settlement', arguments)
    assert battle.load().characters_by_id['char:ada'].mp == 9
    assert battle.effective().mp == 7
    battle.tool('reconcile_combat_baseline', {'combat_id': external.combat.combat_id,
                'investigator': 'Ada', 'event_id': 'baseline:1', 'decision': 'keep_working',
                'reason': 'explicit controller retains reviewed working total'})
    fresh = battle.tool('preview_combat_settlement')['preview']
    assert fresh['settlement_id'] != preview['settlement_id']
    arguments['settlement_id'] = fresh['settlement_id']
    assert battle.tool('confirm_combat_settlement', arguments)['ok']
    assert battle.load().characters_by_id['char:ada'].mp == 7


def test_whole_battle_rollback_preserves_baseline_and_closed_audit_across_retry(battle):
    battle.start()
    battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'mp', 'delta': -3,
                'event_id': 'spell:rollback', 'reason': 'reviewed battle spell'})
    current = battle.load()
    arguments = {'combat_id': current.combat.combat_id, 'event_id': 'rollback:1',
                 'reason': 'bot controller cancelled uncommitted battle'}
    receipt = battle.tool('rollback_combat', arguments)['receipt']
    reloaded = battle.load()
    assert not reloaded.combat.active
    assert reloaded.characters_by_id['char:ada'].mp == 10
    assert reloaded.closed_combat_receipts[current.combat.combat_id]['events']
    assert 'events' not in receipt
    assert battle.tool('rollback_combat', arguments)['receipt'] == receipt
    assert battle.start()['ok']
    assert battle.load().combat.active
    assert battle.load().combat.combat_id != arguments['combat_id']
    new_battle = battle.load().to_dict()
    assert battle.tool('rollback_combat', arguments)['receipt'] == receipt
    assert battle.load().to_dict() == new_battle


def test_settlement_storage_failure_rolls_back_mirrors_and_is_retryable(battle):
    battle.start()
    battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'mp', 'delta': -3,
                'event_id': 'spell:storage', 'reason': 'reviewed battle spell'})
    preview = battle.tool('preview_combat_settlement')['preview']
    before = battle.load().to_dict()
    arguments = {'combat_id': before['combat']['combat_id'], 'settlement_id': preview['settlement_id'],
                 'reason': 'reviewed settlement'}
    write = db.set_json_tx

    def fail_group_write(connection, table, key, value):
        if table == 'group_states':
            raise OSError('simulated storage unavailable after mirror writes')
        return write(connection, table, key, value)

    with patch.object(db, 'set_json_tx', side_effect=fail_group_write), \
         pytest.raises(OSError, match='storage unavailable'):
        battle.tool('confirm_combat_settlement', arguments)
    assert battle.load().to_dict() == before
    for identity in ('char:ada', 'player'):
        assert db.get_json('characters', f'{GROUP}:{identity}')['sheet']['mp'] == 10
    assert battle.tool('confirm_combat_settlement', arguments)['ok']
    assert battle.load().characters_by_id['char:ada'].mp == 7


def test_reserved_action_metadata_cannot_be_overwritten_by_tool_call(battle):
    battle.start()
    before = battle.load().to_dict()
    with patch.object(dice, 'skill_check') as rng:
        rejected = battle.declare('system:continuing-state')
        assert not rejected['ok']
        assert 'identity' in rejected['error']
        rng.assert_not_called()
    assert battle.load().to_dict() == before


@pytest.mark.parametrize(('original', 'corrected', 'major_wound'), [(2, 6, True), (6, 2, False)])
def test_corrected_hp_requires_explicit_injury_reconciliation_without_reroll(battle, original, corrected, major_wound):
    battle.start()
    battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -original,
                'event_id': 'damage:reviewed', 'reason': 'reviewed source damage'})
    state = battle.load()
    if state.pending_checks:
        with patch.object(dice, 'skill_check', return_value=result(roll=1, tier='critical', value=50)):
            battle.check(state.pending_checks['player']['check_id'])
    control = battle.tool('get_combat_status')['control']
    target_event = next(e['event_id'] for e in control['correctable_events'] if e['kind'] == 'resource')
    combat_id = state.combat.combat_id
    with patch.object(dice, 'skill_check') as rng:
        changed = battle.tool('correct_combat_event', {'combat_id': combat_id,
                    'target_event_id': target_event, 'event_id': 'damage:correction',
                    'changes': {'after': 10 - corrected}, 'reason': 'source-backed damage correction'})
        assert changed['phase'] == 'NEEDS_RULING'
        paused = battle.load()
        assert paused.combat.interaction['kind'] == 'correction_reconciliation'
        assert battle.effective().hp == 10 - corrected
        with pytest.raises(ValueError, match='Currently due combat work'):
            battle.tool('preview_combat_settlement')
        injury = {'major_wound': major_wound, 'unconscious': False, 'dying': False, 'dead': False}
        acknowledged = battle.tool('reconcile_combat_correction', {'combat_id': combat_id,
                    'event_id': 'injury:reconciliation', 'reason': 'controller resolved injury dependency',
                    'injury_by_character': {'char:ada': injury},
                    'acknowledge_action_ids': list(paused.combat.actions)})
        assert acknowledged['ok']
        rng.assert_not_called()
    reloaded = battle.load()
    assert not reloaded.combat.interaction
    assert battle.effective().injury == injury
    assert reloaded.characters_by_id['char:ada'].hp == 10
    assert [e['event_id'] for e in reloaded.combat.events].count('damage:reviewed') == 1
    assert any(e['event_id'] == 'damage:correction' for e in reloaded.combat.events)


def test_prior_effect_tick_is_provisional_in_new_battle_and_rollback_reuses_original_roll(battle):
    battle.start()
    combat_id = battle.load().combat.combat_id
    effect = battle.tool('declare_combat_effect', {'combat_id': combat_id, 'effect_id': 'hazard:1',
                'target_id': 'pc:char:ada', 'severity_id': 'minor', 'scope': 'round',
                'timing': 'round_end', 'stop_condition': 'leave explicitly reviewed hazard',
                'reason': 'controller selected reviewed generic damage severity'})
    assert effect['ok']
    preview = battle.tool('preview_combat_settlement')['preview']
    battle.tool('confirm_combat_settlement', {'combat_id': combat_id,
                'settlement_id': preview['settlement_id'], 'reason': 'carry future source-backed hazard'})
    committed = battle.load()
    assert len(committed.postcombat_obligations) == 1
    original = committed.postcombat_obligations[0]
    assert battle.start()['ok']
    assert battle.load().combat.active
    second_id = battle.load().combat.combat_id
    assert second_id != combat_id
    with patch('app.dice.random.randint', return_value=2) as rng:
        tick = battle.tool('process_postcombat_obligations', {'logical_round': 1, 'event_id': 'clock:1'})
        assert tick['ok']
        draws = rng.call_count
        assert draws > 0
        assert battle.effective().hp == 8
        assert battle.load().characters_by_id['char:ada'].hp == 10
        battle.tool('rollback_combat', {'combat_id': second_id, 'event_id': 'rollback:hazard',
                    'reason': 'discard only new battle working resources'})
        rolled_back = battle.load()
        restored = rolled_back.postcombat_obligations[0]
        assert restored['obligation_id'] == original['obligation_id']
        assert restored['next_trigger'] == original['next_trigger']
        assert rolled_back.mechanical_round == 1
        assert restored['roll_receipts']
        catchup = battle.tool('process_postcombat_obligations', {'logical_round': 1, 'event_id': 'clock:catchup'})
        assert catchup['ok']
        assert rng.call_count == draws
    assert battle.load().characters_by_id['char:ada'].hp == 8
    assert battle.load().postcombat_obligations[0]['next_trigger']['round'] == 2


def test_opt_in_autoroll_still_pauses_for_luck_and_spends_working_luck_once(battle):
    state = battle.load()
    state.autoroll_checks = True
    group_state.save_state(state)
    battle.start()
    with patch.object(dice, 'skill_check', return_value=result(roll=65, tier='fail')) as rng:
        declared = battle.declare('attack:auto')
        assert declared['phase'] == 'LUCK_DECISION'
        decision = battle.load().pending_luck_decisions['player']
        battle.luck(decision['decision_id'], choice='regular')
        draws = rng.call_count
        assert battle.effective().luck == 45
        assert battle.load().characters_by_id['char:ada'].luck == 50
        battle.luck(decision['decision_id'], choice='regular')
        assert rng.call_count == draws
        assert battle.effective().luck == 45


def test_npc_attack_owned_defense_choice_and_manual_roll_survive_reload(battle):
    battle.start()
    battle.declare('pc:opening')
    pending = battle.load().pending_checks['player']
    with patch.object(dice, 'skill_check', return_value=result(roll=100, tier='fumble')):
        battle.check(pending['check_id'])
    with patch.object(dice, 'skill_check', side_effect=[result(roll=1, tier='critical', value=50),
                                                      result(roll=1, tier='critical', value=40)]) as rng:
        declared = battle.tool('advance_combat_turn', {'actor_id': 'pc:char:ada', 'event_id': 'advance:opening'})
        assert declared['phase'] == 'PLAYER_CHOICE'
        retried = battle.tool('advance_combat_turn', {'actor_id': 'pc:char:ada', 'event_id': 'advance:opening'})
        assert retried == declared
        waiting = battle.load()
        wait_id = waiting.combat.interaction['interaction_id']
        before = waiting.to_dict()
        with pytest.raises(ValueError, match='owned check investigator binding'):
            battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'dodge'}, actor='intruder')
        assert battle.load().to_dict() == before
        chosen = battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'dodge'})
        assert chosen['phase'] == 'PLAYER_ROLL'
        before_retry = battle.load().to_dict()
        assert battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'dodge'}) == chosen
        assert battle.load().to_dict() == before_retry
        pending = battle.load().pending_checks['player']
        npc_action = pending['combat_context']['action_id']
        assert npc_action.startswith('npc:')
        battle.check(pending['check_id'])
        finished = battle.load()
        assert finished.combat.actions[npc_action]['completed']
        assert battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'dodge'}) == chosen
        draws = rng.call_count
        with pytest.raises(ValueError, match='owned check investigator binding'):
            battle.tool('submit_combat_choice', {'interaction_id': wait_id, 'choice': 'fight_back'})
        battle.check(pending['check_id'])
        assert rng.call_count == draws
    assert battle.load().characters_by_id['char:ada'].hp == 10


def test_single_shot_uses_owned_ammo_once_and_preserves_persistent_weapon_mapping(battle):
    battle.start()
    declared = battle.declare('shot:1', weapon='.45 Automatic', action_kind='single_shot', distance_yards=5)
    assert declared['phase'] == 'PLAYER_ROLL', declared
    pending = battle.load().pending_checks['player']
    with patch.object(dice, 'skill_check', return_value=result(roll=100, tier='fumble')) as rng:
        battle.check(pending['check_id'])
        checkpoint = battle.load()
        assert checkpoint.combat.actions['shot:1']['completed']
        assert battle.effective().weapons['.45 Automatic']['ammo'] == 6
        assert checkpoint.characters_by_id['char:ada'].weapons['.45 Automatic']['ammo'] == 7
        draws = rng.call_count
        duplicate = battle.declare('shot:1', weapon='.45 Automatic', action_kind='single_shot', distance_yards=5)
        assert duplicate['ok']
        assert rng.call_count == draws
        assert battle.effective().weapons['.45 Automatic']['ammo'] == 6
    assert db.get_json('characters', f'{GROUP}:char:ada')['sheet']['weapons']['.45 Automatic']['ammo'] == 7


@pytest.mark.parametrize(('weapon', 'action_kind', 'distance'), [
    ('unknown alien weapon', 'melee', None),
    ('.45 Automatic', 'single_shot', None),
    ('.45 Automatic', 'burst', 5),
])
def test_unsupported_or_unsourced_action_needs_ruling_before_rng_or_ammo(battle, weapon, action_kind, distance):
    battle.start()
    with patch.object(dice, 'skill_check') as rng:
        rejected = battle.declare('unsupported:1', weapon=weapon, action_kind=action_kind, distance_yards=distance)
        assert rejected['phase'] == 'NEEDS_RULING'
        rng.assert_not_called()
    assert battle.effective().weapons['.45 Automatic']['ammo'] == 7
    assert battle.load().characters_by_id['char:ada'].weapons['.45 Automatic']['ammo'] == 7
    assert not battle.load().pending_checks


def test_existing_resource_and_inventory_tools_share_working_snapshot(battle):
    battle.start()
    for resource_field, delta, expected in [('san', -2, 48), ('mp', -3, 7), ('luck', -4, 46)]:
        arguments = {'investigator': 'Ada', 'field': resource_field, 'delta': delta,
                     'event_id': f'resource:{resource_field}', 'reason': 'reviewed combat resource mutation'}
        battle.tool('adjust_character', arguments)
        battle.tool('adjust_character', arguments)
        assert getattr(battle.effective(), resource_field) == expected
    ammo = {'investigator': 'Ada', 'weapon': '.45 Automatic', 'delta': -2, 'event_id': 'ammo:2'}
    battle.tool('adjust_ammo', ammo)
    battle.tool('adjust_ammo', ammo)
    assert battle.effective().weapons['.45 Automatic']['ammo'] == 5
    battle.tool('adjust_ammo', {'investigator': 'Ada', 'weapon': '.45 Automatic',
                              'reload_full': True, 'event_id': 'reload:1'})
    battle.tool('add_status_tag', {'investigator': 'Ada', 'tag': 'Pinned', 'event_id': 'status:1'})
    sheet = battle.tool('get_character_sheet', {'investigator': 'Ada'})
    assert sheet['provisional']
    assert sheet['sheet']['status_tags'] == ['Pinned']
    assert sheet['sheet']['weapons']['.45 Automatic']['ammo'] == 7
    persistent = battle.load().characters_by_id['char:ada']
    assert (persistent.san, persistent.mp, persistent.luck) == (50, 10, 50)
    assert persistent.status_tags == []
    battle.tool('remove_status_tag', {'investigator': 'Ada', 'tag': 'Pinned', 'event_id': 'status:2'})
    assert battle.effective().status_tags == []


def test_unsupported_active_combat_is_refused_through_the_tool_without_a_guessed_baseline(battle):
    old = battle.load()
    old.characters_by_id['char:ada'].hp = 7
    old.characters['player'].hp = 7
    old.combat = CombatState(active=True, round_number=4, order=[
        Combatant('Ada', dex=80, hp=7, hp_max=10, is_pc=True, side='pc',
                  character_id='char:ada', combatant_id='pc:char:ada')])
    group_state.save_state(old)
    before = battle.load().to_dict()
    with pytest.raises(combat_resources.CombatAdmissionError, match='Start a new combat'):
        battle.tool('preview_combat_settlement')
    assert battle.load().to_dict() == before


def test_bot_diagnostics_expose_usable_ids_without_enemy_hp_in_player_status(battle):
    battle.start()
    status = battle.tool('get_combat_status')
    assert status['control']['combat_id'] == battle.load().combat.combat_id
    assert status['control']['current_actor_id'] == 'pc:char:ada'
    assert '20/20' not in status['status']
    battle.declare('diagnostic:action')
    waiting = battle.tool('get_combat_status')['control']
    assert waiting['interaction']['action_id'] == 'diagnostic:action'
    assert waiting['interaction']['check_id'] == battle.load().pending_checks['player']['check_id']
    assert any(a['action_id'] == 'diagnostic:action' for a in waiting['actions'])
    assert all('hp' not in a and 'hp_after' not in a for a in waiting['actions'])


def test_first_npc_runs_its_source_bound_turn_when_the_battle_opens_without_client_hit_or_damage(battle):
    with patch.object(dice, 'skill_check', return_value=result(roll=1, tier='critical', value=50)):
        opened = battle.start(npc_first=True)
    started = opened['opening_enemy_turn']
    assert started['phase'] == 'PLAYER_CHOICE'
    assert not battle.tool('plan_enemy_turn', {'enemy': 'Cultist'})['ok']  # the turn is already played
    pending = battle.load().pending_checks['player']
    assert pending['combat_context']['check_role'] == 'defense_choice'
    assert battle.load().characters_by_id['char:ada'].hp == 10
    with pytest.raises(ValueError, match='due combat work'):
        battle.tool('preview_combat_settlement')


def test_lost_player_reply_replays_saved_roll_without_rng_or_resource_cost(battle):
    battle.start()
    battle.declare('lost:roll')
    check_id = battle.load().pending_checks['player']['check_id']
    with patch.object(dice, 'skill_check', return_value=result(roll=30)) as rng:
        with patch.object(battle, 'reply', side_effect=RuntimeError('transport dropped response')), \
             pytest.raises(RuntimeError, match='transport dropped'):
            battle.check(check_id)
        durable = battle.load().to_dict()
        assert durable['combat']['phase'] == 'LUCK_DECISION'
        draws = rng.call_count
        battle.check(check_id)
        assert rng.call_count == draws
        assert battle.load().to_dict() == durable
    assert '擲出 30' in battle.notifications[-1]
    assert '60%' in battle.notifications[-1]
    assert '原骰值不會重擲' in battle.notifications[-1]


@pytest.mark.parametrize('pending_before_new_battle', [False, True])
def test_zero_hp_prior_dying_target_keeps_owned_wait_through_new_battle_and_rollback(battle, pending_before_new_battle):
    battle.start()
    for identity, damage in [('minor:4', 4), ('major:6', 6)]:
        assert battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -damage,
                          'event_id': identity, 'reason': 'reviewed single-hit damage'})['ok']
    assert battle.effective().injury['dying']
    first_wait = battle.load().pending_checks.get('player')
    if first_wait:
        with patch.object(dice, 'skill_check', return_value=result(roll=1, tier='critical', value=50)):
            battle.check(first_wait['check_id'])
    old_id = battle.load().combat.combat_id
    preview = battle.tool('preview_combat_settlement')['preview']
    battle.tool('confirm_combat_settlement', {'combat_id': old_id,
                'settlement_id': preview['settlement_id'], 'reason': 'carry future dying check'})
    baseline = battle.load()
    assert baseline.characters_by_id['char:ada'].hp == 0
    trigger = baseline.postcombat_obligations[0]['next_trigger']['round']
    assert trigger > baseline.mechanical_round
    if pending_before_new_battle:
        for logical_round in range(baseline.mechanical_round + 1, trigger + 1):
            assert battle.tool('process_postcombat_obligations', {
                'logical_round': logical_round, 'event_id': f'prior:clock:{logical_round}'})['ok']
        assert battle.load().pending_checks['player']['postcombat_context']['round'] == trigger
    battle.start()
    new_id = battle.load().combat.combat_id
    assert new_id != old_id
    assert battle.load().combat.active
    assert 'char:ada' in battle.load().combat.working_resources
    for logical_round in range(battle.load().mechanical_round + 1, trigger):
        assert battle.tool('process_postcombat_obligations', {
            'logical_round': logical_round, 'event_id': f'dying:clock:{logical_round}'})['ok']
    due = battle.tool('process_postcombat_obligations', {'logical_round': trigger, 'event_id': 'dying:round'})
    assert due['ok']
    pending = battle.load().pending_checks['player']
    assert pending['postcombat_context']['round'] == trigger
    assert baseline.postcombat_obligations[0]['character_id'] == 'char:ada'
    with pytest.raises(ValueError, match='[Pp]rior|[Cc]ommitted|due|pending'):
        battle.tool('rollback_combat', {'combat_id': new_id, 'event_id': 'dying:rollback',
                    'reason': 'discard only new battle'})
    assert battle.load().pending_checks['player']['check_id'] == pending['check_id']
    with patch.object(dice, 'skill_check', return_value=result(roll=1, tier='critical', value=50)) as rng:
        battle.check(pending['check_id'])
        assert not battle.load().pending_checks
        battle.tool('rollback_combat', {'combat_id': new_id, 'event_id': 'dying:rollback',
                    'reason': 'discard new battle after prior due work resolved'})
        restored = battle.load()
        assert restored.postcombat_obligations[0]['next_trigger']['round'] == trigger
        draws = rng.call_count
        replay = battle.tool('process_postcombat_obligations', {'logical_round': trigger, 'event_id': 'dying:catchup'})
        assert replay['ok']
        # Manual replay regenerates an owned control, whose authoritative roll
        # is the original durable CON receipt rather than another server draw.
        waiting = battle.load().pending_checks.get('player')
        if waiting:
            battle.check(waiting['check_id'])
        assert rng.call_count == draws
    assert battle.load().postcombat_obligations[0]['next_trigger']['round'] == trigger + 1, (battle.load().postcombat_obligations, battle.load().pending_checks, battle.notifications)


@pytest.mark.parametrize('during_new_battle', [False, True])
def test_source_bound_stop_persists_outside_battle_but_rolls_back_new_working_stop(battle, during_new_battle):
    source_id = battle.carry_hazard()
    prior = battle.load().postcombat_obligations
    if during_new_battle:
        battle.start()
        assert battle.load().combat.active
    stopped = battle.tool('stop_combat_effect', {'combat_id': source_id, 'effect_id': 'carried:hazard',
                         'event_id': 'hazard:stop', 'reason': 'controller confirms source stop condition'})
    assert stopped['ok']
    if during_new_battle:
        assert battle.load().postcombat_obligations == prior
        assert battle.tool('preview_combat_settlement')['ok']
        assert battle.load().combat.settlement['obligations'][0]['status'] == 'resolved'
        battle.tool('rollback_combat', {'combat_id': battle.load().combat.combat_id,
                    'event_id': 'stop:rollback', 'reason': 'discard provisional stop'})
        assert battle.load().postcombat_obligations == prior
    else:
        assert battle.load().postcombat_obligations[0]['status'] == 'resolved'
        with patch('app.dice.random.randint') as rng:
            assert battle.tool('process_postcombat_obligations', {'logical_round': 1, 'event_id': 'stopped:clock'})['ok']
            rng.assert_not_called()
        assert battle.load().characters_by_id['char:ada'].hp == 10


def test_unsourced_first_npc_ruling_can_be_cancelled_and_advanced_without_damage(battle):
    with patch.dict(SOURCE, {'attack_mode': 'melee'}, clear=True):
        assert battle.start(npc_first=True)['ok']
    before = battle.load()
    npc_id = before.combat.order[before.combat.current_index].combatant_id
    plan = battle.tool('plan_enemy_turn', {'enemy': 'Cultist'})
    with patch.object(dice, 'skill_check') as rng:
        blocked = battle.tool('run_enemy_combat_plan', {'plan_id': plan['plan_id']})
        assert blocked['phase'] == 'NEEDS_RULING'
        assert not blocked['ok']
        cancelled = battle.tool('resolve_combat_ruling', {'combat_id': before.combat.combat_id,
                    'action_id': blocked['action_id'], 'event_id': 'unsourced:cancel', 'decision': 'cancel',
                    'reason': 'controller cancels unsupported unsourced attack'})
        assert cancelled['ok']
        advanced = battle.tool('advance_combat_turn', {'actor_id': npc_id, 'event_id': 'unsourced:advance'})
        assert advanced['ok'], advanced
        rng.assert_not_called()
    current = battle.load()
    assert current.combat.order[current.combat.current_index].combatant_id == 'pc:char:ada'
    assert current.characters_by_id['char:ada'].hp == 10


def test_distinct_healer_owned_first_aid_stabilizes_only_bound_patient_and_receipt_once(battle):
    initial = battle.load()
    healer = Character('Grace', 'healer', character_id='char:grace', dex=70,
                       hp=10, hp_max=10, skills={'急救': 70})
    initial.characters['healer'] = healer
    initial.characters_by_id[healer.character_id] = healer
    initial.active_character_id_by_user['healer'] = healer.character_id
    group_state.save_state(initial)
    battle.start()
    for identity, damage in [('medical:minor', 4), ('medical:major', 6)]:
        battle.tool('adjust_character', {'investigator': 'Ada', 'field': 'hp', 'delta': -damage,
                    'event_id': identity, 'reason': 'reviewed single-hit damage'})
    pending = battle.load().pending_checks['player']
    with patch.object(dice, 'skill_check', return_value=result(roll=1, tier='critical', value=50)):
        battle.check(pending['check_id'])
    combat_id = battle.load().combat.combat_id
    preview = battle.tool('preview_combat_settlement')['preview']
    battle.tool('confirm_combat_settlement', {'combat_id': combat_id,
                'settlement_id': preview['settlement_id'], 'reason': 'carry future dying obligation'})
    declared = battle.tool('request_stabilization_check', {
        'healer_character_id': 'char:grace', 'character_id': 'char:ada',
        'event_id': 'firstaid:1', 'reason': 'source-bound First Aid stabilization'}, actor='healer')
    assert declared['ok'] and declared['pending']
    check = battle.load().pending_checks['healer']
    assert check['medical_context']['character_id'] == 'char:ada'
    assert check['medical_context']['healer_character_id'] == 'char:grace'
    with patch.object(dice, 'skill_check', return_value=result(roll=1, tier='critical', value=70)) as rng:
        battle.check(check['check_id'], clicker='healer', owner='healer')
        request = {'character_id': 'char:ada', 'source_check_id': check['check_id'],
                   'event_id': 'stabilization:1', 'reason': 'controller applies successful bound First Aid'}
        first = battle.tool('stabilize_investigator', request)
        assert first['ok']
        receipt = battle.tool('stabilize_investigator', request)
        assert receipt == first
        assert rng.call_count == 1
        wrong = battle.tool('stabilize_investigator', {**request, 'character_id': 'char:grace',
                            'event_id': 'stabilization:wrong'})
        assert not wrong['ok']
    final = battle.load()
    assert not final.characters_by_id['char:ada'].injury['dying']
    assert final.characters_by_id['char:grace'].hp == 10
    assert all(o['status'] == 'resolved' for o in final.postcombat_obligations)


def test_owned_defense_choice_button_replays_saved_delivery_without_new_roll(battle):
    with patch.object(dice, 'skill_check', return_value=result(roll=1, tier='critical', value=50)) as rng:
        battle.start(npc_first=True)
        pending = battle.load().pending_checks['player']
        assert pending['type'] == 'choice'
        with patch.object(battle, 'reply', side_effect=RuntimeError('choice delivery lost')), \
             pytest.raises(RuntimeError, match='choice delivery lost'):
            battle.check(pending['check_id'], option='#0')
        # The current NPC action, not the prior initializer metadata, owns delivery evidence.
        receipt = battle.load().combat.actions[pending['combat_context']['action_id']]
        original = receipt['control_delivery_receipts'][pending['check_id']]['reply_text']
        saved = battle.load().to_dict()
        draws = rng.call_count
        battle.check(pending['check_id'], option='#0')
        assert battle.notifications[-1] == original
        assert battle.load().to_dict() == saved
        assert rng.call_count == draws
        battle.check(pending['check_id'], option='#1')
        assert battle.notifications[-1] != original
        assert battle.load().to_dict() == saved
        battle.check(pending['check_id'], option='#0', clicker='intruder')
        assert battle.load().to_dict() == saved


def test_active_keeper_prompts_follow_managed_source_runner_and_settlement(battle):
    battle.start()
    current = battle.load()
    static = prompt_builder.build_static_prompt(current)
    dynamic = prompt_builder.build_dynamic_prompt(current, 'player')
    assistant = prompt_builder.build_dynamic_prompt(current, 'player', speaker_role='kp_assistant')
    for prompt in (static, dynamic, assistant):
        assert 'plan_enemy_turn' in prompt
        assert 'run_enemy_combat_plan' in prompt
        assert 'confirm_combat_settlement' in prompt
        assert 'offer_npc_attack_defense_choice' not in prompt
        assert 'resolve_enemy_action' not in prompt
        assert 'apply_final_combat_damage' not in prompt
        assert 'roll_weapon_damage' not in prompt
    assert 'adjust_character' in static
    assert 'never duplicate' in static
    assert 'owned choice/check/Luck controls' in dynamic
    assert 'manual rolls remain' in dynamic


@pytest.mark.parametrize('role', ['attack', 'defense', 'defense_choice', 'injury', 'medical', 'counter',
                                 'injury:char:ada'])
def test_check_role_codec_preserves_durable_control_identity(role):
    from app.models import CombatCheckContext, CombatCheckIdentity
    identity = CombatCheckIdentity.from_serialized(role)
    context = CombatCheckContext('battle', 'action', 'interaction', identity)
    assert context.to_dict()['check_role'] == role
    if role.startswith('injury:'):
        assert identity.role == 'injury'
        assert identity.character_id == 'char:ada'


@pytest.mark.parametrize('role', ['invented', 'injury:', 17, None])
def test_unknown_check_role_cannot_enter_mechanical_boundary(role):
    from app.models import CombatCheckIdentity
    with pytest.raises((TypeError, ValueError), match='Unknown combat check role'):
        CombatCheckIdentity.from_serialized(role)


def test_unknown_effect_stop_cannot_publish_success_or_disable_future_damage(battle):
    source = battle.carry_hazard()
    before = battle.load().to_dict()
    arguments = {'combat_id': source, 'effect_id': 'typo:hazard',
                 'event_id': 'stop:missing', 'reason': 'Controller requests a stop'}
    assert not battle.tool('stop_combat_effect', arguments)['ok']
    assert battle.load().to_dict() == before
    arguments.update(effect_id='carried:hazard', combat_id='wrong:source')
    assert not battle.tool('stop_combat_effect', arguments)['ok']
    assert battle.load().to_dict() == before
    arguments.update(combat_id=source, event_id='stop:correct')
    accepted = battle.tool('stop_combat_effect', arguments)
    assert accepted['ok']
    stopped = battle.load().to_dict()
    assert battle.tool('stop_combat_effect', arguments) == accepted
    assert battle.load().to_dict() == stopped
    assert battle.load().postcombat_obligations[0]['status'] == 'resolved'


def test_source_ruling_keeps_owned_instance_key_through_real_tool_reload(battle):
    from app import combat_rules
    weapon = combat_rules.resolve_weapon('.45 Automatic').definition
    assert weapon is not None
    state = battle.load()
    character = state.characters_by_id['char:ada']
    character.weapons = {'owned:gun': {'ammo': 7, 'ammo_max': 7}}
    character.weapon_instances = {'owned:gun': {'definition_id': weapon.id}}
    group_state.save_state(state)
    battle.start()
    assert battle.declare(weapon='owned:gun', action_kind='single_shot')['phase'] == 'NEEDS_RULING'
    current = battle.load()
    action_id = next(k for k, a in current.combat.actions.items() if not a.get('completed'))
    resumed = battle.tool('resolve_combat_ruling', {
        'combat_id': current.combat.combat_id, 'action_id': action_id, 'event_id': 'ruling:ownedgun',
        'reason': 'Verified physical distance', 'decision': 'resume', 'distance_yards': 1})
    assert resumed['phase'] == 'PLAYER_ROLL'
    reloaded = battle.load()
    assert reloaded.combat.actions[action_id]['ammo_key'] == 'owned:gun'
    assert reloaded.combat.actions[action_id]['weapon_reference'] == 'owned:gun'
    assert battle.effective().weapons['owned:gun']['ammo'] == 7
