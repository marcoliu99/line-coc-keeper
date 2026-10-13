"""A generic reply is classified, logged and retried at most once; it is never a silent success."""
from __future__ import annotations

import asyncio
import functools
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import config, observability
from app.agents import reply_pipeline, supervisor
from app.domain.models import (
    FALLBACK_REASONS,
    AgentMessage,
    MechanicResult,
    ObservedOutcome,
    StateDelta,
    TurnResolution,
)
from app.models import Combatant, CombatState, GroupState
from app.services import prompt_config, turn_delivery, turn_fallback


def aio(test):
    """Run an async test on its own event loop; the suite has no async plugin."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


def _state() -> GroupState:
    return GroupState(group_id="g", timeline_id="timeline-a", active_chapter_id="ch-1")


def _result(disposition: str = "blocked", code: str = "validated", **fields: Any) -> MechanicResult:
    status = fields.pop("check_status", {"tool_called": False, "pending": None})
    return MechanicResult(
        success=fields.pop("success", True), action_type="none", narrative_facts=[], state_delta=StateDelta(),
        check_status=status, turn_resolution=TurnResolution(disposition=disposition, validation_code=code),
        **fields,
    )


def _resolved() -> MechanicResult:
    return _result("no_mechanics", "validated")


@pytest.fixture
def events(monkeypatch):
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(observability, "event", lambda name, **fields: seen.append((name, fields)))
    return seen


def fallbacks(events) -> list[dict[str, Any]]:
    return [fields for name, fields in events if name == "turn.fallback"]


async def _turn(state, executor_results, *, narration="敘事", rag_status="success", search=("", "empty"),
                narrator=None, text="我走進廚房"):
    message = AgentMessage(payload={
        "conversation_id": "g", "user_id": "u1", "display_name": "P1", "text": text,
        "resolved_location": {"name": "大廳"}, "speaker_role": "player", "state": state,
        "character": None, "rag_context": "--- 第 3 頁 ---\n大廳", "memory_context": "", "rag_status": rag_status,
    })
    run_executor = AsyncMock(side_effect=executor_results)
    searched = MagicMock(side_effect=search) if isinstance(search, Exception) else MagicMock(return_value=search)
    with patch.object(supervisor.context_builder, "build_context", AsyncMock(return_value=message)), \
            patch.object(supervisor.turn_commit, "ensure_turn_timeline", return_value="timeline-a"), \
            patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
            patch.object(supervisor.executor, "run_executor", run_executor), \
            patch.object(supervisor.context_builder, "search_scenario_context", searched), \
            patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
            patch.object(supervisor.narrator, "run_narrator", narrator or AsyncMock(return_value=(narration, [], []))), \
            patch.object(reply_pipeline.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _m, t: t)), \
            patch.object(supervisor.turn_commit, "commit_turn_result", return_value=True):
        reply, _, _ = await supervisor.run_turn(
            state=state, user_id="u1", display_name="P1", text=text, resolved_location={"name": "大廳"},
            speaker_role="player", conversation_id="g",
        )
    return reply, run_executor, searched, message


# --- the reasons ---------------------------------------------------------------------------

def test_every_reason_is_stable_enumerable_and_has_a_player_message() -> None:
    assert set(FALLBACK_REASONS) == {
        "no_scenario_evidence", "executor_no_action", "unresolved_pending_state", "invalid_tool_plan",
        "tool_failure", "tool_result_rejected", "narration_failure", "state_conflict",
        "unsupported_action", "safety_block", "internal_error", "unknown",
    }
    assert all(turn_fallback.guidance(reason) for reason in FALLBACK_REASONS)
    assert len({turn_fallback.guidance(reason) for reason in FALLBACK_REASONS}) == len(FALLBACK_REASONS)


@pytest.mark.parametrize(("code", "expected"), [
    ("invalid_json", "invalid_tool_plan"), ("invalid_object", "invalid_tool_plan"),
    ("completion_too_long", "invalid_tool_plan"), ("invalid_fields", "invalid_tool_plan"),
    ("invalid_actor_or_disposition", "invalid_tool_plan"), ("invalid_evidence_format", "invalid_tool_plan"),
    ("invalid_evidence_reference", "invalid_tool_plan"), ("missing_actor", "invalid_tool_plan"),
    ("pending_identity_mismatch", "invalid_tool_plan"),  # with nothing run or pending; see the conflict cases below
    ("cancellation_not_verified", "state_conflict"),
    ("luck_takes_precedence", "unresolved_pending_state"), ("unfinished_check_or_luck", "unresolved_pending_state"),
    ("deferral_not_verified", "executor_no_action"),
    ("inventory_or_combat_not_verified", "tool_result_rejected"), ("missing_resolved_effect", "tool_result_rejected"),
    ("no_mechanics_has_effects", "tool_result_rejected"),
    ("missing_scenario_or_mutation_evidence", "no_scenario_evidence"),
    ("model_incomplete", "executor_no_action"),
])
def test_each_validation_code_the_executor_can_fail_with_has_a_reason(code: str, expected: str) -> None:
    assert turn_fallback.classify(_result("incomplete", code), _state(), "u1") == expected


def test_a_handoff_naming_a_check_nobody_opened_after_only_look_ups_is_a_decision_to_retry() -> None:
    """Lightless Beacon 2026-10-13 rerun4, turn 183: the Executor searched, then named a Spot Hidden it never started;
    the player was told 「時間線已更新」."""
    looked = _result("incomplete", "pending_identity_mismatch", tool_calls=(("search_scenario", True),))
    assert turn_fallback.classify(looked, _state(), "u1") == "invalid_tool_plan"
    assert turn_fallback.classify(_result("incomplete", "pending_identity_mismatch"), _state(), "u1") == "invalid_tool_plan"
    # A tool that could have changed the game, or a check someone is waiting on, is still a conflict.
    acted = _result("incomplete", "pending_identity_mismatch", tool_calls=(("initialize_combat", True),))
    assert turn_fallback.classify(acted, _state(), "u1") == "state_conflict"
    waiting = GroupState(group_id="g", timeline_id="timeline-a", pending_checks={"u2": {"check_id": "c1"}})
    assert turn_fallback.classify(looked, waiting, "u1") == "state_conflict"
    assert turn_fallback.classify(_result("incomplete", "cancellation_not_verified"), _state(), "u1") == "state_conflict"


def test_a_model_that_says_incomplete_after_a_failed_tool_is_a_tool_failure() -> None:
    result = _result("incomplete", "model_incomplete", tool_calls=(("search_scenario", True), ("skill_check", False)))
    assert turn_fallback.classify(result, _state(), "u1") == "tool_failure"


def test_a_search_the_allowance_refused_is_not_a_tool_failure() -> None:
    calls = (("search_scenario", True), ("search_scenario", False))
    refused = _result("incomplete", "model_incomplete", tool_calls=calls,
                      check_status={"tool_called": True, "pending": None, "scenario_searches_refused": 1})
    assert turn_fallback.classify(refused, _state(), "u1") == "executor_no_action"
    # A search that failed for another reason, or any other tool that failed, still is one.
    assert turn_fallback.classify(_result("incomplete", "model_incomplete", tool_calls=calls), _state(), "u1") == "tool_failure"
    # An earlier search that failed for its own reason is not excused by a later refusal.
    own = _result("incomplete", "model_incomplete", tool_calls=(("search_scenario", False),) + calls,
                  check_status={"tool_called": True, "pending": None, "scenario_searches_refused": 1})
    assert turn_fallback.classify(own, _state(), "u1") == "tool_failure"
    mixed = _result("incomplete", "model_incomplete", tool_calls=calls + (("skill_check", False),),
                    check_status={"tool_called": True, "pending": None, "scenario_searches_refused": 1})
    assert turn_fallback.classify(mixed, _state(), "u1") == "tool_failure"


def test_an_executor_that_crashed_is_an_internal_error_or_a_tool_failure() -> None:
    crashed = _result("incomplete", "model_incomplete", success=False, execution_health="failed")
    assert turn_fallback.classify(crashed, _state(), "u1") == "internal_error"
    crashed.tool_calls = (("skill_check", False),)
    assert turn_fallback.classify(crashed, _state(), "u1") == "tool_failure"


def test_scenario_evidence_the_gateway_refused_is_a_missing_grounding() -> None:
    result = _result("incomplete", "model_incomplete", check_status={"scenario_evidence_blocked": True})
    assert turn_fallback.classify(result, _state(), "u1") == "no_scenario_evidence"


def test_a_validated_block_is_told_apart_by_what_the_turn_had() -> None:
    state = _state()
    assert turn_fallback.classify(_result("blocked"), state, "u1", rag_status="success") == "unsupported_action"
    assert turn_fallback.classify(_result("blocked"), state, "u1", rag_status="empty") == "no_scenario_evidence"
    searched = _result("blocked", tool_calls=(("search_scenario", True),))
    assert turn_fallback.classify(searched, state, "u1", rag_status="empty") == "unsupported_action"
    state.pending_checks["u1"] = {"check_id": "c1"}
    assert turn_fallback.classify(_result("blocked"), state, "u1", rag_status="success") == "unresolved_pending_state"


def test_a_turn_that_is_not_a_fallback_has_no_reason() -> None:
    for disposition in ("no_mechanics", "resolved", "resolved_without_check", "await_check", "cancelled"):
        assert turn_fallback.classify(_result(disposition), _state(), "u1") is None
    assert turn_fallback.classify(None, _state(), "u1") is None
    assert turn_fallback.classify(_result("deferred"), _state(), "u1") == "unresolved_pending_state"


def test_the_generic_reply_names_what_to_do_for_its_reason() -> None:
    result = _result("blocked", fallback_reason="no_scenario_evidence")
    reply = prompt_config.enforce_mechanic_check_consistency("narration", result)
    assert turn_fallback.guidance("no_scenario_evidence") in reply and "無法繼續" in reply
    assert "請先確認目前狀態或更正原本的行動" not in reply


# --- recovery ------------------------------------------------------------------------------

@aio
async def test_a_blocked_movement_gets_one_targeted_search_and_one_retry() -> None:
    state = _state()
    reply, run_executor, searched, message = await _turn(
        state, [_result("blocked"), _resolved()], rag_status="empty",
        search=("--- 第 4 頁 ---\n廚房的門通往走廊", "success"), narration="你推開廚房的門。",
    )
    assert reply == "你推開廚房的門。"
    assert run_executor.await_count == 2 and searched.call_count == 1
    _, _args, kwargs = searched.mock_calls[0]
    assert kwargs["accept_lexical"] is True and kwargs["label"] == "supervisor.recovery_retrieval"
    assert "我走進廚房" in searched.call_args.args[3] and "大廳" in searched.call_args.args[3]
    assert message.payload["recovery_context"].startswith("--- 第 4 頁 ---")


@aio
async def test_a_valid_inquiry_that_stays_unresolved_ends_in_a_specific_reply_and_a_logged_reason(events) -> None:
    state = _state()
    reply, run_executor, _searched, _ = await _turn(
        state, [_result("incomplete", "model_incomplete"), _result("incomplete", "model_incomplete")],
        text="我仔細觀察大廳",
    )
    assert run_executor.await_count == 2  # one retry, never more
    assert turn_fallback.guidance("executor_no_action") in reply
    [row] = fallbacks(events)
    assert row["fallback_reason"] == "executor_no_action"
    assert (row["recovery_attempted"], row["recovery_result"]) == (True, "unresolved")


@aio
async def test_the_recovery_is_capped_at_one_search_and_one_retry(events) -> None:
    for results in ([_result("blocked")] * 2, [_result("incomplete", "invalid_json")] * 2):
        _, run_executor, searched, _ = await _turn(_state(), results, rag_status="empty")
        assert run_executor.await_count == 2 and searched.call_count <= 1


@aio
async def test_a_recovered_turn_logs_the_reason_it_recovered_from(events) -> None:
    await _turn(_state(), [_result("blocked"), _resolved()], rag_status="empty", search=("找到的內容", "success"))
    [row] = fallbacks(events)
    assert (row["fallback_reason"], row["recovery_attempted"], row["recovery_result"]) == ("no_scenario_evidence", True, "recovered")


@aio
async def test_a_pending_continuation_blocks_with_an_explicit_reason_and_no_retry(events) -> None:
    state = _state()
    state.pending_checks["u1"] = {"check_id": "c1", "skill": "偵查", "investigator": "P1"}
    reply, run_executor, searched, _ = await _turn(state, [_result("blocked")])
    assert run_executor.await_count == 1 and searched.call_count == 0
    assert "擲 P1 的偵查" in reply and "/coc check" in reply  # the wait itself is named, deterministically
    assert fallbacks(events)[0]["fallback_reason"] == "unresolved_pending_state"
    assert fallbacks(events)[0]["pending_own"] == ["check"]


@pytest.mark.parametrize("touched", [
    {"check_status": {"state_changed": True}}, {"check_status": {"dice_rolled": True}},
    {"check_status": {"resolved": {"roll": 5}}}, {"events": [object()]},
])
@aio
async def test_a_turn_that_changed_the_game_is_never_run_again(touched) -> None:
    _, run_executor, searched, _ = await _turn(_state(), [_result("incomplete", "model_incomplete", **touched)])
    assert run_executor.await_count == 1 and searched.call_count == 0


def _outcome(tool: str, number: int = 1) -> ObservedOutcome:
    return ObservedOutcome(f"tool:{number}", tool, True, "", "internal")


@aio
async def test_a_turn_that_only_looked_things_up_is_run_again(events) -> None:
    """``search_scenario`` leaves an observed outcome of its own; that must not make the turn unrecoverable."""
    lookups = _result("incomplete", "model_incomplete", observed_outcomes=[_outcome("search_scenario"), _outcome("search_scenario", 2)],
                      tool_calls=(("search_scenario", True), ("search_scenario", True)))
    _, run_executor, _, _ = await _turn(_state(), [lookups, _resolved()])
    assert run_executor.await_count == 2
    [row] = fallbacks(events)
    assert (row["fallback_reason"], row["recovery_attempted"], row["recovery_result"]) == ("executor_no_action", True, "recovered")


@pytest.mark.parametrize("tool", ["record_clue", "add_carried_item", "remove_carried_item", "skill_check", "adjust_character"])
@aio
async def test_a_turn_whose_tool_changed_anything_is_never_run_again(tool) -> None:
    result = _result("incomplete", "model_incomplete", observed_outcomes=[_outcome("search_scenario"), _outcome(tool, 2)],
                     tool_calls=(("search_scenario", True), (tool, True)))
    _, run_executor, searched, _ = await _turn(_state(), [result])
    assert run_executor.await_count == 1 and searched.call_count == 0


@aio
async def test_a_tool_failure_is_not_retried_and_is_named() -> None:
    result = _result("incomplete", "model_incomplete", tool_calls=(("skill_check", False),))
    reply, run_executor, _, _ = await _turn(_state(), [result])
    assert run_executor.await_count == 1 and turn_fallback.guidance("tool_failure") in reply


@aio
async def test_recovery_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setattr(config, "TURN_FALLBACK_RECOVERY_ENABLED", False)
    _, run_executor, searched, _ = await _turn(_state(), [_result("blocked")], rag_status="empty")
    assert run_executor.await_count == 1 and searched.call_count == 0


@aio
async def test_a_failing_recovery_search_still_lets_the_executor_decide_once() -> None:
    _, run_executor, searched, message = await _turn(
        _state(), [_result("blocked"), _resolved()], rag_status="empty", search=RuntimeError("index unavailable"),
    )
    assert run_executor.await_count == 2 and searched.call_count == 1
    assert "recovery_context" not in message.payload


# --- every generic reply is logged with a reason -------------------------------------------

@aio
async def test_every_logged_fallback_carries_the_turns_evidence(events) -> None:
    await _turn(_state(), [_result("incomplete", "model_incomplete", tool_calls=(("search_scenario", True),)),
                           _result("incomplete", "model_incomplete", tool_calls=(("search_scenario", True),))])
    [row] = fallbacks(events)
    assert row["fallback_reason"] in FALLBACK_REASONS
    for key in ("campaign_id", "timeline_id", "player_id", "turn_id", "scene", "chapter_id", "pending_own",
                "retrieval_count", "retrieval_hit_ids", "executor_decision", "tool_calls",
                "recovery_attempted", "recovery_result"):
        assert key in row, key
    assert row["timeline_id"] == "timeline-a" and row["scene"] == "大廳"
    assert row["retrieval_count"] == 1 and row["retrieval_hit_ids"] == ["page:3"]
    assert row["tool_calls"] == ["search_scenario"] and row["executor_decision"] == "incomplete"


@aio
async def test_a_failed_narration_is_logged_as_one(events) -> None:
    async def failing_narrator(message):
        message.payload["narration_failed"] = True
        return turn_fallback.guidance("narration_failure"), [], []

    await _turn(_state(), [_resolved()], narrator=failing_narrator)
    assert [row["fallback_reason"] for row in fallbacks(events)] == ["narration_failure"]


@aio
async def test_a_refused_commit_is_logged_as_a_state_conflict(events) -> None:
    state = _state()
    state.game_started = True
    with patch.object(supervisor.turn_commit, "commit_turn_result", return_value=False):
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "P1", "text": "x", "resolved_location": None,
            "speaker_role": "player", "state": state, "character": None, "rag_context": "", "memory_context": "",
        })
        with patch.object(supervisor.context_builder, "build_context", AsyncMock(return_value=message)), \
                patch.object(supervisor.turn_commit, "ensure_turn_timeline", return_value="timeline-a"), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
                patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=_resolved())), \
                patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
                patch.object(supervisor.narrator, "run_narrator", AsyncMock(return_value=("敘事", [], []))), \
                patch.object(reply_pipeline.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _m, t: t)):
            await supervisor.run_turn(state=state, user_id="u1", display_name="P1", text="x", resolved_location=None,
                                      speaker_role="player", conversation_id="g")
    assert [row["fallback_reason"] for row in fallbacks(events)] == ["state_conflict"]


@aio
async def test_a_reply_replaced_for_safety_is_logged_as_a_safety_block(events) -> None:
    from app import spoiler_policy

    await _turn(_state(), [_resolved()], narration=spoiler_policy.NEUTRAL_FALLBACK_TEXT)
    assert [row["fallback_reason"] for row in fallbacks(events)] == ["safety_block"]


@aio
async def test_an_ordinary_turn_logs_no_fallback(events) -> None:
    reply, run_executor, searched, _ = await _turn(_state(), [_resolved()])
    assert reply == "敘事" and fallbacks(events) == [] and run_executor.await_count == 1 and searched.call_count == 0


def test_every_generic_reply_site_in_the_supervisor_records_its_reason() -> None:
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app/agents/supervisor.py").read_text(encoding="utf-8"))
    recorded = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "record"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "turn_fallback"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert {"unresolved_pending_state", "narration_failure", "safety_block", "state_conflict"} <= recorded
    assert recorded <= set(FALLBACK_REASONS)


@aio
async def test_recovery_does_not_search_when_retrieval_was_switched_off() -> None:
    """"disabled" means the whole scenario (or the combat state) is already in the prompt; a search would bypass that setting."""
    state = _state()
    state.scenario_text = "完整劇本"
    _, run_executor, searched, _ = await _turn(state, [_result("blocked"), _resolved()], rag_status="disabled")
    assert run_executor.await_count == 2 and searched.call_count == 0


def test_a_disabled_retrieval_with_a_loaded_scenario_is_not_missing_evidence() -> None:
    state = _state()
    state.scenario_text = "完整劇本"
    assert turn_fallback.classify(_result("blocked"), state, "u1", rag_status="disabled") == "unsupported_action"
    assert turn_fallback.classify(_result("blocked"), _state(), "u1", rag_status="disabled") == "no_scenario_evidence"


@aio
async def test_a_retry_that_stays_blocked_is_not_blamed_on_evidence_the_recovery_found(events) -> None:
    reply, run_executor, _searched, message = await _turn(
        _state(), [_result("blocked"), _result("blocked")], rag_status="empty",
        search=("--- 第 4 頁 ---\n廚房的門鎖著", "success"),
    )
    assert run_executor.await_count == 2 and message.payload["recovery_context"]
    [row] = fallbacks(events)
    assert row["fallback_reason"] == "unsupported_action" and row["recovery_result"] == "unresolved"
    assert turn_fallback.guidance("no_scenario_evidence") not in reply


@aio
async def test_the_fallback_event_counts_the_hits_the_recovery_found(events) -> None:
    await _turn(_state(), [_result("blocked"), _result("blocked")], rag_status="empty",
                search=("--- 第 4 頁 ---\n廚房的門鎖著", "success"))
    [row] = fallbacks(events)
    assert row["retrieval_count"] == 2 and row["retrieval_hit_ids"] == ["page:3", "page:4"]


# --- a request that failed before running any tool -------------------------------------------------------------

def _crashed(**fields: Any) -> MechanicResult:
    return _result("incomplete", "model_incomplete", success=False, execution_health="failed", **fields)


@aio
async def test_a_request_that_failed_before_any_tool_is_run_once_more(events) -> None:
    reply, run_executor, _, _ = await _turn(_state(), [_crashed(), _resolved()])
    assert run_executor.await_count == 2 and turn_fallback.guidance("internal_error") not in reply
    [row] = fallbacks(events)
    assert (row["fallback_reason"], row["recovery_attempted"], row["recovery_result"]) == ("internal_error", True, "recovered")


@aio
async def test_a_failed_retry_is_not_repeated(events) -> None:
    reply, run_executor, _, _ = await _turn(_state(), [_crashed(), _crashed()])
    assert run_executor.await_count == 2 and turn_fallback.guidance("internal_error") in reply


@pytest.mark.parametrize("touched", [
    {"tool_calls": (("skill_check", True),)},
    {"observed_outcomes": [ObservedOutcome("e1", "add_carried_item", True)]},
    {"execution_health": "recovery_required"},
    {"check_status": {"state_changed": True}},
    {"events": [object()]},
])
@aio
async def test_a_failure_after_a_tool_that_changed_something_is_never_run_again(touched) -> None:
    fields = {"execution_health": "failed", **touched}
    result = _result("incomplete", "model_incomplete", success=False, **fields)
    _, run_executor, _, _ = await _turn(_state(), [result])
    assert run_executor.await_count == 1


@aio
async def test_a_failure_after_only_look_ups_is_run_once_more(events) -> None:
    # 2026-10-12 soak: get_enemy_stat_block ran, then the model's decision was invalid twice (「系統發生內部錯誤」).
    looked = _result("incomplete", "model_incomplete", success=False, execution_health="partial",
                     tool_calls=(("get_enemy_stat_block", True),),
                     observed_outcomes=[ObservedOutcome("e1", "get_enemy_stat_block", True)])
    reply, run_executor, _, _ = await _turn(_state(), [looked, _resolved()])
    assert run_executor.await_count == 2 and turn_fallback.guidance("internal_error") not in reply
    [row] = fallbacks(events)
    assert (row["recovery_attempted"], row["recovery_result"]) == (True, "recovered")


@aio
async def test_no_retry_when_the_turn_deadline_leaves_too_little_time(monkeypatch) -> None:
    monkeypatch.setattr(config, "TURN_RETRY_MIN_REMAINING_SECONDS", 10_000.0)
    reply, run_executor, _, _ = await _turn(_state(), [_crashed()])
    assert run_executor.await_count == 1 and turn_fallback.guidance("internal_error") in reply


@aio
async def test_the_failure_retry_follows_the_recovery_switch(monkeypatch) -> None:
    monkeypatch.setattr(config, "TURN_FALLBACK_RECOVERY_ENABLED", False)
    _, run_executor, _, _ = await _turn(_state(), [_crashed()])
    assert run_executor.await_count == 1


@aio
async def test_the_retry_time_is_checked_again_after_the_recovery_search(monkeypatch) -> None:
    answers = iter([True, False])  # enough time before the search, not after it
    monkeypatch.setattr(supervisor, "_time_for_retry", lambda: next(answers))
    _, run_executor, searched, _ = await _turn(
        _state(), [_crashed()], rag_status="empty", search=("找到的內容", "success"))
    assert searched.call_count == 1 and run_executor.await_count == 1


def test_the_same_combat_notice_is_shown_once_even_when_only_some_calls_carry_the_provisional_mark() -> None:
    notice = "戰鬥機制操作已記錄；後續以目前戰鬥狀態為準。"
    outcomes = [
        ObservedOutcome("tool:1", "advance_combat_turn", True, notice, "public"),
        ObservedOutcome("tool:2", "advance_combat_turn", True, notice + turn_delivery.PROVISIONAL_MARK, "public"),
        ObservedOutcome("tool:3", "advance_combat_turn", True, notice, "public"),
    ]
    result = _result("blocked", "validated", observed_outcomes=outcomes, check_status={"tool_called": True, "pending": None})
    text = prompt_config.enforce_mechanic_check_consistency("", result)
    assert text.count(notice) == 1 and turn_delivery.PROVISIONAL_MARK in text
    assert turn_delivery.distinct_lines(["a", "b", "a"]) == ["a", "b"]


def test_a_refused_turn_in_a_battle_says_whose_turn_it_is_instead_of_talking_about_the_scenario() -> None:
    state = _state()
    state.scenario_npc_index = [{"name": "鼠群", "aliases": []}]
    state.combat = CombatState(
        active=True, round_number=2, current_index=1, phase="READY",
        order=[Combatant(name="鼠群", side="enemy", hp=5, hp_max=5), Combatant(name="小雨", side="pc", is_pc=True, hp=10, hp_max=10)],
    )
    result = _result("blocked", fallback_reason="unsupported_action", scenario_evidence=PASSAGE)
    reply = prompt_config.enforce_mechanic_check_consistency("narration", result, state=state)
    assert "現在輪到「小雨」行動" in reply and "劇本" not in reply and "鼠群" not in reply
    state.combat.phase = "SETTLEMENT"
    assert "等守密人結算" in prompt_config.enforce_mechanic_check_consistency("narration", result, state=state)
    state.combat.phase = "READY"
    state.combat.order[0].defeated = True
    assert "敵方已全數倒下" in prompt_config.enforce_mechanic_check_consistency("narration", result, state=state)
    state.combat.active = False
    assert prompt_config.enforce_mechanic_check_consistency("narration", result, state=state) == "narration"  # told in the story
    assert "劇本" in prompt_config.enforce_mechanic_check_consistency(
        "narration", _result("blocked", fallback_reason="no_scenario_evidence"), state=state)
    assert turn_fallback.combat_guidance(state, "tool_failure") == ""


def test_a_battle_that_is_paused_or_whose_actor_has_acted_does_not_ask_for_a_declaration() -> None:
    state = _state()
    state.combat = CombatState(
        active=True, round_number=2, current_index=0, phase="READY",
        order=[Combatant(name="小雨", side="pc", is_pc=True, combatant_id="pc:1", hp=10, hp_max=10),
               Combatant(name="鼠群", side="enemy", hp=5, hp_max=5)],
    )
    asking = turn_fallback.combat_guidance(state, "unsupported_action")
    assert "請說明要對哪個目標" in asking
    state.combat.actions = {"a1": {"actor_id": "pc:1", "completed": True, "round": 2}}
    assert "已經行動完畢" in turn_fallback.combat_guidance(state, "unsupported_action")
    state.combat.actions = {"a2": {"actor_id": "pc:1", "completed": False, "round": 2}}
    assert "尚未完成的檢定或選擇" in turn_fallback.combat_guidance(state, "unsupported_action")
    state.combat.actions = {}
    state.combat.phase = "PLAYER_ROLL"
    assert "尚未完成的檢定或選擇" in turn_fallback.combat_guidance(state, "unsupported_action")
    state.combat.phase = "NEEDS_RULING"
    assert "等守密人裁定" in turn_fallback.combat_guidance(state, "unsupported_action")


def test_a_luck_wait_names_the_luck_control_and_an_evidence_hold_keeps_its_own_wording() -> None:
    state = _state()
    state.combat = CombatState(
        active=True, round_number=2, current_index=0, phase="LUCK_DECISION",
        order=[Combatant(name="小雨", side="pc", is_pc=True, combatant_id="pc:1", hp=10, hp_max=10),
               Combatant(name="鼠群", side="enemy", hp=5, hp_max=5)],
    )
    assert "/coc luck" in turn_fallback.combat_guidance(state, "unsupported_action")
    held = _result("blocked", fallback_reason="no_scenario_evidence",
                   check_status={"tool_called": False, "pending": None, "scenario_evidence_blocked": True})
    reply = prompt_config.enforce_mechanic_check_consistency("narration", held, state=state)
    assert "劇本依據" in reply and "/coc luck" not in reply


def test_a_defeated_current_actor_is_waiting_for_the_keeper_to_advance() -> None:
    state = _state()
    state.combat = CombatState(
        active=True, round_number=2, current_index=0, phase="READY",
        order=[Combatant(name="小雨", side="pc", is_pc=True, combatant_id="pc:1", hp=0, hp_max=10, defeated=True),
               Combatant(name="鼠群", side="enemy", hp=5, hp_max=5)],
    )
    text = turn_fallback.combat_guidance(state, "unsupported_action")
    assert "已經倒下" in text and "請說明要對哪個目標" not in text


def test_a_deferral_with_nobody_waiting_is_no_action_even_after_a_look_up() -> None:
    """Camp Sunny 2026-10-11 rerun3 turn 45: one search_scenario, then 「等警長離開再…」 answered as deferred; the player was
    told to finish a check that did not exist. A look-up is not a reason to call it a pending state."""
    looked = _result("incomplete", "deferral_not_verified", tool_calls=(("search_scenario", True),))
    assert turn_fallback.classify(looked, _state(), "u1") == "executor_no_action"
    wrote = _result("incomplete", "deferral_not_verified", tool_calls=(("add_carried_item", True),))
    assert turn_fallback.classify(wrote, _state(), "u1") == "unresolved_pending_state"
    rolled = _result("incomplete", "deferral_not_verified", tool_calls=(("roll_dice", True),))
    assert turn_fallback.classify(rolled, _state(), "u1") == "unresolved_pending_state", "a die rolled is not a look-up"


def test_the_generic_roll_line_is_not_repeated_after_a_reply_that_gives_the_command() -> None:
    ref = turn_delivery.InteractionRef("check", "p1", "check-1", "t")
    assert ref.told_in("請按檢定按鈕或輸入 /coc check，擲 Mark 的偵查 檢定。")
    assert not ref.told_in("你俯身細看泥地上的痕跡。")
    assert not ref.told_in("想回到上個存檔點，用 /coc checkpoint。")  # another command, not the roll
    assert not ref.told_in("輸入 /coc check偵查 擲骰。")  # not a command the router reads
    assert ref.told_in("請輸入 /coc check。") and ref.told_in("輸入 /coc check")


# --- an action the scenario cannot do is explained in the story ---------------------------------------------------

PASSAGE = ("--- 原稿補查 · 第 4 頁 ---\n穆斯科比沒有圖書館；最近的在二十英里外的 Appleford。",)


def _cannot_be_done(**status: Any) -> MechanicResult:
    return _result("blocked", "validated", fallback_reason="unsupported_action", scenario_evidence=PASSAGE,
                   check_status={"tool_called": True, "pending": None, **status})


def test_an_action_the_scenario_cannot_do_is_answered_by_the_narrator_not_the_fixed_refusal() -> None:
    # Scritch-Scratch rerun3: 「去村裡的圖書館」 (the village has none) was explained four times, then refused in fixed text.
    result = _cannot_be_done()
    assert prompt_config.explains_in_story(result) and not prompt_config.narration_replaced(result)
    story = "穆斯科比沒有圖書館；最近的在二十英里外的 Appleford。"
    assert prompt_config.enforce_mechanic_check_consistency(story, result) == story
    assert "【這個行動在劇本裡無法進行】" in prompt_config.build_mechanic_facts_block(result)
    assert "目前無法繼續" in prompt_config.enforce_mechanic_check_consistency("", result)  # no narration: the fixed text stays


@pytest.mark.parametrize("status", [
    {"dice_rolled": True}, {"resolved": True}, {"state_changed": True}, {"combat_opened": True},
    {"pending": {"investigator": "小雨", "skill": "偵查"}}, {"pending_luck": {"investigator": "小雨"}},
    {"scenario_evidence_blocked": True},
])
def test_a_blocked_turn_that_did_anything_keeps_the_fixed_text(status: dict[str, Any]) -> None:
    result = _cannot_be_done(**status)
    assert not prompt_config.explains_in_story(result)


def test_other_reasons_and_a_running_battle_keep_the_fixed_text() -> None:
    assert not prompt_config.explains_in_story(_result("blocked", "validated", fallback_reason="no_scenario_evidence"))
    assert not prompt_config.explains_in_story(_result("incomplete", "model_incomplete", fallback_reason="unsupported_action"))
    state = _state()
    state.combat = CombatState(active=True, round_number=1, current_index=0, phase="READY",
                               order=[Combatant(name="小雨", side="pc", is_pc=True, hp=10, hp_max=10)])
    assert not prompt_config.explains_in_story(_cannot_be_done(), state) and prompt_config.narration_replaced(_cannot_be_done(), state)


@aio
async def test_what_the_first_attempt_found_reaches_the_narrator_when_the_retry_found_nothing() -> None:
    locked = "--- 第 4 頁 ---\n門是鎖著的，要開鎖或破門。"
    first = _result("blocked", "validated", scenario_evidence=(locked,),
                    check_status={"tool_called": True, "pending": None})
    retry = _result("blocked", "validated", check_status={"tool_called": False, "pending": None})
    _, run_executor, _, message = await _turn(_state(), [first, retry])
    assert run_executor.await_count == 2
    assert message.payload["first_attempt_evidence"] == locked


@aio
async def test_a_retry_that_ran_dry_does_not_replace_the_first_attempts_validated_answer(events) -> None:
    """Scritch-Scratch 2026-10-13 rerun3, turn 22: the first attempt worked out that the village has no library; the
    retry spent the rest of the turn's searches and ended 「劇本裡沒有足夠的內容」."""
    first = _cannot_be_done()
    dry = _result("incomplete", "model_incomplete", tool_calls=(("search_scenario", False),),
                  check_status={"tool_called": True, "pending": None, "scenario_searches_refused": 1})
    reply, run_executor, _, _ = await _turn(_state(), [first, dry], narration="穆斯科比沒有圖書館；最近的在 Appleford。")
    assert run_executor.await_count == 2
    assert reply == "穆斯科比沒有圖書館；最近的在 Appleford。"
    [row] = fallbacks(events)
    assert row["fallback_reason"] == "unsupported_action" and row["recovery_result"] == "unresolved"


