import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import combat, config, db, keeper
from app.agents import executor, supervisor
from app.domain.models import AgentMessage, MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.providers import registry
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
    with patch.object(config, "LLM_PROVIDER", "openai"), patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": SimpleNamespace(run_conversation=fake)}):
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
    with patch.object(config, "LLM_PROVIDER", "openai"), patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": SimpleNamespace(run_conversation=fake)}):
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
    with patch.object(config, "LLM_PROVIDER", "openai"), patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": SimpleNamespace(run_conversation=fake)}), \
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
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
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
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}), \
            patch.object(supervisor.context_builder, 'build_context', side_effect=context), \
            patch.object(supervisor.narrator, 'run_narrator', side_effect=narrate):
        asyncio.run(supervisor.run_turn(state, 'a', 'Marco', '等待 Ken', None, 'player', state.group_id))
    assert captured[0].check_status['pending']['check_id'] == 'ken-check'
    assert captured[0].check_status['pending_luck'] is None
    assert state.pending_luck_decisions['a']['decision_id'] == 'marco-luck'


@pytest.mark.parametrize('add_first', [False, True])
@pytest.mark.parametrize('kind', ['resolved', 'resolved_without_check'])
def test_real_transfer_completes_with_unchanged_old_check(state, kind, add_first):
    state.pending_checks['a'] = pending('old-inspection', '偵查木板牆')
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        calls = [('remove_carried_item', 'Marco'), ('add_carried_item', 'Ken')]
        for name, owner in reversed(calls) if add_first else calls:
            await args[5](name, {'investigator': owner, 'item': '一瓶煤油'})
        return decision(state, kind, evidence_refs=['tool:1', 'tool:2'])
    fake = AsyncMock(side_effect=provider)
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
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
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
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
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
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
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
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
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
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


@pytest.mark.parametrize('add_first', [False, True])
def test_supervisor_hands_off_completed_transfer_and_old_pending_together(state, add_first):
    state.pending_checks['a'] = pending('old-inspection', '偵查木板牆')
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        calls = [('remove_carried_item', 'Marco'), ('add_carried_item', 'Ken')]
        for name, owner in reversed(calls) if add_first else calls:
            await args[5](name, {'investigator': owner, 'item': '一瓶煤油'})
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
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}), \
            patch.object(supervisor.context_builder, 'build_context', side_effect=context), \
            patch.object(supervisor.narrator, 'run_narrator', side_effect=narrate):
        reply, _, _ = asyncio.run(supervisor.run_turn(state, 'a', 'Marco', '我把煤油交給 Ken', None, 'player', state.group_id))
    assert '煤油已交給 Ken' in reply and '/coc check' in reply
    assert '尚未完整處理' not in reply


def test_truncated_executor_preserves_prior_committed_tool_without_replay(state, monkeypatch):
    from app.providers import openai_provider
    def response(status, name, item):
        return SimpleNamespace(status=status, id=status, usage=None,
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[SimpleNamespace(type="function_call", name=name, call_id=status,
                arguments=json.dumps({"investigator": "Marco", "item": item}))])
    create = AsyncMock(side_effect=[response("completed", "remove_carried_item", "一瓶煤油"),
                                   response("incomplete", "add_carried_item", "不應新增")])
    monkeypatch.setattr(openai_provider, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_provider, "_create_response_async", create)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(registry, "CONVERSATION_PROVIDERS", {"openai": openai_provider})
    result = asyncio.run(executor.run_executor(message(state)))
    stored = group_state.load_state(state.group_id)
    assert stored.get_active_character("a").carried_items == []
    assert len(stored.consumed_or_removed_items) == 1
    assert result.turn_resolution.disposition == "incomplete"
    assert create.await_count == 2


@pytest.mark.parametrize("kind", ["player_action", "resolved_check_followup", "opening_fallback"])
def test_truncated_narrator_uses_safe_fallback(state, monkeypatch, kind):
    from app.agents import narrator
    from app.providers import openai_provider
    create = AsyncMock(return_value=SimpleNamespace(status="incomplete", incomplete_details=None, output=[]))
    monkeypatch.setattr(openai_provider, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_provider, "_create_response_async", create)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(registry, "CONVERSATION_PROVIDERS", {"openai": openai_provider})
    msg = message(state)
    msg.payload.update(turn_kind=kind, resolved_check_context={"investigator": "Marco", "roll": 42, "outcome": "成功"})
    reply, _, _ = asyncio.run(narrator.run_narrator(msg))
    assert msg.payload["narration_failed"]
    assert create.await_count == 1
    if kind == "opening_fallback":
        assert "遊戲尚未開始" in reply
    else:
        assert "重做" in reply and "不要" in reply
        if kind == "resolved_check_followup":
            assert "42" in reply and "已結算" in reply


