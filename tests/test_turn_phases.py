"""Where a turn's time goes is measured without double counting, and what a turn gathered is reused only while it holds."""
from __future__ import annotations

import asyncio
import functools
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import config, observability
from app.agents import context_builder, supervisor, tool_gateway
from app.domain.models import AgentMessage, MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.services import turn_phases as phases


def aio(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


@pytest.fixture
def events(monkeypatch):
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(observability, "event", lambda name, **fields: seen.append((name, fields)))
    return seen


def named(events, name):
    return [fields for event_name, fields in events if event_name == name]


# --- accounting ----------------------------------------------------------------------------

def line(*intervals: tuple[str, float, float], origin: float = 0.0) -> phases.Timeline:
    timeline = phases.Timeline("turn", "t1", "u1", "g1", origin)
    for name, start, end in intervals:
        timeline.add(name, start, end)
    return timeline


def test_a_tool_inside_the_executor_call_is_not_counted_twice() -> None:
    summary = line(("executor_llm", 0, 10), ("tool_execution", 2, 5)).summary(10)
    assert summary["total_ms"] == {"tool_execution": 3000.0, "executor_llm": 10000.0}
    assert summary["exclusive_ms"] == {"executor_llm": 7000.0, "tool_execution": 3000.0}
    assert summary["overlap_ms"] == 3000.0 and summary["wall_ms"] == 10000.0


def test_exclusive_time_adds_up_to_the_wall_clock_with_the_gaps_called_other() -> None:
    summary = line(("queue_wait", 0, 4), ("executor_llm", 5, 8), ("narrator_llm", 9, 10)).summary(12)
    assert summary["exclusive_ms"] == {"queue_wait": 4000.0, "executor_llm": 3000.0, "narrator_llm": 1000.0, "other": 4000.0}
    assert sum(summary["exclusive_ms"].values()) == summary["wall_ms"] == 12000.0 and summary["overlap_ms"] == 0.0


def test_a_search_that_ran_while_the_turn_queued_is_credited_to_the_search() -> None:
    summary = line(("queue_wait", 0, 6), ("initial_retrieval", 1, 3)).summary(6)
    assert summary["exclusive_ms"] == {"queue_wait": 4000.0, "initial_retrieval": 2000.0}


def test_overlapping_spans_of_one_phase_are_merged() -> None:
    summary = line(("embedding", 0, 3), ("embedding", 2, 5), ("embedding", 7, 8)).summary(8)
    assert summary["total_ms"] == {"embedding": 6000.0} and summary["exclusive_ms"]["embedding"] == 6000.0


def test_a_span_that_began_before_the_turn_is_clipped_to_it() -> None:
    summary = line(("initial_retrieval", -3, 2), origin=0).summary(4)
    assert summary["total_ms"] == {"initial_retrieval": 2000.0}


def test_an_unknown_phase_is_refused() -> None:
    with pytest.raises(ValueError):
        line().add("coffee", 0, 1)
    with pytest.raises(ValueError):
        line().add("other", 0, 1)


def test_every_named_phase_can_win_an_overlap_or_is_other() -> None:
    assert set(phases._PRIORITY) | {"other"} == set(phases.PHASES)


# --- recording -----------------------------------------------------------------------------

def test_recording_outside_a_timeline_costs_nothing_and_fails_nothing() -> None:
    with phases.phase("executor_llm"):
        pass
    phases.seed("queue_wait", 0, 1)
    assert phases.current() is None


def test_a_timeline_reports_each_span_and_one_summary(events) -> None:
    with phases.timeline("turn", turn_id="t1", player_id="u1", campaign_id="g1") as timeline:
        with phases.phase("executor_llm"):
            time.sleep(0.01)
        assert phases.current() is timeline
    assert phases.current() is None
    [span] = named(events, "turn.phase")
    assert span["phase"] == "executor_llm" and span["duration_ms"] >= 5 and {"turn_id", "player_id", "campaign_id"} <= set(span)
    [summary] = named(events, "turn.phases")
    assert summary["exclusive_ms"]["executor_llm"] >= 5 and summary["turn_id"] == "t1" and summary["kind"] == "turn"


@aio
async def test_work_in_a_worker_thread_lands_on_the_turn_that_started_it(events) -> None:
    def search() -> None:
        with phases.phase("memory_search"):
            time.sleep(0.005)

    with phases.timeline("turn", turn_id="t2", player_id="u1", campaign_id="g1"):
        await asyncio.gather(asyncio.to_thread(search), asyncio.to_thread(search))
    assert named(events, "turn.phases")[0]["total_ms"].get("memory_search", 0) > 0
    assert len(named(events, "turn.phase")) == 2


def test_threads_recording_at_once_lose_nothing() -> None:
    timeline = line()
    workers = [threading.Thread(target=lambda: [timeline.add("embedding", i, i + 0.5) for i in range(50)]) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(timeline.intervals) == 200


def test_a_failure_to_report_never_fails_the_turn(monkeypatch) -> None:
    monkeypatch.setattr(observability, "event", MagicMock(side_effect=RuntimeError("sink down")))
    with phases.timeline("turn", turn_id="t3", player_id="u1", campaign_id="g1"):
        pass


@aio
async def test_the_decorator_seeds_the_queue_wait_and_the_prefetch(events) -> None:
    @phases.timed_turn
    async def run_turn(state, user_id, *, turn_kind="player_action", prefetched_retrieval=None, handoff=None):
        with phases.phase("narrator_llm"):
            await asyncio.sleep(0.005)
        return observability.current_context().get("turn_id")

    now = time.monotonic()
    prefetch = SimpleNamespace(started_at=now - 0.2, finished_at=now - 0.1)
    turn_id = await run_turn(SimpleNamespace(group_id="g1"), "u1", prefetched_retrieval=prefetch,
                             handoff=SimpleNamespace(queue_wait_ms=300.0))
    [summary] = named(events, "turn.phases")
    assert summary["turn_id"] == turn_id and summary["total_ms"]["queue_wait"] >= 290
    assert summary["exclusive_ms"]["initial_retrieval"] >= 90 and summary["exclusive_ms"]["queue_wait"] >= 190
    assert summary["campaign_id"] and summary["player_id"]


@aio
async def test_a_continuation_is_labelled_and_timed_as_one(events) -> None:
    @phases.timed_turn
    async def run_turn(state, user_id, *, turn_kind="player_action"):
        await asyncio.sleep(0.01)

    await run_turn(SimpleNamespace(group_id="g1"), "u1", turn_kind="resolved_check_followup")
    await run_turn(SimpleNamespace(group_id="g1"), "u1")
    continuation, action = named(events, "turn.phases")
    assert continuation["kind"] == "continuation" and continuation["total_ms"]["continuation_processing"] >= 5
    assert action["kind"] == "turn" and "continuation_processing" not in action["total_ms"]


@aio
async def test_the_lock_helper_records_how_long_the_turn_queued() -> None:
    from app.commands import router

    reply = AsyncMock()
    seen = []

    async def waiter() -> None:
        async with router._conversation_lock_with_notice("queue-conv", reply) as handoff:
            seen.append(handoff.queue_wait_ms)

    async with router._conversation_lock_with_notice("queue-conv", reply) as first:
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
    await task
    assert first.queue_wait_ms == 0.0 and seen[0] >= 40


# --- reusing what a turn gathered ------------------------------------------------------------------

def make_state(**changes: Any) -> GroupState:
    state = GroupState(group_id="reuse-g", timeline_id="timeline-a", scenario_text="劇本", scenario_title="T", game_started=True)
    state.characters["a"] = Character(name="Marco", owner_id="a")
    state.characters["b"] = Character(name="Ken", owner_id="b")
    for key, value in changes.items():
        setattr(state, key, value)
    return state


def action_message(rag_status: str = "success") -> AgentMessage:
    return AgentMessage(payload={
        "rag_context": "--- 第 1 頁 ---\n櫃檯", "memory_context": "", "rag_status": rag_status, "memory_status": "empty",
    })


@pytest.fixture(autouse=True)
def fresh_grounding(monkeypatch):
    context_builder._grounding.clear()
    context_builder._grounding_sequence.clear()
    monkeypatch.setattr(context_builder.db, "get_json", lambda *args, **kwargs: [])


def test_the_continuation_of_a_roll_gets_the_evidence_of_its_action() -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    reused = context_builder.reusable_grounding(state, "a")
    assert reused is not None and reused.rag_context.endswith("櫃檯") and reused.rag_status == "success"


def test_a_failed_search_is_never_kept() -> None:
    state = make_state()
    for status in ("empty", "fallback", "error", "timeout", "disabled"):
        context_builder.remember_grounding(state, "a", action_message(status))
    assert context_builder.reusable_grounding(state, "a") is None


def test_it_is_not_reused_once_another_turn_has_searched_in_the_conversation() -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    context_builder.remember_grounding(state, "b", action_message())
    assert context_builder.reusable_grounding(state, "a") is None and context_builder.reusable_grounding(state, "b") is not None


def test_it_is_not_reused_for_another_player_or_conversation_or_timeline() -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    assert context_builder.reusable_grounding(state, "b") is None
    assert context_builder.reusable_grounding(make_state(group_id="other"), "a") is None
    assert context_builder.reusable_grounding(make_state(timeline_id="timeline-b"), "a") is None


@pytest.mark.parametrize("change", [
    {"active_chapter_id": "ch-2"}, {"campaign_summary": "新的摘要"}, {"scenario_text": "另一份劇本"},
    {"context_chapter_ids": ["ch-1", "ch-2"]},
])
def test_it_is_not_reused_when_what_it_depended_on_has_changed(change) -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    for key, value in change.items():
        setattr(state, key, value)
    assert context_builder.reusable_grounding(state, "a") is None


def test_it_is_not_reused_when_combat_began_or_the_memory_changed(monkeypatch) -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    monkeypatch.setattr(context_builder.db, "get_json", lambda *args, **kwargs: [{"text": "新記憶"}])
    assert context_builder.reusable_grounding(state, "a") is None


def test_it_expires(monkeypatch) -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    monkeypatch.setattr(context_builder, "RETRIEVAL_REUSE_TTL_SECONDS", -1.0)
    assert context_builder.reusable_grounding(state, "a") is None


def test_the_store_is_bounded() -> None:
    for number in range(context_builder._GROUNDING_MAX + 20):
        context_builder.remember_grounding(make_state(group_id=f"g{number}"), "a", action_message())
    assert len(context_builder._grounding) == context_builder._GROUNDING_MAX


async def _supervised(state, turn_kind, *, prefetched=None, reuse=True):
    message = AgentMessage(payload={
        "conversation_id": state.group_id, "user_id": "a", "display_name": "Marco", "text": "x", "resolved_location": None,
        "speaker_role": "player", "state": state, "character": None, "rag_context": "舊證據", "memory_context": "",
        "rag_status": "success", "memory_status": "empty",
    })
    build = AsyncMock(return_value=message)
    executed = MechanicResult(success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
                              turn_resolution=TurnResolution(disposition="no_mechanics", validation_code="validated"))
    with patch.object(config, "RETRIEVAL_REUSE_FOR_FOLLOWUPS", reuse), \
            patch.object(supervisor.context_builder, "build_context", build), \
            patch.object(supervisor.turn_commit, "ensure_turn_timeline", return_value="timeline-a"), \
            patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
            patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=executed)), \
            patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
            patch.object(supervisor.narrator, "run_narrator", AsyncMock(return_value=("敘事", [], []))), \
            patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _m, t: t)), \
            patch.object(supervisor.turn_commit, "commit_turn_result", return_value=True):
        kwargs: dict[str, Any] = {"turn_kind": turn_kind}
        if turn_kind == "resolved_check_followup":
            kwargs["resolved_check_context"] = {"check_id": "c1", "owner_id": "a"}
        await supervisor.run_turn(state=state, user_id="a", display_name="Marco", text="x", resolved_location=None,
                                  speaker_role="player", conversation_id=state.group_id,
                                  prefetched_retrieval=prefetched, **kwargs)
    return build