@aio
async def test_a_retry_that_changed_the_game_is_kept_over_the_first_attempts_answer() -> None:
    """Codex #304: the first attempt's answer is only kept over a retry that, like it, only looked things up."""
    first = _cannot_be_done()
    for status in ({"dice_rolled": True}, {"state_changed": True}, {"resolved": True}):
        acted = _result("incomplete", "model_incomplete", fallback_reason="executor_no_action",
                        check_status={"tool_called": True, "pending": None, **status})
        reply, *_ = await _turn(_state(), [first, acted], narration="穆斯科比沒有圖書館。")
        assert reply != "穆斯科比沒有圖書館。", status


@aio
async def test_a_retry_that_deferred_to_someone_else_is_kept_over_the_first_attempts_answer() -> None:
    """Codex #304: only a retry that ran dry (incomplete) gives way; a deferral names whom the turn is waiting for."""
    deferred = _result("deferred", "validated", check_status={"tool_called": False, "pending": None})
    reply, *_ = await _turn(_state(), [_cannot_be_done(), deferred], narration="穆斯科比沒有圖書館。")
    assert reply != "穆斯科比沒有圖書館。"


@aio
async def test_evidence_the_retry_found_again_is_not_handed_over_twice() -> None:
    found = ("--- 第 4 頁 ---\n門是鎖著的，要開鎖或破門。",)
    first = _result("blocked", "validated", scenario_evidence=found)
    retry = _result("blocked", "validated", scenario_evidence=found)
    _, _, _, message = await _turn(_state(), [first, retry])
    assert "first_attempt_evidence" not in message.payload