@pytest.mark.parametrize('add_first', [False, True])
@pytest.mark.parametrize('mode', ['same_owner', 'different_item', 'duplicate_add', 'noop_add',
                                  'failed_remove', 'missing_ref', 'final_state_mismatch'])
def test_invalid_transfer_never_bypasses_old_pending(state, add_first, mode):
    state.pending_checks['a'] = pending('old-inspection', '偵查木板牆')
    if mode == 'noop_add':
        state.get_active_character('b').carried_items = ['一瓶煤油']
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        calls = [
            ('remove_carried_item', {'investigator': 'Marco', 'item': '不存在' if mode == 'failed_remove' else '一瓶煤油'}),
            ('add_carried_item', {'investigator': 'Marco' if mode == 'same_owner' else 'Ken',
                                 'item': '空瓶' if mode == 'different_item' else '一瓶煤油'}),
        ]
        for name, arguments in reversed(calls) if add_first else calls:
            await args[5](name, arguments)
        refs = ['tool:1', 'tool:2']
        if mode == 'duplicate_add':
            await args[5]('add_carried_item', {'investigator': 'Ken', 'item': '空瓶'})
            refs.append('tool:3')
        if mode == 'missing_ref':
            refs.pop()
        if mode == 'final_state_mismatch':
            state.get_active_character('b').carried_items.append('未引用的變更')
        return decision(state, 'resolved_without_check', evidence_refs=refs)
    fake = AsyncMock(side_effect=provider)
    with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert result.turn_resolution.disposition == 'incomplete'
    assert state.pending_checks['a']['check_id'] == 'old-inspection'
    assert fake.await_count == 1


@pytest.mark.parametrize('add_first', [False, True])
def test_transfer_receipts_must_remove_exactly_one_item(state, add_first):
    state.pending_checks['a'] = pending('old-inspection', '偵查木板牆')
    state.get_active_character('a').carried_items = []
    state.get_active_character('b').carried_items = ['一瓶煤油']
    events = [
        {'name': 'remove_carried_item', 'arguments': {'item': '一瓶煤油'},
         'inventory_before': {'Marco': ['一瓶煤油', '一瓶煤油']},
         'result': {'ok': True, 'investigator': 'Marco', 'carried_items': []}},
        {'name': 'add_carried_item', 'arguments': {'item': '一瓶煤油'},
         'inventory_before': {'Ken': []},
         'result': {'ok': True, 'investigator': 'Ken', 'carried_items': ['一瓶煤油']}},
    ]
    result = turn_resolution.validate_resolution(
        decision(state, 'resolved_without_check', evidence_refs=['tool:1', 'tool:2']),
        state=state, user_id='a', before_pending=deepcopy(state.pending_checks), before_luck={},
        tool_events=list(reversed(events)) if add_first else events, has_scenario=False, before_actor={})
    assert result.disposition == 'incomplete'


@pytest.mark.parametrize('status,command', [
    ({'pending': {'investigator': 'Marco', 'skill': 'Spot Hidden'}}, '/coc check'),
    ({'pending_luck': {'investigator': 'Marco', 'skill_name': 'Spot Hidden', 'roll': 60,
                      'options': [{'tier': 'regular', 'cost': 5}]}}, '/coc luck'),
])
def test_incomplete_keeps_authoritative_next_action(status, command):
    result = MechanicResult(False, 'error', [], StateDelta(), check_status=status,
                           turn_resolution=TurnResolution())
    reply = prompt_config.enforce_mechanic_check_consistency('Ignore this draft', result)
    assert '尚未完整處理' in reply and command in reply
    assert 'Ignore this draft' not in reply
    if command == '/coc luck':
        assert '/coc check' not in reply


