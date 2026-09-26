import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import combat, db, keeper
from app.agents import executor, supervisor
from app.domain.models import AgentMessage, MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.repositories import group_state
from app.services import prompt_config, turn_context, turn_resolution


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    s = GroupState(group_id="turn-review", timeline_id="timeline-test")
    s.characters["a"] = Character(name="Marco", owner_id="a", dex=60, carried_items=["一瓶煤油"])
    s.characters["b"] = Character(name="Ken", owner_id="b", dex=80)
    group_state.save_state(s)
    return s


def pending(identity="old", context="製作道具"):
    return {"type": "skill", "skill": "投擲", "check_id": identity,
            "timeline_id": "timeline-test", "action_context": context}


def decision(state, kind, **fields):
    return json.dumps({"disposition": kind, "actor_character_id": turn_context.character_id(state, "a"),
                       "evidence_refs": ["state"], **fields}, ensure_ascii=False)


def message(state):
    return AgentMessage({"state": state, "text": "我取消尚未擲骰的投擲", "user_id": "a",
                         "display_name": "Marco", "speaker_role": "player"})


def test_pending_and_luck_are_authoritative_inputs(state):
    state.pending_checks["a"] = pending()
    state.pending_luck_decisions["b"] = {"decision_id": "luck-b", "roll": 68, "action_context": "閃避"}
    with patch.object(keeper.scene_digest, "latest_digest", return_value=None):
        text = keeper._build_dynamic_prompt(state, "a")
    for value in ("old", "製作道具", "luck-b", "閃避", '"owner_id": "b"'):
        assert value in text
    assert state.pending_checks["a"]["check_id"] == "old"


def test_history_does_not_supply_old_inventory_or_combat(state):
    old = {"timeline_id": state.timeline_id, "state_revision": 0, "public": {
        "characters": {"a": {"carried_items": ["STALE_INVENTORY"]}},
        "consumed_or_removed_items": [{"item": "煤油兩瓶"}], "known_clues": [{"text": "線索"}],
    }, "private": {"combat": {"name": "STALE_COMBAT"}, "facts": [{"text": "秘密線索"}]}}
    saved = deepcopy(old)
    with patch.object(keeper.scene_digest, "latest_digest", return_value=old):
        text = keeper._build_dynamic_prompt(state, "a")
    assert "一瓶煤油" in text and "煤油兩瓶" in text and "秘密線索" in text
    assert "STALE_INVENTORY" not in text and "STALE_COMBAT" not in text
    assert '"state_revision": 0' in text and old == saved
    old["state_revision"] = state.state_revision + 1
    assert turn_context.digest_history(state, old) == ""
    old["state_revision"] = 0
    old["timeline_id"] = "old-timeline"
    assert turn_context.digest_history(state, old) == ""


def test_reacquired_item_keeps_history_and_current_inventory(state):
    for name in ("remove_carried_item", "add_carried_item"):
        result = keeper._execute_tool(state, name, {"investigator": "Marco", "item": "一瓶煤油"}, [], [])
        assert result["ok"]
    stored = group_state.load_state(state.group_id)
    assert stored.get_active_character("a").carried_items == ["一瓶煤油"]
    assert stored.consumed_or_removed_items[-1]["item"] == "一瓶煤油"
    assert next(c for c in turn_context.current_state(stored)["characters"] if c["owner_id"] == "a")["carried_items"] == ["一瓶煤油"]