def test_what_the_first_attempt_found_counts_as_evidence_for_the_status_and_the_obligations() -> None:
    from app.agents import supervisor
    bare = AgentMessage(payload={"rag_status": "empty", "rag_context": ""})
    assert supervisor._evidence_status(bare) == "empty" and supervisor._evidence_text(bare) == ""
    carried = AgentMessage(payload={"rag_status": "empty", "rag_context": "", "first_attempt_evidence": "門是鎖著的。"})
    assert supervisor._evidence_status(carried) == "success" and "門是鎖著的。" in supervisor._evidence_text(carried)


def test_an_empty_search_is_not_evidence_that_an_action_cannot_be_done() -> None:
    # scenario_rag answers an empty search with 「（沒有找到相關內容）」, a successful call with a non-empty result.
    empty = _result("blocked", "validated", fallback_reason="unsupported_action",
                    scenario_evidence=("（沒有找到相關內容）",), check_status={"tool_called": True, "pending": None})
    assert not prompt_config.explains_in_story(empty) and turn_fallback.found_scenario_text(_cannot_be_done())
    assert not turn_fallback.found_scenario_text(empty)
    assert "目前無法繼續" in prompt_config.enforce_mechanic_check_consistency("故事", empty)


@aio
async def test_the_first_attempts_passage_counts_for_the_retry_that_found_nothing() -> None:
    first = _result("blocked", "validated", scenario_evidence=PASSAGE, check_status={"tool_called": True, "pending": None})
    retry = _result("blocked", "validated", fallback_reason="unsupported_action",
                    check_status={"tool_called": False, "pending": None})
    await _turn(_state(), [first, retry])
    assert PASSAGE[0] in retry.scenario_evidence