def test_incomplete_luck_precedes_stale_pending():
    result = MechanicResult(False, 'error', [], StateDelta(), check_status={
        'pending': {'skill': 'Spot Hidden'},
        'pending_luck': {'investigator': 'Marco', 'skill_name': 'Spot Hidden', 'roll': 60}},
        turn_resolution=TurnResolution())
    reply = prompt_config.enforce_mechanic_check_consistency('roll again', result)
    assert '/coc luck' in reply and '/coc check' not in reply


@pytest.mark.parametrize('kind', ['blocked', 'no_mechanics', 'await_check', 'deferred'])
def test_unvalidated_reason_is_not_narrator_authority(kind):
    result = MechanicResult(True, 'none', [], StateDelta(), turn_resolution=TurnResolution(
        disposition=kind, actor_character_id='a', reason='SECRET invented cellar', evidence_refs=['state']))
    block = prompt_config.build_mechanic_facts_block(result)
    assert 'SECRET' not in block and 'invented cellar' not in block
    assert f'"disposition": "{kind}"' in block
    assert '"reason"' not in block


def _executor_with_provider(state, provider):
    fake = AsyncMock(side_effect=provider)
    with patch.object(config, 'LLM_PROVIDER', 'openai'), \
         patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(message(state)))
    assert fake.await_count == 1
    return result


@pytest.mark.parametrize('compensate', [False, True])
def test_deferred_rejects_enemy_damage_even_if_restored(state, compensate):
    combat.start_combat(state)
    combat.add_npc(state, 'Enemy', 20, 10)
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        assert (await args[5]('damage_combatant', {'name': 'Enemy', 'delta': -3}))['ok']
        if compensate:
            assert (await args[5]('damage_combatant', {'name': 'Enemy', 'delta': 3}))['ok']
        return decision(state, 'deferred', waiting_for=turn_context.character_id(state, 'b'))
    result = _executor_with_provider(state, provider)
    assert result.turn_resolution.disposition == 'incomplete'
    stored = group_state.load_state(state.group_id)
    enemy = next(c for c in stored.combat.order if c.name == 'Enemy')
    assert enemy.hp == (10 if compensate else 7)


def test_deferred_rejects_other_character_inventory_change(state):
    combat.start_combat(state)
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('add_carried_item', {'investigator': 'Ken', 'item': 'Unrequested item'})
        return decision(state, 'deferred', waiting_for=turn_context.character_id(state, 'b'))
    result = _executor_with_provider(state, provider)
    assert result.turn_resolution.disposition == 'incomplete'
    assert 'Unrequested item' in group_state.load_state(state.group_id).get_active_character('b').carried_items


def test_setup_only_encounter_can_still_defer(state):
    async def provider(*args, **kwargs):
        await args[5]('start_combat', {})
        await args[5]('add_npc_to_combat', {'name': 'Enemy', 'dex': 20, 'hp': 10})
        await args[5]('get_combat_status', {})
        return decision(state, 'deferred', waiting_for=turn_context.character_id(state, 'b'))
    result = _executor_with_provider(state, provider)
    assert result.turn_resolution.disposition == 'deferred'
    assert group_state.load_state(state.group_id).combat.active


def test_setup_only_new_encounter_after_ended_fight_can_still_defer(state):
    state.last_combat_report = {
        'timeline_id': state.timeline_id,
        'ended': True,
        'combatants': [{'name': 'Old Enemy', 'side': 'enemy', 'defeated': True}],
    }
    group_state.save_state(state)

    async def provider(*args, **kwargs):
        assert (await args[5]('start_combat', {}))['ok']
        assert (await args[5]('add_npc_to_combat', {'name': 'New Enemy', 'dex': 20, 'hp': 10}))['ok']
        return decision(state, 'deferred', waiting_for=turn_context.character_id(state, 'b'))

    result = _executor_with_provider(state, provider)
    assert result.turn_resolution.disposition == 'deferred'
    stored = group_state.load_state(state.group_id)
    assert stored.combat.active
    assert stored.last_combat_report == {}


