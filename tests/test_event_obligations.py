"""What a scenario attaches to an event is owed in the turn the event is narrated, once, and only if it said so."""
from __future__ import annotations

import asyncio
import functools
from unittest.mock import AsyncMock, patch

import pytest

from app import db, dice
from app.agents import obligation_gate, reply_pipeline, supervisor
from app.domain.models import AgentMessage, MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.repositories import group_state
from app.services import event_obligations as eo

BUCKET_RULE = "紅色水桶裡有一隻斷手。當調查員發現水桶中的斷手時，須進行 SAN 檢定（1/1D4）。"
TRAP_RULE = "若有人觸碰祭壇上的黑色石塊，會受到 1D6 點傷害。"


def aio(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


# --- reading the rule ----------------------------------------------------------------------

@pytest.mark.parametrize(("text", "success", "failure"), [
    ("看見斷手：SAN 1/1D4。", "1", "1d4"),
    ("任何看見怪物的人需做 SAN 檢定 1D3/1D10。", "1d3", "1d10"),
    ("The investigator who sees the corpse loses Sanity 0/1D3.", "0", "1d3"),
    ("理智檢定 0 / 1d6+1 才能直視它。", "0", "1d6+1"),
])
def test_an_explicit_san_rule_is_read_in_either_language(text: str, success: str, failure: str) -> None:
    [rule] = eo.extract(text)
    assert (rule.kind, rule.arg("loss_success"), rule.arg("loss_failure")) == ("sanity_check", success, failure)


def test_horror_without_a_stated_rule_owes_nothing() -> None:
    for text in ("房間陰森恐怖，令人毛骨悚然。", "The corridor is cold and silent, and something seems wrong.",
                 "血跡一路延伸到門後。", "他的眼神讓人不寒而慄。"):
        assert eo.extract(text) == []


def test_damage_needs_a_trigger_and_is_not_a_combat_attack() -> None:
    [trap] = eo.extract(TRAP_RULE)
    assert (trap.kind, trap.arg("expression")) == ("damage", "1d6")
    assert eo.extract("The creature attacks and takes 1D6 damage.") == []
    assert eo.extract("食屍鬼的攻擊：爪擊造成 1D6 點傷害。") == []
    assert eo.extract("受到 1D6 點傷害。") == []  # no trigger: not a rule about this event


def test_a_forced_check_names_its_skill_and_difficulty() -> None:
    [rule] = eo.extract("走進地下室的人須通過困難 聆聽 檢定，否則聽不見腳步聲。")
    assert (rule.kind, rule.arg("skill"), rule.arg("difficulty")) == ("forced_check", "聆聽", "hard")


def test_an_obligation_is_found_once_with_the_reference_clutter_removed() -> None:
    evidence = f"--- 第 12 頁 ---\n{BUCKET_RULE}\n\n--- 第 12 頁 ---\n{BUCKET_RULE}\n【取用完整性】[{{\"x\": \"SAN 9/9D9\"}}]"
    assert len(eo.extract(evidence)) == 1


# --- deciding that the event was shown ------------------------------------------------------

def test_the_event_is_shown_only_when_what_the_rule_names_is_narrated() -> None:
    [rule] = eo.extract(BUCKET_RULE)
    assert eo.triggered(rule, "你掀開紅色水桶的蓋子，裡面泡著一隻斷手，指節仍在滴水。")
    assert not eo.triggered(rule, "你看了看牆邊那個紅色水桶，桶裡只有清水。")
    assert not eo.triggered(rule, "你在水桶旁邊蹲下，聞到一股消毒水的味道。")
    assert not eo.triggered(rule, "走廊盡頭傳來一陣腳步聲，令人不安。")


def test_a_rule_that_names_one_thing_needs_that_thing_to_be_narrated() -> None:
    [rule] = eo.extract("看見屍體：SAN 0/1D3。")
    assert eo.triggered(rule, "你推開門，一具屍體倒在地上。")
    assert not eo.triggered(rule, "你推開門，房間裡很安靜。")


def test_identity_is_per_investigator_unless_the_rule_repeats() -> None:
    [once] = eo.extract(BUCKET_RULE)
    [every] = eo.extract("每次有人觸碰祭壇上的黑色石塊，就會受到 1D6 點傷害。")
    assert eo.identity("t", "c1", once, "turn-1") == eo.identity("t", "c1", once, "turn-2")
    assert eo.identity("t", "c1", once, "turn-1") != eo.identity("t", "c2", once, "turn-1")
    assert eo.identity("t", "c1", every, "turn-1") != eo.identity("t", "c1", every, "turn-2")
    assert eo.identity("t", "c1", every, "turn-1") == eo.identity("t", "c1", every, "turn-1")


# --- the gate ------------------------------------------------------------------------------

@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    s = GroupState(group_id="obligations", timeline_id="timeline-a", game_started=True)
    s.characters["a"] = Character(name="Marco", owner_id="a", san=60, san_max=99, hp=11, hp_max=11)
    s.characters["b"] = Character(name="Ken", owner_id="b", san=55, san_max=99)
    group_state.save_state(s)
    return s


def _result() -> MechanicResult:
    return MechanicResult(success=True, action_type="none", narrative_facts=[], state_delta=StateDelta())


async def _enforce(state, narration, evidence, *, turn_id="turn-1", result=None, user_id="a"):
    result = result or _result()
    applied = await obligation_gate.enforce(
        state, user_id, narration, evidence, result, turn_id=turn_id, speaker_role="player",
        private_messages=[], image_requests=[], observed_outcomes=[],
    )
    return applied, result


REVEAL = "你掀開紅色水桶的蓋子，裡面泡著一隻斷手。"


@aio
async def test_a_revealed_event_creates_the_san_check_in_the_same_turn(state) -> None:
    applied, result = await _enforce(state, REVEAL, [BUCKET_RULE])
    assert [item.kind for item in applied] == ["sanity_check"] and applied[0].pending
    stored = group_state.load_state(state.group_id)
    pending = stored.pending_checks["a"]
    assert (pending["type"], pending["loss_success"], pending["loss_failure"]) == ("sanity", "1", "1d4")
    assert result.check_status["pending"]["investigator"] == "Marco"
    assert "b" not in stored.pending_checks  # the acting investigator only


@aio
async def test_with_autoroll_the_san_check_is_settled_in_the_turn_and_reported(state) -> None:
    state.autoroll_checks = True
    group_state.save_state(state)
    with patch("app.dice.roll_percentile_with_dice_pool", return_value=95):
        applied, _ = await _enforce(state, REVEAL, [BUCKET_RULE])
    [item] = applied
    assert not item.pending and "理智檢定已結算" in item.summary and "SAN 60 →" in item.summary
    stored = group_state.load_state(state.group_id)
    assert not stored.pending_checks and stored.characters["a"].san < 60


@aio
async def test_atmospheric_horror_creates_no_san_check(state) -> None:
    applied, _ = await _enforce(state, "房間陰森恐怖，你渾身發毛，一隻斷手的影子在牆上晃動。", ["這間房間很冷。"])
    assert applied == [] and not group_state.load_state(state.group_id).pending_checks


@aio
async def test_a_rule_whose_event_is_not_narrated_creates_nothing(state) -> None:
    applied, _ = await _enforce(state, "你在大廳裡四處張望，沒有發現什麼異狀。", [BUCKET_RULE])
    assert applied == [] and not group_state.load_state(state.group_id).pending_checks


@aio
async def test_replaying_the_same_event_does_not_owe_it_twice(state) -> None:
    first, _ = await _enforce(state, REVEAL, [BUCKET_RULE])
    again, _ = await _enforce(state, REVEAL, [BUCKET_RULE], turn_id="turn-2")
    assert len(first) == 1 and again == []
    stored = group_state.load_state(state.group_id)
    assert len(stored.pending_checks) == 1
    stored.pending_checks.clear()  # the player rolled; a later mention of the same hand costs nothing more
    group_state.save_state(stored)
    later, _ = await _enforce(stored, REVEAL, [BUCKET_RULE], turn_id="turn-3")
    assert later == []


@aio
async def test_the_other_investigator_who_sees_it_owes_their_own_check(state) -> None:
    await _enforce(state, REVEAL, [BUCKET_RULE])
    state = group_state.load_state(state.group_id)
    applied, _ = await _enforce(state, REVEAL, [BUCKET_RULE], user_id="b", turn_id="turn-2")
    assert len(applied) == 1 and set(group_state.load_state(state.group_id).pending_checks) == {"a", "b"}


@aio
async def test_a_check_that_cannot_be_registered_now_is_released_not_lost(state) -> None:
    state.pending_checks["a"] = {"type": "skill", "skill": "偵查", "check_id": "c-old", "timeline_id": "timeline-a",
                                  "investigator": "Marco", "skill_value": 50}
    group_state.save_state(state)
    applied, _ = await _enforce(state, REVEAL, [BUCKET_RULE])
    assert applied == []
    stored = group_state.load_state(state.group_id)
    assert stored.pending_checks["a"]["check_id"] == "c-old" and not stored.check_consequence_receipts
    stored.pending_checks.clear()
    group_state.save_state(stored)
    retry, _ = await _enforce(stored, REVEAL, [BUCKET_RULE], turn_id="turn-2")
    assert len(retry) == 1


@aio
async def test_an_event_that_costs_hit_points_goes_through_the_same_mechanism(state) -> None:
    narration = "你伸手碰了祭壇上的黑色石塊，一陣灼痛竄上手臂。"
    with patch.object(dice, "roll_expression", return_value=dice.RollResult(expression="1d6", rolls=[4], modifier=0, total=4)):
        applied, _ = await _enforce(state, narration, [TRAP_RULE])
        again, _ = await _enforce(group_state.load_state(state.group_id), narration, [TRAP_RULE], turn_id="turn-2")
    assert [item.kind for item in applied] == ["damage"] and not applied[0].pending and again == []
    stored = group_state.load_state(state.group_id)
    assert stored.characters["a"].hp == 7 and "4" in applied[0].summary


@aio
async def test_a_required_skill_check_is_created(state) -> None:
    applied, _ = await _enforce(state, "你走進地下室，腳下的木板吱嘎作響。", ["走進地下室的人須通過 聆聽 檢定，否則聽不見腳步聲。"])
    assert [item.kind for item in applied] == ["forced_check"]
    assert group_state.load_state(state.group_id).pending_checks["a"]["skill"] == "聆聽"


@aio
async def test_a_turn_without_an_investigator_applies_nothing(state) -> None:
    applied, _ = await _enforce(state, REVEAL, [BUCKET_RULE], user_id="nobody")
    assert applied == []


# --- through the supervisor ------------------------------------------------------------------

async def _turn(state, narration, rag_context):
    message = AgentMessage(payload={
        "conversation_id": state.group_id, "user_id": "a", "display_name": "Marco", "text": "我掀開水桶",
        "resolved_location": None, "speaker_role": "player", "state": state, "character": None,
        "rag_context": rag_context, "memory_context": "", "rag_status": "success",
    })
    executed = MechanicResult(
        success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
        check_status={"tool_called": False, "pending": None},
        turn_resolution=TurnResolution(disposition="no_mechanics", validation_code="validated"),
    )
    with patch.object(supervisor.context_builder, "build_context", AsyncMock(return_value=message)), \
            patch.object(supervisor.turn_commit, "ensure_turn_timeline", return_value="timeline-a"), \
            patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
            patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=executed)), \
            patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
            patch.object(supervisor.narrator, "run_narrator", AsyncMock(return_value=(narration, [], []))), \
            patch.object(reply_pipeline.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _m, t: t)):
        reply, _, _ = await supervisor.run_turn(
            state=state, user_id="a", display_name="Marco", text="我掀開水桶", resolved_location=None,
            speaker_role="player", conversation_id=state.group_id,
        )
    return reply