@aio
async def test_the_passage_the_recovery_search_found_counts_for_a_retry_that_searched_nothing() -> None:
    first = _result("blocked", "validated", check_status={"tool_called": False, "pending": None})
    retry = _result("blocked", "validated", fallback_reason="unsupported_action",
                    check_status={"tool_called": False, "pending": None})
    await _turn(_state(), [first, retry], rag_status="empty", search=("--- 第 4 頁 ---\n門是鎖著的。", "success"))
    assert turn_fallback.found_scenario_text(retry) and prompt_config.explains_in_story(retry)


@aio
async def test_what_the_narrator_suggests_instead_of_an_action_the_scenario_cannot_do_owes_nothing() -> None:
    # The passage may attach a Sanity loss or damage to the alternative; suggesting it is not it happening.
    from unittest.mock import patch
    gate = MagicMock(return_value=True)
    with patch.object(supervisor.obligation_gate, "possible", gate):
        await _turn(_state(), [_cannot_be_done(), _cannot_be_done()], narration="去山洞入口看看吧。")
        assert not gate.called
        gate.reset_mock()
        await _turn(_state(), [_result("resolved", "validated", scenario_evidence=PASSAGE)], narration="好。")
        assert gate.called  # any other turn is still asked


@aio
async def test_the_passage_retrieval_already_gave_counts_when_the_executor_did_not_search_again() -> None:
    # The Executor is told to reuse the proactive passage instead of calling search_scenario.
    first = _result("blocked", "validated", check_status={"tool_called": False, "pending": None})
    last = _result("blocked", "validated", check_status={"tool_called": False, "pending": None})
    await _turn(_state(), [first, last], rag_status="success")
    assert turn_fallback.found_scenario_text(last) and prompt_config.explains_in_story(last)
    nothing = _result("blocked", "validated", check_status={"tool_called": False, "pending": None})
    await _turn(_state(), [nothing, nothing], rag_status="empty")
    assert not turn_fallback.found_scenario_text(nothing)  # no retrieved passage, nothing to explain from