@aio
async def test_an_action_turn_remembers_and_its_continuation_reuses(events) -> None:
    state = make_state()
    await _supervised(state, "player_action")
    build = await _supervised(state, "resolved_check_followup")
    assert build.call_args.kwargs["prefetched"] is not None
    assert named(events, "rag.followup_grounding")[-1]["reused"] is True


@aio
async def test_a_continuation_searches_afresh_when_nothing_can_be_reused(events) -> None:
    build = await _supervised(make_state(), "resolved_check_followup")
    assert build.call_args.kwargs["prefetched"] is None
    assert named(events, "rag.followup_grounding")[-1]["reused"] is False


@aio
async def test_reuse_can_be_switched_off() -> None:
    state = make_state()
    await _supervised(state, "player_action")
    build = await _supervised(state, "resolved_check_followup", reuse=False)
    assert build.call_args.kwargs["prefetched"] is None


@aio
async def test_an_action_turn_never_reuses_an_earlier_turns_evidence() -> None:
    state = make_state()
    await _supervised(state, "player_action")
    build = await _supervised(state, "player_action")
    assert build.call_args.kwargs["prefetched"] is None


# --- bounding the search loop ----------------------------------------------------------------------

@aio
async def test_a_turn_may_search_the_scenario_only_so_many_times(events) -> None:
    state = make_state()
    facts: list[str] = []
    calls = []

    def fake_tool(_state, name, args, *_rest, **_kw):
        calls.append(name)
        return {"ok": True, "results": "內容"}

    with patch.object(tool_gateway.keeper, "_execute_tool", fake_tool):
        execute = tool_gateway.make_tool_executor(state, [], [], "player", facts, scenario_search_limit=2, actor_id="a")
        results = [await execute("search_scenario", {"query": f"q{n}"}) for n in range(4)]
        other = await execute("get_character_sheet", {"investigator": "Marco"})
    assert [r["ok"] for r in results] == [True, True, False, False] and results[2]["error"] == "scenario_search_limit_reached"
    assert calls == ["search_scenario", "search_scenario", "get_character_sheet"] and other["ok"]
    assert [e["attempted"] for e in named(events, "executor.scenario_search.limit_exceeded")] == [3, 4]


