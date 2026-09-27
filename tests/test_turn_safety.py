"""S0 counterexamples and S1 fault/entry contracts; isolated DB, no live API."""
import asyncio
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import checkpoints, db, keeper, legacy_commands, locks, spoiler_policy
from app.agents import executor, guard, narrator, supervisor, tool_gateway
from app.commands import router
from app.commands.handlers import (
    character,
    combat,
    correct,
    map_handler,
    purchase,
    system,
)
from app.domain.models import (
    AgentMessage,
    MechanicResult,
    ObservedOutcome,
    StateDelta,
    TurnResolution,
)
from app.models import Character, Combatant, GroupState
from app.repositories.group_state import load_state, save_state
from app.services import mutation_admission as admission
from app.services import turn_delivery


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    state = GroupState("safety", timeline_id="timeline-original", game_started=True)
    state.characters["u"] = Character("Ada", "u", hp=10, hp_max=10, skills={"偵查": 50})
    save_state(state)
    return state


def message(state):
    return AgentMessage({"state": state, "text": "拿起手電筒然後偵查", "user_id": "u",
                         "display_name": "Ada", "speaker_role": "player", "intent": "GAMEPLAY_ACTION"})


@pytest.fixture
def held(state):
    owner = admission.start_worker(state.group_id, state.timeline_id, "test-blocked-worker")
    admission.detach(owner)
    try:
        yield owner
    finally:
        admission.settle(owner)


def test_successful_tool_then_provider_failure_preserves_specific_results(state):
    msg = message(state)

    async def provider(*args, **kwargs):
        result = await args[5]("add_carried_item", {"investigator": "Ada", "item": "手電筒"})
        assert result["ok"]
        raise RuntimeError("provider disconnected after tool commit")

    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, "LLM_PROVIDER", "openai"), patch.object(
        executor, "_PROVIDERS", {"openai": SimpleNamespace(run_conversation=fake)}
    ):
        result = asyncio.run(executor.run_executor(msg))
    assert fake.await_count == 1
    assert result.execution_health == "partial"
    assert result.turn_resolution.disposition == "incomplete"
    assert any("手電筒" in fact for fact in result.narrative_facts)
    assert not any("纯敘事" in fact or "純敘事" in fact for fact in result.narrative_facts)
    assert result.observed_outcomes[0].evidence_ref == "tool:1"
    assert load_state(state.group_id).characters["u"].carried_items == ["手電筒"]
    reply, _ = turn_delivery.finalize(msg, "這次行動尚未完整處理。")
    assert "手電筒" in reply


def test_followup_tool_success_survives_narrator_failure(state):
    state.combat.active = True
    state.combat.order = [Combatant("Ada", 50, 10, 10, is_pc=True)]
    save_state(state)
    msg = message(state)
    msg.payload.update(turn_kind="resolved_check_followup", resolved_check_context={"roll": 12, "outcome": "成功"})

    async def provider(*args, **kwargs):
        assert (await args[5]("apply_combat_damage", {"target": "Ada", "raw_damage": 1}))["ok"]
        raise RuntimeError("narration interrupted")

    fake = AsyncMock(side_effect=provider)
    with patch.object(narrator, "LLM_PROVIDER", "openai"), patch.object(
        narrator, "_PROVIDERS", {"openai": SimpleNamespace(run_conversation=fake)}
    ):
        narrative, _, _ = asyncio.run(narrator.run_narrator(msg))
    reply, _ = turn_delivery.finalize(msg, narrative)
    assert "已結算傷害 1" in reply
    assert load_state(state.group_id).characters["u"].hp == 9
    assert fake.await_count == 1


def test_spoiler_fallback_keeps_legitimate_result_and_original_pending(state):
    state.pending_checks["u"] = {"check_id": "original-check", "timeline_id": state.timeline_id, "skill": "偵查"}
    state.established_facts.append({"text": "PROTECTED-SECRET", "visibility": "kp_only"})
    msg = message(state)
    msg.payload["observed_outcomes"] = [ObservedOutcome("tool:1", "roll_dice", True, "骰子已結算：總值 12。", "public")]
    original = deepcopy(state.pending_checks)
    reply, _ = turn_delivery.finalize(msg, "PROTECTED-SECRET")
    assert "PROTECTED-SECRET" not in reply
    assert "12" in reply and "/coc check" in reply
    assert state.pending_checks == original
    assert msg.payload["delivery_envelope"].interactions[0].identity == "original-check"
    assert msg.payload["delivery_envelope"].status == "projected_fallback"