@aio
async def test_an_empty_search_of_the_first_attempt_is_not_carried_as_evidence() -> None:
    first = _result("blocked", "validated", scenario_evidence=("（沒有找到相關內容）",),
                    check_status={"tool_called": True, "pending": None})
    retry = _result("blocked", "validated", fallback_reason="unsupported_action",
                    check_status={"tool_called": False, "pending": None})
    _, _, _, message = await _turn(_state(), [first, retry], rag_status="empty")
    assert "first_attempt_evidence" not in message.payload and not turn_fallback.found_scenario_text(retry)


@aio
async def test_a_narrator_that_failed_does_not_stand_in_for_the_explanation() -> None:
    # run_narrator answers a failed request with a notice that says submitted changes are kept; this turn changed nothing.
    async def failing(message):
        message.payload["narration_failed"] = True
        return "守密人暫時無法完成敘事。已提交的變更會保留。", [], []

    reply, *_ = await _turn(_state(), [_cannot_be_done(), _cannot_be_done()], narrator=failing)
    assert "已提交的變更會保留" not in reply and "目前無法繼續" in reply


def test_a_fight_that_began_is_not_followed_by_a_request_to_start_over() -> None:
    # Haunting rerun3 turn 28: initialize_combat succeeded, the handoff named a check that did not exist.
    story = "戰鬥已然開始，Felix，你先行動。"
    began = _result("incomplete", "pending_identity_mismatch", fallback_reason="state_conflict",
                    check_status={"tool_called": True, "pending": None, "combat_opened": True})
    assert prompt_config.fight_started_cleanly(began)
    assert prompt_config.enforce_mechanic_check_consistency(story, began) == story
    for status in ({"pending": {"investigator": "小雨", "skill": "偵查"}}, {"dice_rolled": True}, {"resolved": True}):
        other = _result("incomplete", "pending_identity_mismatch", fallback_reason="state_conflict",
                        check_status={"tool_called": True, "pending": None, "combat_opened": True, **status})
        assert not prompt_config.fight_started_cleanly(other)
    unrelated = _result("incomplete", "model_incomplete", fallback_reason="executor_no_action",
                        check_status={"tool_called": True, "pending": None, "combat_opened": True})
    assert not prompt_config.fight_started_cleanly(unrelated)
    failed = _result("incomplete", "pending_identity_mismatch", fallback_reason="state_conflict",
                     check_status={"tool_called": True, "pending": None, "combat_opened": True},
                     tool_calls=[("initialize_combat", True), ("record_item", False)])
    assert not prompt_config.fight_started_cleanly(failed)  # another requested change failed
    waiting = GroupState("g", pending_checks={"c1": {"check_id": "c1", "visibility": "private"}})
    assert not prompt_config.fight_started_cleanly(began, waiting)  # a private check the public result hides
    assert prompt_config.fight_started_cleanly(began, GroupState("g"))
    # A cancellation nobody verified is a state conflict too, but the table still needs to hear about it.
    cancelled = _result("incomplete", "cancellation_not_verified", fallback_reason="state_conflict",
                        check_status={"tool_called": True, "pending": None, "combat_opened": True})
    assert not prompt_config.fight_started_cleanly(cancelled)
    assert "尚未完整處理" in prompt_config.enforce_mechanic_check_consistency(story, cancelled)
    assert "尚未完整處理" in prompt_config.enforce_mechanic_check_consistency(story, unrelated)