@aio
async def test_without_a_limit_searching_is_unbounded() -> None:
    facts: list[str] = []
    with patch.object(tool_gateway.keeper, "_execute_tool", lambda *a, **k: {"ok": True, "results": "x"}):
        execute = tool_gateway.make_tool_executor(make_state(), [], [], "player", facts, actor_id="a")
        assert all([(await execute("search_scenario", {"query": "q"}))["ok"] for _ in range(10)])


def test_the_executor_passes_its_limit_to_the_gateway() -> None:
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/agents/executor.py").read_text(encoding="utf-8")
    assert "scenario_search_limit=config.SCENARIO_SEARCH_MAX_PER_TURN" in source
    assert ast.parse(source)


# --- review: a stale action's evidence, the wait before the timeline, and one allowance per turn ----------

def test_an_unsuccessful_search_drops_the_players_earlier_evidence() -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    assert context_builder.reusable_grounding(state, "a") is not None
    context_builder.remember_grounding(state, "a", action_message("empty"))  # the next action found nothing
    assert context_builder.reusable_grounding(state, "a") is None


def test_an_unsuccessful_search_by_someone_else_also_ends_the_reuse() -> None:
    state = make_state()
    context_builder.remember_grounding(state, "a", action_message())
    context_builder.remember_grounding(state, "b", action_message("fallback"))
    assert context_builder.reusable_grounding(state, "a") is None