def test_private_pending_has_private_contract_and_never_public_controls(state):
    state.pending_checks["u"] = {"check_id": "private-check", "timeline_id": state.timeline_id,
                                  "skill": "秘密技能", "visibility": "player_private"}
    msg = message(state)
    reply, private = turn_delivery.finalize(msg, "你觀察著房間。")
    assert reply == "你觀察著房間。" and "/coc check" not in reply
    assert private == [("u", "已有待處理檢定／選擇，請使用檢定按鈕或 /coc check。")]
    assert not msg.payload["delivery_envelope"].interactions


def test_unsafe_projected_fallback_blocks_without_removing_pending(state):
    state.pending_checks["u"] = {"check_id": "keep", "timeline_id": state.timeline_id}
    msg = message(state)
    with patch.object(spoiler_policy, "sanitize_public_text", return_value=SimpleNamespace(is_safe=False)):
        text, _ = turn_delivery.finalize(msg, "unsafe")
    assert text == turn_delivery.BLOCKED_NOTICE
    assert msg.payload["delivery_envelope"].status == "blocked"
    assert state.pending_checks["u"]["check_id"] == "keep"


def test_contract_rejects_stale_identity_and_cross_recipient(state):
    state.pending_checks["u"] = {"check_id": "first", "timeline_id": state.timeline_id}
    ref = turn_delivery.interaction_refs(state)[0]
    envelope = turn_delivery.DeliveryEnvelope("out", "public", "", "", interactions=[ref])
    state.pending_checks["u"]["check_id"] = "second"
    assert not turn_delivery.validate_delivery_contract(envelope, envelope.render(), state)
    private_fact = ObservedOutcome("private", "send_private_info", True, "secret", "player_private", "u")
    envelope.interactions = []
    envelope.authorized_facts = [private_fact]
    assert not turn_delivery.validate_delivery_contract(envelope, envelope.render(), state)


def test_guard_rewrite_is_rechecked_then_final_safety_runs(state):
    state.pending_checks["u"] = {"check_id": "keep", "timeline_id": state.timeline_id, "skill": "偵查"}
    state.established_facts.append({"text": "PROTECTED-SECRET", "visibility": "kp_only"})
    save_state(state)
    msg = message(state)
    result = MechanicResult(True, "none", [], StateDelta(), turn_resolution=TurnResolution(disposition="await_check"))
    with patch.object(supervisor.context_builder, "build_context", AsyncMock(return_value=msg)), \
         patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
         patch.object(executor, "run_executor", AsyncMock(return_value=result)), \
         patch.object(narrator, "run_narrator", AsyncMock(return_value=("請偵查", [], []))), \
         patch.object(guard, "enforce_narrative_safety", AsyncMock(return_value="PROTECTED-SECRET")) as repair:
        reply, _, _ = asyncio.run(supervisor.run_turn(state, "u", "Ada", "偵查", None, "player", state.group_id))
    assert repair.await_count == 1
    assert "PROTECTED-SECRET" not in reply and "/coc check" in reply
    assert load_state(state.group_id).pending_checks["u"]["check_id"] == "keep"


def test_valid_narrative_is_preserved(state):
    text = "窗外的雨聲仍未止息，你收起筆記，走向門口。"
    reply, private = turn_delivery.finalize(message(state), text)
    assert reply == text and private == []


@pytest.mark.parametrize("text", ["我攻擊", "/coc check", "/coc luck skip", "/coc sudo u act attack",
                                  "/coc purchase quote", "/coc go hallway", "/coc switch other",
                                  "/coc newgame", "/coc rollback saved", "/coc correct issue"])
def test_router_mutation_entry_matrix_is_held_before_dispatch(state, held, text):
    reply = AsyncMock()
    with patch.object(router, "_handle_text_message_impl", AsyncMock()) as dispatch:
        asyncio.run(router.handle_text_message(state.group_id, "u", AsyncMock(), reply, AsyncMock(),
                                               AsyncMock(), AsyncMock(), text))
    dispatch.assert_not_awaited()
    reply.assert_awaited_once_with(admission.NOTICE)