@pytest.mark.parametrize('extra', ['inventory', 'enemy', 'other_pending', 'compensated'])
def test_cancellation_rejects_unrelated_committed_changes(state, extra):
    state.pending_checks = {'a': pending(), 'b': pending('other')}
    combat.start_combat(state)
    combat.add_npc(state, 'Enemy', 20, 10)
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('clear_pending_check', {'investigator': 'Marco'})
        if extra in {'inventory', 'compensated'}:
            await args[5]('remove_carried_item', {'investigator': 'Marco', 'item': '一瓶煤油'})
            if extra == 'compensated':
                await args[5]('add_carried_item', {'investigator': 'Marco', 'item': '一瓶煤油'})
        elif extra == 'enemy':
            await args[5]('damage_combatant', {'name': 'Enemy', 'delta': -3})
        else:
            await args[5]('clear_pending_check', {'investigator': 'Ken'})
        return decision(state, 'cancelled', check_id='old', evidence_refs=['tool:1'])
    result = _executor_with_provider(state, provider)
    assert result.turn_resolution.disposition == 'incomplete'
    assert '已取消這筆' not in prompt_config.enforce_mechanic_check_consistency('cancelled', result)
    assert 'a' not in group_state.load_state(state.group_id).pending_checks


@pytest.mark.parametrize('failure', [RuntimeError('incomplete continuation'), TimeoutError('timeout')])
def test_private_outputs_survive_executor_failure(state, failure):
    state.scenario_library_id = 'test-scenario'
    msg = message(state)
    async def provider(*args, **kwargs):
        assert (await args[5]('send_private_info', {'investigator': 'Ken', 'message': 'private clue'}))['ok']
        assert (await args[5]('show_scenario_image', {'investigator': 'Ken', 'page_number': 2}))['ok']
        raise failure
    fake = AsyncMock(side_effect=provider)
    with patch.object(config, 'LLM_PROVIDER', 'openai'), \
         patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}), \
         patch.object(keeper.scenario_library, 'search_images', return_value=[{'page': 2, 'type': 'map'}]):
        result = asyncio.run(executor.run_executor(msg))
    assert not result.success and result.turn_resolution.disposition == 'incomplete'
    assert msg.payload['private_messages'] == [('b', 'private clue')]
    assert msg.payload['image_requests'] == [('b', 2)]
    assert fake.await_count == 1


def test_supervisor_preserves_failed_executor_private_outputs(state):
    state.scenario_library_id = 'test-scenario'
    state.game_started = True
    group_state.save_state(state)
    async def provider(*args, **kwargs):
        await args[5]('send_private_info', {'investigator': 'Ken', 'message': 'private clue'})
        await args[5]('show_scenario_image', {'investigator': 'Ken', 'page_number': 2})
        raise TimeoutError('after successful tools')
    async def context(**kwargs):
        return AgentMessage(kwargs)
    executor_call = AsyncMock(side_effect=provider)
    narration_call = AsyncMock(return_value='A safe public response.')
    stages = iter([executor_call, narration_call])  # one provider: the executor runs first, then the narrator
    async def run_conversation(*args, **kwargs):
        return await next(stages)(*args, **kwargs)
    with patch.object(config, 'LLM_PROVIDER', 'openai'), \
         patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=run_conversation)}), \
         patch.object(supervisor.context_builder, 'build_context', side_effect=context), \
         patch.object(supervisor.guard, 'enforce_narrative_safety', side_effect=lambda msg, text: text), \
         patch.object(keeper.scenario_library, 'search_images', return_value=[{'page': 2, 'type': 'map'}]):
        reply, private, images = asyncio.run(supervisor.run_turn(
            state, 'a', 'Marco', '我調查房間', None, 'player', state.group_id))
    assert '尚未完整處理' in reply and 'private clue' not in reply
    assert private == [('b', 'private clue')] and images == [('b', 2)]
    assert executor_call.await_count == narration_call.await_count == 1