@aio
async def test_the_reveal_and_its_san_check_arrive_in_one_reply(state) -> None:
    reply = await _turn(state, REVEAL, f"--- 第 12 頁 ---\n{BUCKET_RULE}")
    assert "斷手" in reply and "理智檢定" in reply and "/coc check" in reply
    assert "a" in group_state.load_state(state.group_id).pending_checks


@aio
async def test_a_turn_without_a_rule_leaves_the_reply_alone(state) -> None:
    reply = await _turn(state, "你掀開水桶，裡面是一堆生鏽的釘子。", f"--- 第 12 頁 ---\n{BUCKET_RULE}")
    assert "理智檢定" not in reply and not group_state.load_state(state.group_id).pending_checks


def test_no_runtime_module_other_than_the_gate_applies_an_obligation() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    users = {
        str(path.relative_to(root)) for path in root.rglob("*.py")
        if any(isinstance(node, ast.Attribute) and node.attr in {"extract", "triggered"}
               and isinstance(node.value, ast.Name) and node.value.id == "event_obligations"
               for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert users == {"agents/obligation_gate.py"}


# --- review: incomplete evidence, what survives the Guard, the mutation phase, cancellation ---------------

INCOMPLETE = (
    f"--- 第 12 頁 ---\n{BUCKET_RULE}\n【依據尚未完整】必要依據未齊；請續取、補查或聚焦行動，暫緩機制。\n"
    '【取用完整性】[{"complete_for_action": false, "root_record_ids": ["r1"]}]'
)


@aio
async def test_a_rule_read_from_evidence_marked_incomplete_is_not_applied(state) -> None:
    applied, _ = await _enforce(state, REVEAL, [INCOMPLETE])
    assert applied == [] and not group_state.load_state(state.group_id).pending_checks
    complete = f"--- 第 12 頁 ---\n{BUCKET_RULE}"
    applied, _ = await _enforce(state, REVEAL, [INCOMPLETE, complete])
    assert len(applied) == 1  # the complete passage still counts


@aio
async def test_nothing_is_applied_when_the_executor_was_told_its_evidence_is_incomplete(state) -> None:
    blocked = _result()
    blocked.check_status["scenario_evidence_blocked"] = True
    applied, _ = await _enforce(state, REVEAL, [BUCKET_RULE], result=blocked)
    assert applied == [] and not obligation_gate.possible([BUCKET_RULE], blocked)
    assert obligation_gate.possible([BUCKET_RULE], _result()) and not obligation_gate.possible(["沒有規則。"], _result())


@aio
async def test_a_trigger_the_guard_removed_charges_nothing(state) -> None:
    message = AgentMessage(payload={
        "conversation_id": state.group_id, "user_id": "a", "display_name": "Marco", "text": "我掀開水桶",
        "resolved_location": None, "speaker_role": "player", "state": state, "character": None,
        "rag_context": f"--- 第 12 頁 ---\n{BUCKET_RULE}", "memory_context": "", "rag_status": "success",
    })
    executed = MechanicResult(success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
                              turn_resolution=TurnResolution(disposition="no_mechanics", validation_code="validated"))
    with patch.object(supervisor.context_builder, "build_context", AsyncMock(return_value=message)), \
            patch.object(supervisor.turn_commit, "ensure_turn_timeline", return_value="timeline-a"), \
            patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
            patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=executed)), \
            patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
            patch.object(supervisor.narrator, "run_narrator", AsyncMock(return_value=(REVEAL, [], []))), \
            patch.object(reply_pipeline.guard, "enforce_narrative_safety", AsyncMock(return_value="你掀開蓋子，什麼也沒看清。")):
        reply, _, _ = await supervisor.run_turn(
            state=state, user_id="a", display_name="Marco", text="我掀開水桶", resolved_location=None,
            speaker_role="player", conversation_id=state.group_id)
    assert "斷手" not in reply and "理智檢定" not in reply
    assert not group_state.load_state(state.group_id).pending_checks