def _acted(**fields: Any) -> MechanicResult:
    """An Executor that rolled and changed something, then left the turn incomplete (Scritch-Scratch turn 21)."""
    status = {"tool_called": True, "pending": None, "dice_rolled": True, "state_changed": True, **fields.pop("check_status", {})}
    fields.setdefault("fallback_reason", "executor_no_action")
    fields.setdefault("observed_outcomes", [ObservedOutcome("tool:2", "skill_check", True, "已結算檢定：小雨 的偵查，骰值 13，結果為「極難成功」。", "public")])
    return _result("incomplete", "model_incomplete", check_status=status,
                   tool_calls=fields.pop("tool_calls", (("search_scenario", True), ("skill_check", True), ("sanity_check", True))),
                   **fields)


def test_an_incomplete_turn_after_committed_results_is_told_in_the_story() -> None:
    result = _acted()
    assert prompt_config.narrates_what_was_done(result, _state())
    assert not prompt_config.narration_replaced(result, _state())
    story = "林間的鼠屍掛滿枝頭，你們屏住了呼吸。"
    assert prompt_config.enforce_mechanic_check_consistency(story, result, state=_state()) == story
    assert "【本回合已完成的部分" in prompt_config.build_mechanic_facts_block(result)
    # Narration that failed leaves the fixed notice, not an empty reply.
    assert "尚未完整處理" in prompt_config.enforce_mechanic_check_consistency("", result, state=_state())


