"""A person or place reads the same on every turn: the index's Chinese name, never its English original or a fresh
translation (2026-10-12: names drifted between turns of one run)."""
from __future__ import annotations

from app import presentation
from app.agents import reply_pipeline
from app.models import GroupState
from app.prompt_builder import build_static_prompt

CORBITT = {"name": "柯比特", "aliases": ["Walter Corbitt", "Corbitt", "華特"]}
HOUSE = {"name": "柯比特宅", "aliases": ["Corbitt House"]}


def test_an_english_original_is_written_as_the_index_name():
    text = "Walter Corbitt 的身影從 Corbitt House 的地窖升起，corbitt's 笑聲迴盪。"
    assert presentation.use_scenario_names(text, [CORBITT, HOUSE]) == "柯比特 的身影從 柯比特宅 的地窖升起，柯比特的 笑聲迴盪。"


def test_only_what_the_index_lists_and_names_once_changes():
    text = "Corbittson 和 Al 以及 Deep One 都在。"
    entries = [CORBITT, {"name": "艾爾", "aliases": ["Al"]},  # too short to be a name reliably
               {"name": "深潛者", "aliases": ["Deep One"]}, {"name": "深潛者（幼體）", "aliases": ["Deep One"]}]
    assert presentation.use_scenario_names(text, entries) == text  # a longer word, a short alias, two candidates


def test_an_investigator_s_own_name_is_never_rewritten():
    entry = {"name": "卡特", "aliases": ["Carter"]}
    text = "Malcolm Carter 推開門，Carter 先生點頭。"
    assert presentation.use_scenario_names(text, [entry], keep=["Malcolm Carter"]) == text


def test_the_prompt_asks_for_the_index_names():
    state = GroupState(group_id="g", scenario_npc_index=[CORBITT], scenario_location_index=[HOUSE])
    prompt = build_static_prompt(state)
    assert "## 譯名" in prompt and "每次都一樣" in prompt
    assert "## 譯名" not in build_static_prompt(GroupState(group_id="g"))


def test_names_cover_what_delivery_rendered():
    names = [step.name for step in reply_pipeline.STEPS]
    assert names.index("finalize") < names.index("scenario_names") < names.index("player_text")
    assert next(step for step in reply_pipeline.STEPS if step.name == "scenario_names").display_only


def test_a_name_inside_quoted_english_is_left_as_written():
    will = {"name": "威爾", "aliases": ["Will"]}
    assert presentation.use_scenario_names("紙條寫著：「Will he return?」", [will]) == "紙條寫著：「Will he return?」"
    assert presentation.use_scenario_names("Will 推開了門。", [will]) == "威爾 推開了門。"


def test_a_private_message_reads_the_same_names():
    from unittest.mock import MagicMock
    state = GroupState(group_id="g", scenario_npc_index=[CORBITT])
    draft = MagicMock(text="Corbitt 在看你。\n線索：Corbitt 的日記。", private_messages=[("p1", "只有你注意到 Corbitt 的手在動。")],
                      private_controls=[("p1", "Corbitt 的檢定")])
    reply_pipeline._scenario_names(MagicMock(state=state), draft)
    assert draft.text == "柯比特 在看你。\n線索：柯比特 的日記。"
    assert draft.private_messages == [("p1", "只有你注意到 柯比特 的手在動。")]
    assert draft.private_controls == [("p1", "柯比特 的檢定")]


def test_an_alias_with_digits_or_a_comma_is_used_too():
    entries = [{"name": "三號房", "aliases": ["Room 3"]}, {"name": "幼體（深海夢魘）", "aliases": ["Youngling, deep-sea nightmare"]}]
    assert presentation.use_scenario_names("他走進 Room 3。", entries) == "他走進 三號房。"
    assert presentation.use_scenario_names("那是 Youngling, deep-sea nightmare。", entries) == "那是 幼體（深海夢魘）。"