@pytest.mark.parametrize("handler,kwargs", [
    (character.handle_character_command, {"parts": ["/coc", "switch", "other"], "user_id": "u", "send_dm": None}),
    (correct.handle_correct_command, {"parts": ["/coc", "correct", "issue"], "user_id": "u"}),
    (legacy_commands.handle_role_sheet_upload, {"file_text": "", "file_name": "role_test.md"}),
    (legacy_commands.handle_map_upload, {"push": None, "yaml_bytes": b"", "file_name": "map.yaml"}),
    (legacy_commands.handle_pdf_upload, {"push": None, "pdf_bytes": b"", "file_name": "scenario.pdf"}),
    (combat.handle_combat_command, {"parts": ["/coc", "combat", "next"]}),
    (purchase.handle, {"parts": ["/coc", "purchase", "quote"], "user_id": "u"}),
    (map_handler.handle_map_command, {"parts": ["/coc", "go", "room"], "user_id": "u", "send_image": None}),
    (system.handle_system_command, {"parts": ["/coc", "newgame"], "user_id": "u", "send_dm": None,
                                    "send_image": None, "send_dm_image": None}),
])
def test_direct_command_entry_matrix(state, held, handler, kwargs):
    reply = AsyncMock()
    asyncio.run(handler(conversation_id=state.group_id, reply=reply, **kwargs))
    reply.assert_awaited_once_with(admission.NOTICE)


@pytest.mark.parametrize("call", [
    lambda s: keeper._execute_tool(s, "roll_dice", {"expression": "1d100"}, [], []),
    lambda s: legacy_commands._resolve_check_deterministically(s.group_id, "u", "/coc check"),
    lambda s: legacy_commands._resolve_luck_decision_deterministically(s.group_id, "u", "skip"),
    lambda s: legacy_commands._resolve_map_action_transaction(s.group_id, "u", "go hallway"),
    lambda s: keeper._mutate_and_save_state(s, lambda latest: setattr(latest, "scenario_title", "bad")),
    lambda s: save_state(GroupState(s.group_id), reason="newgame"),
    lambda s: checkpoints.rollback(s.group_id, "checkpoint", actor_id="kp"),
])
def test_authoritative_entries_hold_before_side_effects(state, held, call):
    before = load_state(state.group_id).to_dict()
    with patch.object(keeper.dice, "roll_expression") as roll, pytest.raises(admission.MutationHeld):
        call(state)
    roll.assert_not_called()
    assert load_state(state.group_id).to_dict() == before


def test_read_queries_and_other_groups_remain_available(state, held):
    assert load_state(state.group_id).timeline_id == state.timeline_id
    other = GroupState("other", timeline_id="other-timeline")
    save_state(other)
    assert load_state("other").state_revision == 1

    async def read_under_lock(*args):
        async with locks.get_conversation_lock(state.group_id):
            assert load_state(state.group_id).characters["u"].name == "Ada"
            with pytest.raises(admission.MutationHeld):
                save_state(state)

    with patch.object(router, "_handle_text_message_impl", AsyncMock(side_effect=read_under_lock)) as read:
        asyncio.run(router.handle_text_message(state.group_id, "u", AsyncMock(), AsyncMock(), AsyncMock(),
                                               AsyncMock(), AsyncMock(), "/coc status"))
    read.assert_awaited_once()


def test_old_owner_cannot_release_a_new_hold(state):
    old = admission.start_worker(state.group_id, state.timeline_id, "old")
    admission.detach(old)
    admission.settle(old)
    new = admission.start_worker(state.group_id, state.timeline_id, "new")
    admission.detach(new)
    try:
        admission.settle(old)
        assert admission.is_held(state.group_id)
    finally:
        admission.settle(new)