@pytest.mark.parametrize(("evidence", "hands_off"), [(f"--- 第 12 頁 ---\n{BUCKET_RULE}", False), ("沒有任何機制規則。", True)])
@aio
async def test_the_mutation_phase_is_kept_when_the_evidence_states_an_obligation(state, monkeypatch, evidence, hands_off) -> None:
    from app import config

    monkeypatch.setattr(config, "NARRATION_OUTSIDE_MUTATION_LOCK", True)
    handoff = AsyncMock()
    message = AgentMessage(payload={
        "conversation_id": state.group_id, "user_id": "a", "display_name": "Marco", "text": "我掀開水桶",
        "resolved_location": None, "speaker_role": "player", "state": state, "character": None,
        "rag_context": evidence, "memory_context": "", "rag_status": "success",
    })
    executed = MechanicResult(success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
                              turn_resolution=TurnResolution(disposition="no_mechanics", validation_code="validated"))
    with patch.object(supervisor.context_builder, "build_context", AsyncMock(return_value=message)), \
            patch.object(supervisor.turn_commit, "ensure_turn_timeline", return_value="timeline-a"), \
            patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
            patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=executed)), \
            patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
            patch.object(supervisor.narrator, "run_narrator", AsyncMock(return_value=("你掀開水桶。", [], []))), \
            patch.object(reply_pipeline.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _m, t: t)):
        await supervisor.run_turn(
            state=state, user_id="a", display_name="Marco", text="我掀開水桶", resolved_location=None,
            speaker_role="player", conversation_id=state.group_id, handoff=handoff)
    assert (handoff.to_narration.await_count == 1) is hands_off


@aio
async def test_a_cancelled_application_is_never_applied_a_second_time(state) -> None:
    cancelled = AsyncMock(side_effect=asyncio.CancelledError())
    with patch.object(obligation_gate, "make_tool_executor", return_value=cancelled), pytest.raises(asyncio.CancelledError):
        await _enforce(state, REVEAL, [BUCKET_RULE])
    [receipt] = group_state.load_state(state.group_id).check_consequence_receipts.values()
    assert receipt["result"]["status"] == "executing"  # the tool may have run, so it is never run again
    applied, _ = await _enforce(state, REVEAL, [BUCKET_RULE], turn_id="turn-2")
    assert applied == []


@aio
async def test_an_application_cannot_be_cancelled_between_reserving_and_starting(state) -> None:
    """Reserving and marking happen without awaiting, so no cancellation point leaves a reservation with no tool run."""
    import inspect

    source = inspect.getsource(obligation_gate.enforce)
    reserve, mark, execute = source.index("_reserve(state"), source.index("_executing(state"), source.index("await execute(")
    assert "await" not in source[reserve:mark] and reserve < mark < execute
