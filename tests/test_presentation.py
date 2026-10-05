"""A player reads the table's language: no raw result tiers, no internal ids, and the real party size."""
from __future__ import annotations

import asyncio
import functools
import re
from unittest.mock import AsyncMock, patch

import pytest

from app import config, presentation
from app.agents import narrator, supervisor, tool_gateway
from app.domain.models import (
    AgentMessage,
    MechanicResult,
    StateDelta,
    TurnResolution,
)
from app.models import Character, GroupState
from app.services import prompt_config, turn_delivery

CHECK_ID = "check-" + "a1" * 16
DECISION_ID = "decision-" + "b2" * 16
RAW_TIERS = re.compile(r"(?<![A-Za-z])(?:fumble|fail|regular|hard|extreme|critical)(?![A-Za-z])")


def aio(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


# --- tiers ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "shown"), [
    ("regular 成功", "一般成功"), ("hard 成功", "困難成功"), ("extreme 成功", "極難成功"), ("critical 成功", "大成功"),
    ("fail 失敗", "失敗"), ("fumble 失敗", "大失敗"), ("hard 失敗", "失敗（擲出困難成功）"),
    ("結果：extreme  成功。", "結果：極難成功。"),
])
def test_an_outcome_is_shown_in_the_tables_language(raw: str, shown: str) -> None:
    assert presentation.outcome_label(raw) == shown
    assert presentation.player_text(raw) == shown


def test_a_labelled_difficulty_or_tier_is_mapped_and_prose_is_left_alone() -> None:
    assert presentation.player_text("難度 hard，等級 critical") == "難度 困難，等級 大成功"
    assert presentation.player_text("原始等級：regular；所需等級: extreme") == "原始等級：一般成功；所需等級: 極難成功"
    for prose in ("This is a regular day at the hard drive factory.", "他的硬功夫極難成功不了", "fail-safe 與 hard-coded"):
        assert presentation.player_text(prose) == prose


def test_the_mapping_is_idempotent() -> None:
    once = presentation.player_text(f"結果 hard 成功（check_id={CHECK_ID}）。難度 extreme")
    assert presentation.player_text(once) == once


def test_unknown_values_pass_through() -> None:
    assert presentation.tier_label("mystery") == "mystery" and presentation.difficulty_label("odd") == "odd"


# --- internal ids ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    f"檢定已建立（check_id={CHECK_ID}），請擲骰。", f"檢定已建立 (check_id: {CHECK_ID}) 請擲骰。",
    f"請處理 {CHECK_ID} 這筆檢定。", f"Luck 決定 decision_id={DECISION_ID} 待處理。",
])
def test_an_internal_id_never_reaches_the_player(text: str) -> None:
    shown = presentation.player_text(text)
    assert CHECK_ID not in shown and DECISION_ID not in shown and "check_id" not in shown and "decision_id" not in shown
    assert "（）" not in shown and "()" not in shown


def test_the_surrounding_sentence_survives_the_removal() -> None:
    assert presentation.player_text(f"檢定已建立（check_id={CHECK_ID}），請擲骰。") == "檢定已建立，請擲骰。"


def test_debugging_can_ask_to_see_the_ids(monkeypatch) -> None:
    monkeypatch.setattr(config, "DEBUG_SHOW_INTERNAL_IDS", True)
    assert CHECK_ID in presentation.player_text(f"check_id={CHECK_ID}")
    assert presentation.player_text("hard 成功") == "困難成功"  # tier names are never shown raw


def test_the_facts_handed_to_the_narrator_carry_no_ids() -> None:
    fact = tool_gateway._describe_tool_call("skill_check", {
        "ok": True, "investigator": "Marco", "pending": True, "check_id": CHECK_ID, "timeline_id": "t-1",
        "decision_id": DECISION_ID, "event_id": "e-1", "evidence_ref": "tool:1", "skill": "偵查",
    })
    assert "偵查" in fact and "Marco" in fact
    for hidden in (CHECK_ID, DECISION_ID, "check_id", "timeline_id", "event_id", "evidence_ref"):
        assert hidden not in fact


# --- the places a player is told a result ----------------------------------------------------------

def test_the_public_line_for_a_settled_check_has_no_raw_tier() -> None:
    outcome = turn_delivery.observe_tool("skill_check", {
        "ok": True, "resolved": True, "investigator": "Marco", "roll": 12, "tier": "hard"}, 1)
    assert "困難成功" in outcome.public_text and not RAW_TIERS.search(outcome.public_text)


def test_the_consistency_fallbacks_have_no_raw_tier_or_difficulty() -> None:
    result = {"investigator": "Marco", "skill": "偵查", "roll": 12, "difficulty": "hard", "outcome": "hard 成功"}
    text = prompt_config.enforce_resolved_check_consistency("行動尚未結算", result)
    assert "困難" in text and "困難成功" in text and not RAW_TIERS.search(text)
    block = prompt_config.build_resolved_check_outcome_block({**result, "tier": "hard", "skill_value": 60})
    assert "難度：困難" in block and not RAW_TIERS.search(block)


def test_the_history_the_narrator_sees_has_no_raw_tier() -> None:
    block = prompt_config.build_resolved_check_history_block(
        [{"investigator": "Marco", "skill": "偵查", "roll": 12, "difficulty": "extreme", "outcome": "regular 成功"}],
        {"HP": "11/11"})
    assert not RAW_TIERS.search(block)


def _state(count: int) -> GroupState:
    state = GroupState(group_id="p-g", timeline_id="timeline-a", game_started=True)
    for number in range(count):
        state.characters[f"u{number}"] = Character(name=f"調查員{number}", owner_id=f"u{number}")
    return state