def test_worker_original_timeline_is_rechecked_before_mutator(state):
    owner = admission.start_worker(state.group_id, state.timeline_id, "old-work")
    replacement = GroupState(state.group_id, timeline_id="replacement")
    save_state(replacement, reason="newgame")
    ran = []
    try:
        with admission.bind(owner), pytest.raises(admission.MutationHeld):
            keeper._mutate_and_save_state(state, lambda latest: ran.append(True))
    finally:
        admission.settle(owner)
    assert ran == [] and load_state(state.group_id).timeline_id == "replacement"


def test_cancelled_task_does_not_release_live_worker_or_replay_dice(state):
    async def scenario():
        started, release, stopped = threading.Event(), threading.Event(), threading.Event()
        facts, outcomes = [], []
        calls = []

        def worker(*args):
            calls.append(args[1])
            started.set()
            try:
                assert release.wait(2)
                return {"ok": True, "total": 42, "expression": "1d100"}
            finally:
                stopped.set()

        execute = tool_gateway.make_tool_executor(state, [], [], "player", facts, observed_outcomes=outcomes)
        with patch.object(keeper, "_execute_tool", side_effect=worker), \
             patch.object(tool_gateway, "PROVIDER_SHUTDOWN_GRACE_SECONDS", 0.001), \
             patch.object(keeper, "record_tool_recovery_marker_bounded", AsyncMock()):
            task = asyncio.create_task(execute("roll_dice", {"expression": "1d100"}))
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert admission.is_held(state.group_id)
                with pytest.raises(admission.MutationHeld):
                    await execute("roll_dice", {"expression": "1d100"})
                assert not stopped.is_set()
            finally:
                release.set()
                assert await asyncio.to_thread(stopped.wait, 1)
                # Let actual worker finish observation/settlement, not an elapsed timeout assumption.
                for _ in range(100):
                    if not admission.is_held(state.group_id):
                        break
                    await asyncio.sleep(0.001)
            assert not admission.is_held(state.group_id)
            assert calls == ["roll_dice"]
            assert len(outcomes) == 1 and "42" in outcomes[0].public_text
    asyncio.run(scenario())


def test_background_maintenance_consumes_hold(state, held):
    with pytest.raises(admission.MutationHeld):
        keeper._persist_memory_maintenance_state(
            state.group_id, "new", [{"role": "user", "content": "old"}],
            timeline_id=state.timeline_id, base_summary="", source_revision=state.state_revision,
            idempotency_key="maintenance", embedding=[],
        )
    with pytest.raises(admission.MutationHeld):
        keeper.run_scene_digest_maintenance(state.group_id)


def test_private_button_preserves_original_identity_and_recipient(state):
    from app import discord_bot
    from app.check_identity import compact_identity_token

    async def scenario():
        private_channel = SimpleNamespace(id=123)
        public_channel = SimpleNamespace(id=456)
        entry = {"type": "skill", "skill": "偵查", "check_id": "check-private", "visibility": "player_private"}
        with patch.object(discord_bot.client, "get_user", return_value=private_channel), \
             patch.object(discord_bot, "_send_direct_message", AsyncMock()) as send:
            await discord_bot._send_check_button(public_channel, "discord-channel-456", "123", entry, "Ada", state.timeline_id, None)
        assert send.call_args.args[0] is private_channel
        button = send.call_args.kwargs["view"].children[0]
        assert compact_identity_token("check", "123", "check-private", state.timeline_id) in button.custom_id
    asyncio.run(scenario())


def test_observed_button_entry_reports_hold_without_running_callback(state, held):
    from app import discord_bot

    # The callback's stored conversation lock is authoritative even in a DM.
    async def raw(self, interaction):
        async with locks.get_conversation_lock(state.group_id):
            raise AssertionError("held callback ran")

    callback = discord_bot._observed_interaction(raw)
    interaction = SimpleNamespace(channel=SimpleNamespace(id=123))
    with patch.object(discord_bot, "_send_interaction_message", AsyncMock()) as reply:
        asyncio.run(callback(object(), interaction))
    reply.assert_awaited_once_with(interaction, admission.NOTICE, ephemeral=True)


def test_supervisor_hold_blocks_opening_and_followup_before_context(state, held):
    for kind in ("player_action", "resolved_check_followup", "opening_fallback"):
        with patch.object(supervisor.context_builder, "build_context", AsyncMock()) as build, \
             pytest.raises(admission.MutationHeld):
            asyncio.run(supervisor.run_turn(state, "u", "Ada", "text", None, "player", state.group_id,
                                           turn_kind=kind, resolved_check_context={"roll": 42}))
        build.assert_not_awaited()