def test_a_search_the_allowance_refused_does_not_keep_a_turn_from_being_narrated() -> None:
    refused = _acted(tool_calls=(("skill_check", True), ("search_scenario", False)),
                     check_status={"scenario_searches_refused": 1})
    assert prompt_config.narrates_what_was_done(refused, _state())
    own = _acted(tool_calls=(("skill_check", True), ("search_scenario", False)))
    assert not prompt_config.narrates_what_was_done(own, _state())


def test_a_refused_search_is_not_among_the_facts_the_narrator_is_given() -> None:
    result = _acted(tool_calls=(("skill_check", True), ("search_scenario", False)),
                    check_status={"scenario_searches_refused": 1})
    result.narrative_facts = ["skill_check 成功：骰值 13", "search_scenario 失敗：scenario_search_limit_reached"]
    block = prompt_config.build_mechanic_facts_block(result)
    assert "骰值 13" in block and "scenario_search_limit_reached" not in block


def test_a_turn_that_only_kept_a_kp_only_note_has_nothing_to_narrate() -> None:
    quiet = _acted(observed_outcomes=[ObservedOutcome("tool:1", "record_clue", True, "", "internal")])
    assert not prompt_config.narrates_what_was_done(quiet, _state())
    assert prompt_config.narrates_what_was_done(_acted(), _state())


