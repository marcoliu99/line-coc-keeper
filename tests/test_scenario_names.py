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


def test_names_are_settled_before_delivery_validates_them():
    names = [step.name for step in reply_pipeline.STEPS]
    assert names.index("obligations") < names.index("scenario_names") < names.index("finalize")