def test_cancellation_before_thread_start_does_not_leave_an_owner(state):
    # Rejected/stopped generations must not affect subsequent work.
    owner = admission.start_worker(state.group_id, state.timeline_id, "never-started")
    admission.settle(owner)
    admission.detach(owner)
    assert not admission.is_held(state.group_id)


def test_shutdown_reports_real_worker_even_when_no_async_task_remains(state):
    from app import async_utils
    owner = admission.start_worker(state.group_id, state.timeline_id, "running-thread")
    admission.mark_started(owner)
    admission.detach(owner)
    try:
        admission.reject_unstarted(owner)
        with patch.object(async_utils.observability, "event") as event:
            asyncio.run(async_utils.wait_for_background_tasks(0.001))
        assert admission.is_held(state.group_id)
        assert event.call_args.kwargs["running_worker_count"] == 1
        assert event.call_args.args[0] == "async.background_task.shutdown_degraded"
    finally:
        admission.settle(owner)


def test_cancelled_queued_worker_cannot_start_later(state):
    owner = admission.start_worker(state.group_id, state.timeline_id, "queued")
    admission.detach(owner)
    admission.reject_unstarted(owner)
    assert not admission.mark_started(owner)
    assert not admission.is_held(state.group_id)


def test_private_information_and_raw_search_are_never_public_fallback_facts(state):
    observations = [turn_delivery.observe_tool(name, result, i) for i, (name, result) in enumerate([
        ("send_private_info", {"ok": True, "delivered_to": "Ada", "message": "secret"}),
        ("search_scenario", {"ok": True, "results": "SECRET SCENARIO"}),
        ("record_clue", {"ok": True, "record": {"text": "secret clue", "visibility": "kp_only"}}),
        ("apply_combat_damage", {"ok": True}),
    ])]
    msg = message(state)
    msg.payload["observed_outcomes"] = observations
    reply, _ = turn_delivery.finalize(msg, "已確認目前狀態。")
    assert reply == "已確認目前狀態。"
    assert all(item.audience == "internal" for item in observations)


def test_luck_fallback_does_not_recreate_roll_or_change_identity(state):
    state.pending_luck_decisions["u"] = {"decision_id": "luck-original", "timeline_id": state.timeline_id,
                                          "roll": 68, "options": [{"tier": "regular", "cost": 8}]}
    state.established_facts.append({"text": "PROTECTED-SECRET", "visibility": "kp_only"})
    before = deepcopy(state.pending_luck_decisions)
    msg = message(state)
    text, _ = turn_delivery.finalize(msg, "PROTECTED-SECRET")
    assert "/coc luck skip" in text and "/coc check" not in text
    assert state.pending_luck_decisions == before
    assert msg.payload["delivery_envelope"].interactions[0].identity == "luck-original"


def test_stale_background_digest_cannot_publish_into_new_timeline(state):
    from app import scene_digest
    save_state(GroupState(state.group_id, timeline_id="replacement"), reason="newgame")
    with pytest.raises(admission.MutationHeld, match="source changed"):
        scene_digest.create_digest(state)
    assert db.list_keys("scene_digests") == []


def test_explicit_old_digest_cleanup_is_allowed_after_timeline_switch(state):
    from app import scene_digest
    old = scene_digest.create_digest(state)
    save_state(GroupState(state.group_id, timeline_id="replacement"), reason="newgame")
    scene_digest.clean_digest(state.group_id, old["digest_id"])
    assert db.list_keys("scene_digests") == []


def test_private_resolved_check_never_promotes_model_prose_to_public(state):
    msg = message(state)
    msg.payload["resolved_check_context"] = {"roll": 68, "outcome": "秘密結果", "visibility": "player_private"}
    public, private = turn_delivery.finalize(msg, "你擲出 68，發現秘密結果及其他內幕。")
    assert public == "請查看你的私訊。"
    assert len(private) == 1 and private[0][0] == "u"
    assert "68" in private[0][1] and "秘密結果" in private[0][1]