def test_a_batched_sanity_witness_that_did_not_roll_keeps_the_notice() -> None:
    partial = _acted(check_status={"batched_call_failed": True})
    assert not prompt_config.narrates_what_was_done(partial, _state())
    assert turn_fallback.failed_a_tool(partial)


def test_only_a_clean_incomplete_turn_that_did_something_is_narrated() -> None:
    state = _state()
    assert not prompt_config.narrates_what_was_done(_acted(check_status={"dice_rolled": False, "state_changed": False}), state)
    assert not prompt_config.narrates_what_was_done(_acted(tool_calls=(("skill_check", True), ("record_clue", False))), state)
    assert not prompt_config.narrates_what_was_done(_acted(check_status={"pending": {"investigator": "小雨", "skill": "偵查"}}), state)
    assert not prompt_config.narrates_what_was_done(_acted(check_status={"combat_opened": True}), state)
    assert not prompt_config.narrates_what_was_done(_acted(fallback_reason="tool_failure"), state)
    assert not prompt_config.narrates_what_was_done(_result("blocked", "validated"), state)
    waiting = _state()
    waiting.pending_checks["u2"] = {"check_id": "c1"}
    assert not prompt_config.narrates_what_was_done(_acted(), waiting)
    fighting = _state()
    fighting.combat.active = True
    assert not prompt_config.narrates_what_was_done(_acted(), fighting)


@aio
async def test_a_turn_that_rolled_and_gave_up_reads_as_the_narration() -> None:
    reply, run_executor, _, _ = await _turn(_state(), [_acted()], narration="枝頭的鼠屍在風裡輕輕搖晃，你們一時說不出話。")
    assert "枝頭的鼠屍" in reply and "尚未完整處理" not in reply and run_executor.await_count == 1


@aio
async def test_a_failed_narration_after_a_result_gets_the_fixed_notice_not_the_failure_text() -> None:
    async def failing(message):
        message.payload["narration_failed"] = True
        return "守密人暫時無法完成敘事。已提交的變更會保留；請查看目前狀態。", [], []

    reply, _, _, _ = await _turn(_state(), [_acted()], narrator=failing)
    assert "尚未完整處理" in reply and "暫時無法完成敘事" not in reply


@aio
async def test_a_turn_told_from_its_results_still_goes_through_the_obligation_gate() -> None:
    """The gate records what the Executor applied (its receipts stop a later turn charging it twice)."""
    gate = AsyncMock(return_value=[])
    with patch.object(supervisor.obligation_gate, "possible", return_value=True), \
            patch.object(reply_pipeline.obligation_gate, "enforce", gate):
        reply, _, _, _ = await _turn(_state(), [_acted()], narration="枝頭的鼠屍在風裡輕輕搖晃。")
    assert "枝頭的鼠屍" in reply and gate.await_count == 1


def test_a_check_the_gate_opens_after_the_story_does_not_turn_it_back_into_a_notice() -> None:
    story = "林間的鼠屍掛滿枝頭，你們屏住了呼吸。"
    opened = _acted(check_status={"pending": {"investigator": "小雨", "skill": "理智", "check_id": "c9"}})
    # Decided before the gate: the story is kept and the new check's instruction rides with it.
    kept = prompt_config.enforce_mechanic_check_consistency(story, opened, state=_state(), narrated=True)
    assert story in kept and "尚未完整處理" not in kept and "/coc check" in kept
    # Without that decision the live pending check makes the turn ineligible, as before.
    assert "尚未完整處理" in prompt_config.enforce_mechanic_check_consistency(story, opened, state=_state())
    # Narration that failed still gets the notice.
    assert "尚未完整處理" in prompt_config.enforce_mechanic_check_consistency("", _acted(), state=_state(), narrated=True)