async def _reply(state: GroupState, narration: str, *, private=()) -> tuple[str, list]:
    message = AgentMessage(payload={
        "conversation_id": state.group_id, "user_id": "u0", "display_name": "調查員0", "text": "x",
        "resolved_location": None, "speaker_role": "player", "state": state, "character": None,
        "rag_context": "", "memory_context": "", "rag_status": "empty", "memory_status": "empty",
    })
    executed = MechanicResult(success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
                              turn_resolution=TurnResolution(disposition="no_mechanics", validation_code="validated"))
    with patch.object(supervisor.context_builder, "build_context", AsyncMock(return_value=message)), \
            patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-a"), \
            patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
            patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=executed)), \
            patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
            patch.object(supervisor.narrator, "run_narrator", AsyncMock(return_value=(narration, list(private), []))), \
            patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _m, t: t)), \
            patch.object(supervisor.keeper, "_commit_turn_result", return_value=True):
        reply, private_messages, _ = await supervisor.run_turn(
            state=state, user_id="u0", display_name="調查員0", text="x", resolved_location=None,
            speaker_role="player", conversation_id=state.group_id)
    return reply, private_messages


@aio
async def test_the_final_reply_is_cleaned_whatever_the_model_wrote() -> None:
    reply, private = await _reply(
        _state(2), f"擲出 12，結果 hard 成功（check_id={CHECK_ID}）。", private=[("u0", f"私訊 {CHECK_ID} 難度 hard")])
    assert "困難成功" in reply and CHECK_ID not in reply and not RAW_TIERS.search(reply)
    assert CHECK_ID not in private[0][1] and "難度 困難" in private[0][1]


# --- party size ------------------------------------------------------------------------------

@pytest.mark.parametrize(("narration", "fixed"), [
    ("你們六位調查員走進營地。", "你們五位調查員走進營地。"),
    ("你們一共有 6 名探員。", "你們一共有 5 名探員。"),
    ("隊伍共七位隊員，圍坐在營火旁。", "隊伍共五位隊員，圍坐在營火旁。"),
    ("你們一行十位冒險者上了船。", "你們一行五位冒險者上了船。"),
])
def test_a_party_larger_than_the_real_one_is_corrected(narration: str, fixed: str) -> None:
    assert presentation.enforce_party_size(narration, 5) == fixed


@pytest.mark.parametrize("narration", [
    "你們五位調查員走進營地。", "你們兩位調查員留在原地看守。", "三名探員去查看谷倉。", "營地很安靜。",
    "六個人影在霧中晃動。", "他說這裡有六位老師。",
    "敵方有六名探員。", "營地共有 6 名探員。", "你們看到有六名探員站在門口。", "歷史上曾有七位調查員失蹤。",
    "七位冒險者圍坐在營火旁。",
])
def test_a_true_count_a_subgroup_another_group_and_other_numbers_are_left_alone(narration: str) -> None:
    assert presentation.enforce_party_size(narration, 5) == narration


@aio
async def test_five_active_investigators_are_never_narrated_as_six() -> None:
    reply, _ = await _reply(_state(5), "你們六位調查員站在營地入口。")
    assert "六位" not in reply and "五位調查員" in reply


def test_the_narrator_is_told_who_is_in_the_party() -> None:
    prompt = presentation.party_prompt(["Marco", "Ken", "Ann"])
    assert "共 3 位調查員" in prompt and "Marco、Ken、Ann" in prompt and "不得" in prompt


@aio
async def test_the_narrator_prompt_carries_the_real_party(monkeypatch) -> None:
    state = _state(5)
    message = AgentMessage(payload={
        "conversation_id": state.group_id, "user_id": "u0", "display_name": "調查員0", "text": "x", "speaker_role": "player",
        "state": state, "rag_context": "", "memory_context": "", "intent": "PURE_ROLEPLAY", "turn_kind": "player_action",
    })
    seen: list[str] = []

    async def run_conversation(static, dynamic, *args, **kwargs):
        seen.append(dynamic)
        return "好。"

    session = type("S", (), {
        "provider": type("P", (), {"run_conversation": staticmethod(run_conversation)})(), "model": "m",
        "decision_context": False, "dynamic_tools": False, "history": lambda self, log: [],
        "stage_options": lambda self, stage, **kw: {},
    })()
    with patch.object(narrator.ConversationSession, "current", return_value=session), \
            patch.object(narrator.keeper, "_build_static_prompt", return_value=""), \
            patch.object(narrator.keeper, "_build_dynamic_prompt", return_value=""), \
            patch.object(narrator.keeper, "_correction_context_message", return_value=""):
        await narrator.run_narrator(message)
    assert "共 5 位調查員" in seen[0] and "調查員4" in seen[0]


@aio
async def test_another_groups_count_in_the_same_reply_is_left_alone() -> None:
    reply, _ = await _reply(_state(5), "敵方有六名探員守在門口，你們六位調查員屏住呼吸。")
    assert "敵方有六名探員" in reply and "你們五位調查員" in reply


@aio
async def test_the_party_is_corrected_before_delivery_validates_the_reply() -> None:
    seen: list[str] = []
    original = turn_delivery.finalize

    def spy(message, narrative):
        seen.append(narrative)
        return original(message, narrative)

    with patch.object(supervisor.turn_delivery, "finalize", spy):
        reply, _ = await _reply(_state(5), "你們六位調查員站在營地入口。")
    assert seen == ["你們五位調查員站在營地入口。"] and "五位" in reply