@aio
async def test_a_retrieval_that_finished_before_the_turn_reached_the_supervisor_is_counted(events) -> None:
    @phases.timed_turn
    async def run_turn(state, user_id, *, turn_kind="player_action", prefetched_retrieval=None, handoff=None):
        await asyncio.sleep(0.005)

    now = time.monotonic()
    prefetch = SimpleNamespace(started_at=now - 0.4, finished_at=now - 0.1)
    await run_turn(SimpleNamespace(group_id="g1"), "u1", prefetched_retrieval=prefetch, handoff=SimpleNamespace(queue_wait_ms=0.0))
    [summary] = named(events, "turn.phases")
    assert summary["wall_ms"] >= 400 and summary["exclusive_ms"]["initial_retrieval"] >= 290
    assert summary["total_ms"]["initial_retrieval"] >= 290


@aio
async def test_a_retry_and_the_recovery_search_share_the_turns_search_allowance(events) -> None:
    facts: list[str] = []
    with patch.object(tool_gateway.keeper, "_execute_tool", lambda *a, **k: {"ok": True, "results": "x"}), \
            observability.context(turn_id="turn-shared-1"):
        first = tool_gateway.make_tool_executor(make_state(), [], [], "player", facts, scenario_search_limit=3, actor_id="a")
        assert [(await first("search_scenario", {"query": "q"}))["ok"] for _ in range(2)] == [True, True]
        tool_gateway.note_scenario_search()  # the recovery search
        retry = tool_gateway.make_tool_executor(make_state(), [], [], "player", facts, scenario_search_limit=3, actor_id="a")
        assert (await retry("search_scenario", {"query": "q"}))["error"] == "scenario_search_limit_reached"
    with observability.context(turn_id="turn-shared-2"):
        fresh = tool_gateway.make_tool_executor(make_state(), [], [], "player", facts, scenario_search_limit=3, actor_id="a")
        with patch.object(tool_gateway.keeper, "_execute_tool", lambda *a, **k: {"ok": True, "results": "x"}):
            assert (await fresh("search_scenario", {"query": "q"}))["ok"]


def test_the_counter_of_finished_turns_is_bounded() -> None:
    for number in range(tool_gateway._SEARCHES_MAX_TURNS + 30):
        with observability.context(turn_id=f"turn-bound-{number}"):
            tool_gateway.note_scenario_search()
    assert len(tool_gateway._searches) == tool_gateway._SEARCHES_MAX_TURNS
    assert tool_gateway.note_scenario_search() is None  # no turn id: nothing to count against