def test_real_cancellation_reaches_narrator_without_extra_provider_call(state):
    state.pending_checks = {"a": pending(), "b": pending("other", "偵查")}
    group_state.save_state(state)
    seen = []
    async def provider(*args, **kwargs):
        assert "old" in args[1] and "製作道具" in args[1]
        result = await args[5]("clear_pending_check", {"investigator": "Marco"})
        seen.append(result)
        return decision(state, "cancelled", check_id="old", evidence_refs=["tool:1"])
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, "LLM_PROVIDER", "openai"), patch.object(executor, "_PROVIDERS", {"openai": SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert fake.await_count == 1 and fake.call_args.kwargs["enable_wrapup"] is False
    assert result.turn_resolution.disposition == "cancelled"
    assert seen[0]["current_turn_state"]["pending_checks"][0]["check_id"] == "other"
    stored = group_state.load_state(state.group_id)
    assert "a" not in stored.pending_checks and stored.pending_checks["b"]["check_id"] == "other"
    reply = prompt_config.enforce_mechanic_check_consistency("請投擲", result)
    assert "已取消" in reply and "/coc check" not in reply


def test_claimed_cancellation_does_not_clear_real_pending(state):
    state.pending_checks["a"] = pending()
    fake = AsyncMock(return_value=decision(state, "cancelled", check_id="old"))
    with patch.object(executor, "LLM_PROVIDER", "openai"), patch.object(executor, "_PROVIDERS", {"openai": SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert result.turn_resolution.disposition == "incomplete"
    assert "a" in state.pending_checks
    assert "已取消" not in prompt_config.enforce_mechanic_check_consistency("已取消", result)


def test_off_turn_attack_is_deferred_end_to_end(state):
    combat.start_combat(state)
    group_state.save_state(state)
    waiting = turn_context.character_id(state, "b")
    fake = AsyncMock(return_value=decision(state, "deferred", waiting_for=waiting, reason="還沒輪到 Marco"))
    async def context(**kwargs):
        return AgentMessage(kwargs)
    with patch.object(executor, "LLM_PROVIDER", "openai"), patch.object(executor, "_PROVIDERS", {"openai": SimpleNamespace(run_conversation=fake)}), \
            patch.object(supervisor.context_builder, "build_context", side_effect=context), \
            patch.object(supervisor.narrator, "run_narrator", AsyncMock(return_value=("Marco揮拳，完成攻擊檢定後才能確定。", [], []))):
        reply, _, _ = asyncio.run(supervisor.run_turn(state, "a", "Marco", "我揮拳", None, "player", state.group_id))
    assert "Ken" in reply and "尚未執行" in reply and "揮拳" not in reply
    assert state.combat.current_index == 0 and not state.pending_checks
    assert fake.await_count == 1


@pytest.mark.parametrize("text", ["not-json", "[]", '{"disposition": []}', '{"disposition":"await_check","actor_character_id":"fake"}'])
def test_malformed_resolution_is_incomplete(state, text):
    result = turn_resolution.validate_resolution(text, state=state, user_id="a", before_pending={}, before_luck={},
                                                tool_events=[], has_scenario=False, before_actor={})
    assert result.disposition == "incomplete"


def test_unknown_or_failed_evidence_is_rejected(state):
    result = turn_resolution.validate_resolution(decision(state, "resolved_without_check", evidence_refs=["tool:1"]),
        state=state, user_id="a", before_pending={}, before_luck={}, tool_events=[{"name": "search_scenario", "result": {"ok": False}}],
        has_scenario=False, before_actor={})
    assert result.disposition == "incomplete"


def test_no_check_scenario_adjudication_needs_no_artificial_roll(state):
    result = turn_resolution.validate_resolution(decision(state, "resolved_without_check", evidence_refs=["scenario_context"]),
        state=state, user_id="a", before_pending={}, before_luck={}, tool_events=[], has_scenario=True, before_actor={})
    assert result.disposition == "resolved_without_check" and not state.pending_checks


def test_instruction_without_actual_check_is_stopped_but_negation_is_preserved():
    result = MechanicResult(True, "none", [], StateDelta())
    assert "不需要擲骰" in prompt_config.enforce_mechanic_check_consistency("完成這次鬥毆攻擊檢定後，才能確定結果。", result)
    negated = "不用完成這次鬥毆檢定後才能離開。"
    assert prompt_config.enforce_mechanic_check_consistency(negated, result) == negated


def test_resolution_does_not_publish_free_text_reason():
    result = MechanicResult(True, "none", [], StateDelta(), turn_resolution=TurnResolution(disposition="deferred", reason="SECRET"))
    assert "SECRET" not in prompt_config.enforce_mechanic_check_consistency("SECRET", result)


def test_await_check_verifies_id_and_timeline(state):
    state.pending_checks['a'] = pending('real')
    kwargs = {'state': state, 'user_id': 'a', 'before_pending': {}, 'before_luck': {}, 'tool_events': [], 'has_scenario': False, 'before_actor': {}}
    assert turn_resolution.validate_resolution(decision(state, 'await_check', check_id='real'), **kwargs).disposition == 'await_check'
    assert turn_resolution.validate_resolution(decision(state, 'await_check', check_id='wrong'), **kwargs).disposition == 'incomplete'
    state.pending_checks['a']['timeline_id'] = 'old'
    assert turn_resolution.validate_resolution(decision(state, 'await_check', check_id='real'), **kwargs).disposition == 'incomplete'


def test_waiting_luck_cannot_be_reported_as_unrolled_check(state):
    state.pending_checks['a'] = pending('real')
    state.pending_luck_decisions['a'] = {'decision_id': 'luck-real', 'timeline_id': state.timeline_id}
    kwargs = {'state': state, 'user_id': 'a', 'before_pending': {}, 'before_luck': {}, 'tool_events': [], 'has_scenario': False, 'before_actor': {}}
    assert turn_resolution.validate_resolution(decision(state, 'await_check', check_id='real'), **kwargs).disposition == 'incomplete'
    assert turn_resolution.validate_resolution(decision(state, 'await_luck', check_id='luck-real'), **kwargs).disposition == 'await_luck'


def test_deferred_claim_cannot_hide_spent_ammunition(state):
    combat.start_combat(state)
    char = state.get_active_character('a')
    char.weapons = {'gun': {'ammo': 6, 'ammo_max': 6}}
    before = turn_resolution.actor_snapshot(state, 'a')
    char.weapons['gun']['ammo'] = 5
    result = turn_resolution.validate_resolution(decision(state, 'deferred', waiting_for=turn_context.character_id(state, 'b')),
        state=state, user_id='a', before_pending={}, before_luck={}, tool_events=[], has_scenario=False, before_actor=before)
    assert result.disposition == 'incomplete'
    assert char.weapons['gun']['ammo'] == 5  # no invented rollback or repeated mutation


def test_invalid_completion_keeps_real_inventory_mutation(state):
    async def provider(*args, **kwargs):
        await args[5]('remove_carried_item', {'investigator': 'Marco', 'item': '一瓶煤油'})
        return 'invalid completion'
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert result.turn_resolution.disposition == 'incomplete' and fake.await_count == 1
    stored = group_state.load_state(state.group_id)
    assert stored.get_active_character('a').carried_items == []
    assert len(stored.consumed_or_removed_items) == 1


@pytest.mark.parametrize('investigator,refs,expected', [
    ('Marco', ['tool:1'], 'resolved'),
    ('Ken', ['tool:1'], 'incomplete'),
    ('Marco', ['state'], 'incomplete'),
])
def test_resolved_requires_referenced_actor_result(state, investigator, refs, expected):
    result = turn_resolution.validate_resolution(decision(state, 'resolved', evidence_refs=refs),
        state=state, user_id='a', before_pending={}, before_luck={}, before_actor={}, has_scenario=False,
        tool_events=[{'name': 'skill_check', 'result': {'ok': True, 'resolved': True,
            'investigator': investigator, 'timeline_id': state.timeline_id}}])
    assert result.disposition == expected


def test_referenced_check_not_overridden_by_other_players_luck(state):
    state.pending_checks['b'] = pending('ken-check')
    state.pending_luck_decisions['a'] = {'decision_id': 'marco-luck', 'timeline_id': state.timeline_id}
    fake = AsyncMock(return_value=decision(state, 'await_check', check_id='ken-check',
                                         waiting_for=turn_context.character_id(state, 'b')))
    captured = []
    async def context(**kwargs):
        return AgentMessage(kwargs)
    async def narrate(msg):
        captured.append(msg.payload['mechanic_result'])
        return '等待 Ken 檢定。', [], []
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}), \
            patch.object(supervisor.context_builder, 'build_context', side_effect=context), \
            patch.object(supervisor.narrator, 'run_narrator', side_effect=narrate):
        asyncio.run(supervisor.run_turn(state, 'a', 'Marco', '等待 Ken', None, 'player', state.group_id))
    assert captured[0].check_status['pending']['check_id'] == 'ken-check'
    assert captured[0].check_status['pending_luck'] is None
    assert state.pending_luck_decisions['a']['decision_id'] == 'marco-luck'


@pytest.mark.parametrize('kind', ['resolved', 'resolved_without_check'])
def test_real_transfer_completes_with_unchanged_old_check(state, kind):
    state.pending_checks['a'] = pending('old-inspection', '偵查木板牆')
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('remove_carried_item', {'investigator': 'Marco', 'item': '一瓶煤油'})
        await args[5]('add_carried_item', {'investigator': 'Ken', 'item': '一瓶煤油'})
        return decision(state, kind, evidence_refs=['tool:1', 'tool:2'])
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert result.turn_resolution.disposition == 'resolved_without_check'
    stored = group_state.load_state(state.group_id)
    assert stored.pending_checks['a']['check_id'] == 'old-inspection'
    assert stored.get_active_character('a').carried_items == []
    assert stored.get_active_character('b').carried_items == ['一瓶煤油']
    assert fake.await_count == 1


def test_real_crafting_resolved_uses_inventory_evidence(state):
    async def provider(*args, **kwargs):
        await args[5]('remove_carried_item', {'investigator': 'Marco', 'item': '一瓶煤油'})
        await args[5]('add_carried_item', {'investigator': 'Marco', 'item': '未點燃燃燒瓶'})
        return decision(state, 'resolved', evidence_refs=['tool:1', 'tool:2'])
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert result.turn_resolution.disposition == 'resolved_without_check'
    assert group_state.load_state(state.group_id).get_active_character('a').carried_items == ['未點燃燃燒瓶']


def test_real_end_combat_is_completion_without_roll(state):
    combat.start_combat(state)
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('end_combat', {})
        return decision(state, 'resolved', evidence_refs=['tool:1'])
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert result.turn_resolution.disposition == 'resolved_without_check'
    assert not group_state.load_state(state.group_id).combat.active


@pytest.mark.parametrize('mode', ['old_craft_check', 'new_other_check', 'failed_add', 'missing_ref', 'old_luck'])
def test_completion_does_not_hide_remaining_or_unproven_work(state, mode):
    if mode == 'old_craft_check':
        state.pending_checks['a'] = pending()
    if mode == 'old_luck':
        state.pending_luck_decisions['a'] = {'decision_id': 'luck-old'}
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('remove_carried_item', {'investigator': 'Marco', 'item': '一瓶煤油'})
        target = 'nobody' if mode == 'failed_add' else ('Marco' if mode == 'old_craft_check' else 'Ken')
        await args[5]('add_carried_item', {'investigator': target, 'item': '未點燃燃燒瓶' if mode == 'old_craft_check' else '一瓶煤油'})
        if mode == 'new_other_check':
            state.pending_checks['b'] = pending('new-check')
        refs = ['tool:1'] if mode in {'missing_ref', 'failed_add'} else ['tool:1', 'tool:2']
        return decision(state, 'resolved_without_check', evidence_refs=refs)
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert result.turn_resolution.disposition == 'incomplete'
    assert fake.await_count == 1  # failed validation never replays mutations


def test_compensated_ammunition_change_is_not_deferred(state):
    combat.start_combat(state)
    state.get_active_character('a').weapons = {'gun': {'ammo': 6, 'ammo_max': 6}}
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('adjust_ammo', {'investigator': 'Marco', 'weapon': 'gun', 'delta': -1})
        await args[5]('adjust_ammo', {'investigator': 'Marco', 'weapon': 'gun', 'delta': 1})
        return decision(state, 'deferred', waiting_for=turn_context.character_id(state, 'b'))
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert state.get_active_character('a').weapons['gun']['ammo'] == 6
    assert result.turn_resolution.disposition == 'incomplete'


@pytest.mark.parametrize('name,result', [
    ('get_character_sheet', {'ok': True}),
    ('clear_pending_check', {'ok': True, 'cleared': False}),
    ('end_combat', {'ok': True}),
    ('search_scenario', {'ok': True, 'results': ''}),
])
def test_successful_read_or_noop_does_not_prove_completion(state, name, result):
    resolved = turn_resolution.validate_resolution(decision(state, 'resolved', evidence_refs=['tool:1']),
        state=state, user_id='a', before_pending={}, before_luck={}, before_actor={}, has_scenario=False,
        tool_events=[{'name': name, 'result': result}])
    assert resolved.disposition == 'incomplete'


def test_supervisor_hands_off_completed_transfer_and_old_pending_together(state):
    state.pending_checks['a'] = pending('old-inspection', '偵查木板牆')
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('remove_carried_item', {'investigator': 'Marco', 'item': '一瓶煤油'})
        await args[5]('add_carried_item', {'investigator': 'Ken', 'item': '一瓶煤油'})
        return decision(state, 'resolved', evidence_refs=['tool:1', 'tool:2'])
    async def context(**kwargs):
        return AgentMessage(kwargs)
    async def narrate(msg):
        mechanic = msg.payload['mechanic_result']
        assert mechanic.turn_resolution.disposition == 'resolved_without_check'
        assert mechanic.check_status['pending']['check_id'] == 'old-inspection'
        assert mechanic.check_status['pending']['action_context'] == '偵查木板牆'
        return '煤油已交給 Ken。', [], []
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}), \
            patch.object(supervisor.context_builder, 'build_context', side_effect=context), \
            patch.object(supervisor.narrator, 'run_narrator', side_effect=narrate):
        reply, _, _ = asyncio.run(supervisor.run_turn(state, 'a', 'Marco', '我把煤油交給 Ken', None, 'player', state.group_id))
    assert '煤油已交給 Ken' in reply and '/coc check' in reply
    assert '尚未完整處理' not in reply
