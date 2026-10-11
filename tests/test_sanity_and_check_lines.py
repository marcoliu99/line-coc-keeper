"""Checks from the 2026-10-11 soak runs: a Sanity point lost without the player being told, a mixed skill name
rolled at the wrong value, and the guidance that keeps a settled check from being rolled again."""
from __future__ import annotations

from app.checks.skills import resolve_skill_value
from app.keeper_tools import registry
from app.models import Character, GroupState
from app.prompt_builder import build_static_prompt
from app.services import prompt_config, turn_delivery


def test_a_sanity_roll_says_it_was_sanity_and_what_was_lost():
    line = turn_delivery.observe_tool("sanity_check", {
        "ok": True, "resolved": True, "investigator": "Helen", "roll": 30, "tier": "regular", "success": True,
        "current_san": 40, "san_after": 39, "loss": 1}, 1).public_text
    assert line == "Helen 的理智檢定：骰值 30／40，一般成功，SAN −1（40→39）。", line
    kept = turn_delivery.observe_tool("sanity_check", {
        "ok": True, "resolved": True, "investigator": "Helen", "roll": 30, "tier": "regular",
        "current_san": 40, "san_after": 40, "loss": 0}, 1).public_text
    assert "SAN 沒有損失" in kept


def test_a_skill_roll_names_the_skill():
    line = turn_delivery.observe_tool("skill_check", {"ok": True, "resolved": True, "investigator": "Alan",
                                                      "roll": 30, "tier": "hard"}, 1, {"skill": "偵查"}).public_text
    assert line.startswith("Alan 的偵查已結算：骰值 30")


def test_a_name_that_covers_two_skills_uses_the_better_one():
    alan = Character(name="Alan", owner_id="p1", skills={"聆聽": 25, "偵查": 65})
    assert resolve_skill_value(alan, "偵查與聆聽", register_unknown=False) == 65
    assert resolve_skill_value(alan, "聆聽", register_unknown=False) == 25  # an exact name is still that skill


def test_the_keeper_is_told_sanity_per_witness_on_first_sight_and_no_second_roll():
    prompt = build_static_prompt(GroupState(group_id="g"))
    assert "在場目擊的每一位調查員各擲一次" in prompt and "witnesses" in prompt and "不用再擲" in prompt
    assert "就沿用那次的結果" in prompt and "不要再建立一次新的檢定" in prompt


def test_the_skill_field_says_one_skill_and_drive_is_not_pilot():
    schema = registry.REGISTRY["skill_check"].schema["input_schema"]["properties"]["skill"]["description"]
    assert "一次只擲一個技能" in schema and "開車是「汽車駕駛」" in schema


def test_the_recent_checks_say_what_each_roll_was_for():
    block = prompt_config.build_resolved_check_history_block(
        [{"investigator": "Daniel", "skill": "化學", "skill_value": 40, "roll": 12, "difficulty": "regular",
          "outcome": "hard 成功", "player_declaration": "我辨認架上那些容器的成分。"}], {"HP": "12/12"})
    assert "（當時的行動：我辨認架上那些容器的成分。）" in block


def test_a_sanity_roll_in_the_history_says_what_was_seen():
    block = prompt_config.build_resolved_check_history_block(
        [{"investigator": "Edith", "skill": "SAN", "skill_value": 50, "roll": 30, "outcome": "regular 成功",
          "action_context": "看見 Corbitt 的屍體動起來", "player_declaration": ""}], {"HP": "12/12"})
    assert "（當時的行動：看見 Corbitt 的屍體動起來）" in block


def test_the_autorolled_sanity_event_keeps_its_context():
    from app.checks import service
    from app.models import Character, GroupState
    state = GroupState(group_id="g", autoroll_checks=True)
    edith = Character(name="Edith", owner_id="p1", character_id="c1", san=50)
    state.characters, state.characters_by_id, state.active_character_id_by_user = {"p1": edith}, {"c1": edith}, {"p1": "c1"}
    outcome = service.autoroll_sanity(state, edith, tool_input={"action_context": "看見屍體動起來"},
                                      loss_success="0", loss_failure="1d3")
    assert outcome.event_seed["action_context"] == "看見屍體動起來"


def test_an_investigators_own_rolls_survive_the_rest_of_the_table():
    from app.agents import context_builder
    from app.models import GroupState
    state = GroupState(group_id="g", timeline_id="t")
    mine = {"owner_id": "p1", "character_id": "c1", "timeline_id": "t", "skill": "化學"}
    others = [{"owner_id": "p2", "character_id": "c2", "timeline_id": "t", "skill": "偵查"}] * 30
    state.resolved_check_events = [mine, *others]
    assert context_builder._resolved_events_for_character(state, "p1", "c1", "g") == [mine]


def test_the_stored_history_keeps_enough_for_each_investigator():
    """The table's history is capped when saved and loaded; an investigator's earlier roll must outlive twenty of the
    others'."""
    from app.models import GroupState
    mine = {"owner_id": "p1", "character_id": "c1", "timeline_id": "t", "skill": "化學"}
    others = [{"owner_id": "p2", "character_id": "c2", "timeline_id": "t", "skill": "偵查"}] * 40
    data = GroupState(group_id="g", timeline_id="t").to_dict()
    data["resolved_check_events"] = [mine, *others]
    loaded = GroupState.from_dict(data)
    assert mine in loaded.resolved_check_events


def test_every_witness_can_roll_sanity_within_one_turn_and_says_what_was_seen():
    from app.providers import codex_provider
    assert not codex_provider.counts_against_tool_budget("sanity_check")  # owed by each witness, not chosen
    assert codex_provider.counts_against_tool_budget("skill_check")
    schema = registry.REGISTRY["sanity_check"].schema["input_schema"]
    assert "action_context" in schema["required"] and "看見了什麼" in schema["properties"]["action_context"]["description"]


def test_a_sanity_roll_is_still_a_mutation_for_repeats_and_cached_lookups():
    from app.providers import codex_provider
    assert not codex_provider._is_lookup("sanity_check")  # never re-run as a look-up, and it invalidates cached reads
    assert codex_provider._is_lookup("get_character_sheet")


def test_a_skill_event_keeps_what_the_check_was_for():
    from app.checks import events
    seed = events.event_seed(check_id="c", timeline_id="t", owner_id="p1", character_id="c1", investigator="Daniel",
                             skill="化學", skill_value=40, roll=12, difficulty="regular", outcome="hard 成功",
                             before={}, check_context={"action_context": "辨認架上容器的成分"})
    assert seed["action_context"] == "辨認架上容器的成分"