@pytest.mark.parametrize('failure', ['malformed', 'exception', 'iteration_cap'])
def test_supervisor_failure_after_check_keeps_next_action(state, failure):
    async def provider(*args, **kwargs):
        assert (await args[5]('skill_check', {'investigator': 'Marco', 'skill': '偵查'}))['ok']
        if failure == 'exception':
            raise TimeoutError('after check')
        return '' if failure == 'iteration_cap' else 'not valid JSON'
    async def context(**kwargs):
        return AgentMessage(kwargs)
    fake = AsyncMock(side_effect=provider)
    with patch.object(config, 'LLM_PROVIDER', 'openai'), \
         patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}), \
         patch.object(supervisor.context_builder, 'build_context', side_effect=context), \
         patch.object(supervisor.narrator, 'run_narrator', AsyncMock(return_value=('請使用 /coc check。', [], []))), \
         patch.object(supervisor.guard, 'enforce_narrative_safety', side_effect=lambda msg, text: text):
        reply, _, _ = asyncio.run(supervisor.run_turn(
            state, 'a', 'Marco', '我偵查房間', None, 'player', state.group_id))
    assert '/coc check' in reply and '尚未完整處理' in reply
    assert group_state.load_state(state.group_id).pending_checks['a']['check_id']
    assert fake.await_count == 1


def test_deferred_without_full_snapshot_fails_closed(state):
    combat.start_combat(state)
    result = turn_resolution.validate_resolution(
        decision(state, 'deferred', waiting_for=turn_context.character_id(state, 'b')),
        state=state, user_id='a', before_pending={}, before_luck={}, tool_events=[],
        has_scenario=False, before_actor=turn_resolution.actor_snapshot(state, 'a'))
    assert result.disposition == 'incomplete'


def test_truncated_continuation_keeps_successful_private_output_queues(state, monkeypatch):
    from app.providers import openai_provider
    state.scenario_library_id = 'test-scenario'
    calls = [
        SimpleNamespace(type='function_call', name='send_private_info', call_id='private',
            arguments=json.dumps({'investigator': 'Ken', 'message': 'private clue'})),
        SimpleNamespace(type='function_call', name='show_scenario_image', call_id='image',
            arguments=json.dumps({'investigator': 'Ken', 'page_number': 2})),
    ]
    create = AsyncMock(side_effect=[
        SimpleNamespace(status='completed', id='completed', usage=None, output=calls),
        SimpleNamespace(status='incomplete', id='truncated', usage=None,
            incomplete_details=SimpleNamespace(reason='max_output_tokens'), output=calls),
    ])
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'test-key')
    monkeypatch.setattr(openai_provider, '_create_response_async', create)
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'openai')
    monkeypatch.setattr(registry, 'CONVERSATION_PROVIDERS', {'openai': openai_provider})
    msg = message(state)
    with patch.object(keeper.scenario_library, 'search_images', return_value=[{'page': 2, 'type': 'map'}]):
        result = asyncio.run(executor.run_executor(msg))
    assert not result.success and result.turn_resolution.disposition == 'incomplete'
    assert msg.payload['private_messages'] == [('b', 'private clue')]
    assert msg.payload['image_requests'] == [('b', 2)]
    assert create.await_count == 2


@pytest.mark.parametrize('complete', [False, True])
def test_cash_and_keys_evidence_gate_and_real_inventory_handoff(state, complete):
    """Search results are mocked; acquisition, persistence and validation are real."""
    original_tool = keeper._execute_tool
    def tool(s, name, data, *args):
        if name == 'search_scenario':
            return {'ok': True, 'results': '房東提供二十美元預付款與鑰匙。',
                    'complete_for_action': True if complete else None,
                    'evidence_record_ids': ['intro'] if complete else []}
        return original_tool(s, name, data, *args)

    async def provider(*args, **kwargs):
        callback = args[5]
        await callback('search_scenario', {'query': '房東 鑰匙 預付款'})
        for item in ('房屋鑰匙', '房東預付現金 20 美元'):
            receipt = await callback('add_carried_item', {'investigator': 'Marco', 'item': item})
            assert receipt['ok'] is complete
        return decision(state, 'resolved_without_check' if complete else 'incomplete',
                        evidence_refs=['tool:1', 'tool:2', 'tool:3'] if complete else ['state'])

    payload = message(state)
    payload.payload.update(text='拿走錢 跟鑰匙 並看一下地址', rag_context=(
        '【依據尚未完整】【取用完整性】[{"complete_for_action":false,"root_record_ids":["intro"]}]'))
    fake = AsyncMock(side_effect=provider)
    with patch.object(keeper, '_execute_tool', side_effect=tool), \
            patch.object(config, 'LLM_PROVIDER', 'openai'), \
            patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(payload))
    stored = group_state.load_state(state.group_id).get_active_character('a')
    assert ('房屋鑰匙' in stored.carried_items) is complete
    assert ('房東預付現金 20 美元' in stored.carried_items) is complete
    assert result.turn_resolution.disposition == ('resolved_without_check' if complete else 'incomplete')
    assert fake.await_count == 1
    if not complete:
        assert sum('失敗' in fact for fact in result.narrative_facts) == 2
        reply = prompt_config.enforce_mechanic_check_consistency('你已取得鑰匙。', result)
        assert '劇本依據' in reply and '更正原本' not in reply and '你已取得' not in reply
        result.check_status.update(state_changed=True, dice_rolled=True)
        reply = prompt_config.enforce_mechanic_check_consistency('', result)
        assert '已記錄的變更會保留' in reply and '不要重擲' in reply
        result.check_status['pending'] = {'investigator': 'Marco', 'skill': '偵查'}
        assert '/coc check' in prompt_config.enforce_mechanic_check_consistency('', result)


