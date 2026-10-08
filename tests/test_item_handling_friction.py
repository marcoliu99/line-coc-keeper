"""Items: a near-miss name still finds the item, a no-op call never costs the player the turn, and the sheet
labels weapons as weapons (docs/specs/enhancement/item_handling_friction_design_spec.md)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app import combat_rules, db, tool_dispatch
from app.keeper_tools import inventory, registry
from app.models import Character, GroupState
from app.repositories import group_state
from app.services import mutation_admission, turn_resolution


@pytest.fixture(autouse=True)
def database(tmp_path):
    with patch.object(db, "DB_PATH", tmp_path / "state.db"), patch.object(db, "BACKUP_DIR", tmp_path / "backups"), \
            patch.object(group_state.db, "DB_PATH", tmp_path / "state.db"):
        db._ensure_tables()
        yield


def _state(items: list[str]) -> GroupState:
    state = GroupState(group_id="items", timeline_id="t")
    state.characters["u1"] = Character(name="Ann", owner_id="u1", carried_items=list(items))
    group_state.save_state(state)
    turn_resolution.actor_snapshot(state, "u1")  # binds the active character, as the Executor has by validation time
    return state


def _call(state: GroupState, name: str, item: str) -> dict:
    with patch.object(mutation_admission, "assert_admitted"):
        return tool_dispatch.execute_tool(state, name, {"investigator": "Ann", "item": item}, [], [], actor_id="u1")


@pytest.mark.parametrize("reference, expected", [
    ("地下室鑰匙", ["地下室鑰匙"]),  # exact
    ("knife", ["Knife"]),  # case
    ("鑰匙", ["地下室鑰匙"]),  # part of the stored text
    ("一把生鏽的地下室鑰匙", ["地下室鑰匙"]),  # stored text is part of the reference
    ("手電筒", []),
    ("", []),
])
def test_match_carried_items_finds_the_stored_entry(reference, expected):
    assert inventory.match_carried_items(["地下室鑰匙", "Knife", "一封信"], reference) == expected


def test_match_carried_items_reports_an_ambiguous_reference_as_every_candidate():
    assert inventory.match_carried_items(["地下室鑰匙", "閣樓鑰匙"], "鑰匙") == ["地下室鑰匙", "閣樓鑰匙"]


def test_remove_by_a_partial_name_removes_the_stored_entry():
    state = _state(["地下室鑰匙", "一封信"])
    result = _call(state, "remove_carried_item", "鑰匙")
    assert result["ok"] and result["removed"] == "地下室鑰匙" and result["changed"] is True
    assert result["carried_items"] == ["一封信"]
    assert state.consumed_or_removed_items[-1]["item"] == "地下室鑰匙"


def test_remove_of_an_item_not_held_is_refused_with_the_pack_listed():
    state = _state(["地下室鑰匙"])
    result = _call(state, "remove_carried_item", "手電筒")
    assert not result["ok"] and result["refusal"] == "item_not_held" and result["changed"] is False
    assert "地下室鑰匙" in result["error"] and result["carried_items"] == ["地下室鑰匙"]
    assert state.characters["u1"].carried_items == ["地下室鑰匙"]


def test_remove_of_an_ambiguous_name_is_refused_with_the_candidates():
    state = _state(["地下室鑰匙", "閣樓鑰匙"])
    result = _call(state, "remove_carried_item", "鑰匙")
    assert not result["ok"] and result["refusal"] == "ambiguous_item"
    assert result["candidates"] == ["地下室鑰匙", "閣樓鑰匙"]
    assert state.characters["u1"].carried_items == ["地下室鑰匙", "閣樓鑰匙"]


def test_adding_an_item_already_carried_succeeds_as_a_no_op():
    state = _state(["手電筒"])
    result = _call(state, "add_carried_item", "手電筒 ")
    assert result["ok"] and result["changed"] is False and result["already_carried"]
    assert state.characters["u1"].carried_items == ["手電筒"]
    added = _call(state, "add_carried_item", "鑰匙")
    assert added["ok"] and added["changed"] is True and added["carried_items"] == ["手電筒", "鑰匙"]


def test_inventory_tools_are_offered_to_the_narrator_after_a_settled_check():
    assert {"add_carried_item", "remove_carried_item"} <= registry.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES


def _event(name: str, result: dict, before: dict, gameplay: dict) -> dict:
    return {"name": name, "arguments": {}, "result": result, "inventory_before": before,
            "gameplay_before": gameplay, "gameplay_after": gameplay, "actor_changed": False}


def _validate(state: GroupState, events: list[dict], disposition: str, refs: list[str],
              gameplay: dict) -> turn_resolution.TurnResolution:
    text = json.dumps({"disposition": disposition, "actor_character_id": state.characters["u1"].character_id,
                       "waiting_for": "", "check_id": "", "reason": "", "evidence_refs": refs})
    before_actor = turn_resolution.actor_snapshot(state, "u1")
    return turn_resolution.validate_resolution(
        text, state=state, user_id="u1", before_pending={}, before_luck={}, tool_events=events,
        has_scenario=False, before_actor=before_actor, before_gameplay=gameplay)


def test_a_refused_removal_followed_by_the_right_name_still_completes_the_turn():
    state = _state(["地下室鑰匙"])
    state.characters["u1"].to_dict()  # assign the legacy character id the resolution compares against
    gameplay = turn_resolution.gameplay_snapshot(state)
    refused = _event("remove_carried_item", {"ok": False, "refusal": "item_not_held", "investigator": "Ann",
                                             "carried_items": ["地下室鑰匙"], "changed": False},
                     {"Ann": ["地下室鑰匙"]}, gameplay)
    refused["arguments"] = {"investigator": "Ann", "item": "鑰匙"}
    state.characters["u1"].carried_items = []
    removed = _event("remove_carried_item", {"ok": True, "investigator": "Ann", "character_id": state.characters["u1"].character_id,
                                             "carried_items": [], "removed": "地下室鑰匙", "changed": True},
                     {"Ann": ["地下室鑰匙"]}, gameplay)
    removed["arguments"] = {"investigator": "Ann", "item": "地下室鑰匙"}
    resolution = _validate(state, [refused, removed], "resolved_without_check", ["tool:2"], gameplay)
    assert resolution.disposition == "resolved_without_check", resolution.reason


def test_an_unretried_refusal_after_a_real_change_still_voids_the_completion():
    state = _state(["地下室鑰匙"])
    state.characters["u1"].to_dict()
    gameplay = turn_resolution.gameplay_snapshot(state)
    state.characters["u1"].carried_items = []
    removed = _event("remove_carried_item", {"ok": True, "investigator": "Ann", "character_id": state.characters["u1"].character_id,
                                             "carried_items": [], "removed": "地下室鑰匙", "changed": True},
                     {"Ann": ["地下室鑰匙"]}, gameplay)
    refused = _event("add_carried_item", {"ok": False, "error": "找不到角色「nobody」"}, {"Ann": []}, gameplay)
    resolution = _validate(state, [removed, refused], "resolved_without_check", ["tool:1"], gameplay)
    assert resolution.disposition == "incomplete"


def test_a_duplicate_add_lets_the_turn_end_as_no_mechanics():
    state = _state(["手電筒"])
    state.characters["u1"].to_dict()
    gameplay = turn_resolution.gameplay_snapshot(state)
    noop = _event("add_carried_item", {"ok": True, "investigator": "Ann", "character_id": state.characters["u1"].character_id,
                                       "carried_items": ["手電筒"], "changed": False, "already_carried": True},
                  {"Ann": ["手電筒"]}, gameplay)
    resolution = _validate(state, [noop], "no_mechanics", ["state"], gameplay)
    assert resolution.disposition == "no_mechanics", resolution.reason


def test_the_sheet_lists_melee_weapons_as_weapons_not_ammunition():
    char = Character(name="Ann", owner_id="u1", weapons={"小刀": {}, ".38 左輪": {"ammo": 6, "ammo_max": 6}})
    assert "武器：小刀" in char.sheet_text() and "彈藥：.38 左輪 6/6" in char.sheet_text()
    assert "武器 小刀" in char.dynamic_state_text() and "彈藥 .38 左輪 6/6" in char.dynamic_state_text()
    assert Character(name="B", owner_id="u2", weapons={"小刀": {}}).weapon_lines("武器：", "彈藥：") == ["武器：小刀"]


@pytest.mark.parametrize("reference, weapon_id", [
    ("小刀", "i.weapon.knife-small"), ("匕首", "i.weapon.knife-small"), ("菜刀", "i.weapon.knife-medium"),
    ("開山刀", "i.weapon.knife-large"), ("球棒", "i.weapon.club-large"), ("指虎", "i.weapon.brass-knuckles"),
    ("Dagger", "i.weapon.knife-small"), ("斧頭", "i.weapon.axe"), ("手斧", "i.weapon.hatchet-sickle"),
    ("長劍", "i.weapon.sword"), ("西洋劍", "i.weapon.rapier"), ("剃刀", "i.weapon.knife-small"),
])
def test_common_chinese_weapon_names_resolve(reference, weapon_id):
    resolution = combat_rules.resolve_weapon(reference)
    assert resolution.status == "resolved" and resolution.definition.id == weapon_id


def test_a_gauge_less_double_barrel_needs_the_keeper_to_pick_the_gauge():
    resolution = combat_rules.resolve_weapon("雙管霰彈槍")
    assert resolution.status == "needs_ruling" and len(resolution.candidates) == 4  # 20, 16, 12 gauge and the sawed-off


def test_every_alias_names_one_weapon_unless_deliberately_shared():
    shared = {"handgun", "pistol", "手槍", "左輪", "左輪手槍", "revolver", ".45", "步槍", "rifle", "shotgun", "霰彈槍",
              "散彈槍", "獵槍", "雙管霰彈槍", "雙管散彈槍", "double barrel shotgun", "衝鋒槍", "submachine gun", "smg",
              "機槍", "machine gun"}
    seen: dict[str, str] = {}
    for definition in combat_rules.weapon_catalog():
        for alias in definition.aliases:
            key = alias.casefold()
            assert key in shared or key not in seen, f"{alias} names both {seen.get(key)} and {definition.id}"
            seen[key] = definition.id


@pytest.mark.parametrize("reference, weapon_id", [
    ("一把生鏽的小刀", "i.weapon.knife-small"),  # the pack's own wording contains the catalog name
    ("矛", "i.weapon.spear"),  # a one-character name is still a name
    ("祖父留下的獵刀", "i.weapon.knife-medium"),
])
def test_a_reference_containing_a_weapon_name_resolves_to_it(reference, weapon_id):
    resolution = combat_rules.resolve_weapon(reference)
    assert resolution.candidates and resolution.candidates[0].id == weapon_id


def test_a_bare_knife_is_every_knife_until_the_keeper_picks_one():
    resolution = combat_rules.resolve_weapon("刀")
    assert resolution.status == "needs_ruling"
    assert {d.id for d in resolution.candidates} >= {"i.weapon.knife-small", "i.weapon.knife-medium", "i.weapon.knife-large"}


def test_a_refused_transfer_is_never_hidden_by_a_later_unrelated_transfer():
    state = _state(["X", "Y"])
    state.characters["u1"].to_dict()
    gameplay = turn_resolution.gameplay_snapshot(state)
    refused = _event("transfer_item", {"ok": False, "refusal": "item_not_held"}, {"Ann": ["X", "Y"]}, gameplay)
    refused["arguments"] = {"from": "Ann", "to": "Bob", "item": "X"}
    assert turn_resolution._changed_nothing(refused, [{"name": "transfer_item", "arguments": {"from": "Ann", "to": "Cal", "item": "Y"},
                                                       "result": {"ok": True, "changed": True}}]) is False


def test_a_one_character_item_is_not_removed_by_a_word_that_contains_it():
    state = _state(["信", "手電筒"])
    result = _call(state, "remove_carried_item", "信號槍")
    assert not result["ok"] and result["refusal"] == "item_not_held"
    assert state.characters["u1"].carried_items == ["信", "手電筒"]
    assert _call(state, "remove_carried_item", "信")["removed"] == "信"


def test_the_bow_keeps_its_one_character_name():
    resolution = combat_rules.resolve_weapon("弓")
    assert resolution.status == "resolved" and resolution.definition.id == "i.weapon.bow"


def test_a_refusal_is_only_hidden_by_a_retry_of_the_same_item():
    state = _state(["手電筒"])
    gameplay = turn_resolution.gameplay_snapshot(state)
    refused = _event("remove_carried_item", {"ok": False, "refusal": "item_not_held"}, {"Ann": ["手電筒"]}, gameplay)
    refused["arguments"] = {"investigator": "Ann", "item": "鑰匙"}
    other = _event("remove_carried_item", {"ok": True, "investigator": "Ann", "carried_items": [], "removed": "手電筒", "changed": True},
                   {"Ann": ["手電筒"]}, gameplay)
    other["arguments"] = {"investigator": "Ann", "item": "手電筒"}
    assert turn_resolution._changed_nothing(refused, [other]) is False
    retry = {**other, "arguments": {"investigator": "Ann", "item": "地下室鑰匙"},
             "result": {**other["result"], "removed": "地下室鑰匙"}}
    assert turn_resolution._changed_nothing(refused, [retry]) is True
    # A one-character item removed later is not a retry of a refused longer word that contains it.
    letter_refused = {**refused, "arguments": {"investigator": "Ann", "item": "信號槍"}}
    letter = {**other, "arguments": {"investigator": "Ann", "item": "信"}, "result": {**other["result"], "removed": "信"}}
    assert turn_resolution._changed_nothing(letter_refused, [letter]) is False


def test_the_keeper_reference_weapons_resolve_with_their_table_values():
    spear = combat_rules.resolve_weapon("長矛").definition
    assert (spear.damage, spear.db_policy, spear.extreme_rule, spear.skill_id) == ("1d8+1", "none", "impale", "fighting-spear")
    thrown = combat_rules.resolve_weapon("投矛").definition
    assert (thrown.damage, thrown.db_policy, thrown.base_range_formula) == ("1d8", "half", "STR/5")
    assert combat_rules.base_range_for(thrown, 65) == 13 and combat_rules.base_range_for(thrown, None) is None
    torch = combat_rules.resolve_weapon("火把").definition
    assert (torch.damage, torch.db_policy, torch.skill_id) == ("1d6", "none", "fighting-brawl")
    crossbow = combat_rules.resolve_weapon("弩").definition
    assert (crossbow.damage, crossbow.base_range_yards, crossbow.capacity, crossbow.malfunction) == ("1d8+2", 50, 1, 96)
    thompson = combat_rules.resolve_weapon("湯普森").definition
    assert (thompson.damage, thompson.base_range_yards, thompson.capacity, thompson.malfunction) == ("1d10+2", 20, 20, 96)
    shuriken = combat_rules.resolve_weapon("手裏劍").definition
    assert (shuriken.extreme_rule, shuriken.malfunction, shuriken.base_range_formula) == ("impale", 100, "STR/5")
    uzi = combat_rules.resolve_weapon("烏茲").definition
    assert (uzi.damage, uzi.base_range_yards, uzi.capacity, uzi.malfunction, uzi.skill_id) == (
        "1d10", 20, 32, 98, "firearms-submachine-gun")


@pytest.mark.parametrize("reference, count", [("散彈槍", 5), ("左輪", 2), ("步槍", 7), ("衝鋒槍", 3), ("機槍", 5)])
def test_a_generic_chinese_gun_word_lists_the_models_for_the_keeper(reference, count):
    resolution = combat_rules.resolve_weapon(reference)
    assert resolution.status == "needs_ruling" and len(resolution.candidates) == count


def test_a_sentence_naming_a_specific_gun_resolves_to_it():
    assert combat_rules.resolve_weapon("我拔出點四五左輪").definition.id == "i.weapon.45-revolver"
    assert combat_rules.resolve_weapon("鋸短散彈槍").definition.id == "i.weapon.12-gauge-shotgun-2b-sawed-off"
    assert combat_rules.resolve_weapon("我丟出手裏劍").definition.id == "i.weapon.shuriken"  # 劍 alone would be the sword