def test_followup_search_reuses_delivered_evidence_and_budgets_wire_receipts(state):
    from app import scenario_rag, scenario_retrieval
    from app.services import input_budget
    records = {name: {'page': 1, 'name': name, 'visibility': 'kp_only', 'type': 'scene',
                     'kp_text': name * 500, 'public_text': '', 'related_record_ids': []}
               for name in ('intro', 'address')}
    records['address']['kp_text'] = 'Address unspecified.'
    initial_budget = scenario_retrieval.BUDGET.set(10000)
    try:
        initial = scenario_retrieval.project(records, ['intro'], 'keys')
    finally:
        scenario_retrieval.BUDGET.reset(initial_budget)
    original_tool = keeper._execute_tool
    seen_budgets = []
    def budget(context, *args):
        seen_budgets.append(context)
        assert 'gameplay_before' not in str(context)
        assert 'gameplay_after' not in str(context)
        if len(seen_budgets) > 1:
            assert 'current_turn_state' in str(context)
        return 1800
    def tool(s, name, data, *args):
        if name == 'search_scenario':
            rows = scenario_retrieval.project(records, ['intro', 'address'], data['query'])
            assert rows[0]['complete_for_action']
            assert 'intro#kp_only' in rows[0]['reused_fragment_ids']
            return {'ok': True, 'results': scenario_rag.format_results(rows),
                    'complete_for_action': True, 'evidence_record_ids': ['intro', 'address']}
        return original_tool(s, name, data, *args)
    async def provider(*args, **kwargs):
        for query in ('address', 'address again'):
            await args[5]('search_scenario', {'query': query})
        await args[5]('add_carried_item', {'investigator': 'Marco', 'item': '鑰匙'})
        return decision(state, 'resolved_without_check', evidence_refs=['tool:1', 'tool:2', 'tool:3'])
    payload = message(state)
    payload.payload.update(text='拿走鑰匙並查看地址', rag_context=scenario_rag.format_results(initial))
    fake = AsyncMock(side_effect=provider)
    with patch.object(keeper, '_execute_tool', side_effect=tool), \
            patch.object(input_budget, '_encoding', return_value=None), \
            patch.object(scenario_retrieval, 'request_budget', side_effect=budget), \
            patch.object(config, 'LLM_PROVIDER', 'openai'), \
            patch.dict(registry.CONVERSATION_PROVIDERS, {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(payload))
    assert result.turn_resolution.disposition == 'resolved_without_check'
    assert '鑰匙' in group_state.load_state(state.group_id).get_active_character('a').carried_items
    assert len(seen_budgets) == 2
    assert not scenario_retrieval.DELIVERED_FRAGMENTS.get()


def test_blocked_travel_cannot_be_narrated_as_arrival_or_acquisition():
    result = MechanicResult(True, 'none', [], StateDelta(),
                            turn_resolution=TurnResolution(disposition='blocked'))
    bad = '你已到科比特宅門前，手中的鑰匙抵著鎖孔。'
    reply = prompt_config.enforce_mechanic_check_consistency(bad, result)
    assert '目前無法繼續' in reply and '鎖孔' not in reply and '你已到' not in reply
    result.check_status.update(state_changed=True, pending={'investigator': 'Marco', 'skill': '偵查'})
    reply = prompt_config.enforce_mechanic_check_consistency(bad, result)
    assert '已記錄的變更會保留' in reply and '/coc check' in reply
