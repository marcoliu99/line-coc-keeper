"""A hand-off between investigators is one committed step: both inventories change, or neither does."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import db, observability, tool_dispatch
from app.models import Character, GroupState
from app.repositories import group_state


@pytest.fixture(autouse=True)
def database(tmp_path):
    with patch.object(db, "DB_PATH", tmp_path / "state.db"), patch.object(db, "BACKUP_DIR", tmp_path / "backups"), \
            patch.object(group_state.db, "DB_PATH", tmp_path / "state.db"):
        db._ensure_tables()
        yield


def _state(**carried: list[str]) -> GroupState:
    state = GroupState("g")
    for owner, name in (("u1", "Ann"), ("u2", "Bea"), ("u3", "Anna")):
        state.characters[owner] = Character(name, owner, carried_items=list(carried.get(name, [])))
    group_state.save_state(state)
    return state


def _transfer(state: GroupState, args: dict, *, actor: str = "u1", origin: str | None = None) -> dict:
    return tool_dispatch.execute_tool(state, "transfer_item", args, [], [], actor_id=actor, system_origin=origin)  # type: ignore[arg-type]


def _items(state_id: str = "g") -> dict[str, list[str]]:
    loaded = group_state.load_state(state_id)
    return {c.name: list(c.carried_items) for c in loaded.all_characters()}


def test_a_hand_off_moves_the_item_in_one_commit_and_is_recorded():
    state = _state(Ann=["古書", "鑰匙"])
    result = _transfer(state, {"from": "Ann", "to": "Bea", "item": "古書"})
    assert result["ok"] and (result["from"], result["to"], result["quantity"]) == ("Ann", "Bea", 1)
    assert _items() == {"Ann": ["鑰匙"], "Bea": ["古書"], "Anna": []}
    loaded = group_state.load_state("g")
    (record,) = loaded.inventory_transfers
    assert (record["from"], record["to"], record["item"], record["quantity"]) == ("Ann", "Bea", "古書", 1)
    assert loaded.consumed_or_removed_items == []  # a hand-off is not a consumption


def test_an_old_snapshot_without_the_record_loads_with_an_empty_list():
    data = GroupState("old").to_dict()
    del data["inventory_transfers"]
    assert GroupState.from_dict(data).inventory_transfers == []


REFUSALS = [
    ({"from": "Ann", "to": "Bea", "item": "手電筒"}, "item_not_held"),
    ({"from": "Ann", "to": "Bea", "item": "古"}, "item_not_held"),            # a substring of the item
    ({"from": "Ann", "to": "Ann", "item": "古書"}, "same_character"),
    ({"from": "Ann", "to": "Nobody", "item": "古書"}, "unknown_receiver"),
    ({"from": "Be", "to": "Ann", "item": "古書"}, "unknown_giver"),            # a partial name never resolves
    ({"from": "Ann", "to": "Be", "item": "古書"}, "unknown_receiver"),         # a partial name of another investigator
    ({"from": "Ann", "to": "Ann", "item": "  "}, "empty_item"),
    ({"from": "Ann", "to": "Bea", "item": "古書", "quantity": 0}, "invalid_quantity"),
    ({"from": "Ann", "to": "Bea", "item": "古書", "quantity": -1}, "invalid_quantity"),
    ({"from": "Ann", "to": "Bea", "item": "古書", "quantity": 1.5}, "invalid_quantity"),
    ({"from": "Ann", "to": "Bea", "item": "古書", "quantity": "2"}, "invalid_quantity"),
    ({"from": "Ann", "to": "Bea", "item": "古書", "quantity": True}, "invalid_quantity"),
    ({"from": "Ann", "to": "Bea", "item": "古書", "quantity": 2}, "item_not_held"),
    ({"from": "Bea", "to": "Ann", "item": "古書"}, "not_actors_item"),         # the acting player is Ann
]


@pytest.mark.parametrize(("args", "code"), REFUSALS)
def test_a_refused_hand_off_writes_nothing(args, code):
    state = _state(Ann=["古書"], Bea=["古書"])
    before = group_state.load_state("g").to_dict()
    result = _transfer(state, args)
    assert result["ok"] is False and result["refusal"] == code
    assert group_state.load_state("g").to_dict() == before


def test_a_name_that_two_active_investigators_share_is_ambiguous_not_guessed():
    state = _state(Ann=["古書"])
    state.characters["u4"] = Character("Bea", "u4")
    group_state.save_state(state)
    result = _transfer(state, {"from": "Ann", "to": "Bea", "item": "古書"})
    assert result["refusal"] == "ambiguous_receiver" and "Bea" in result["error"]
    assert _items()["Ann"] == ["古書"]


def test_without_a_known_acting_player_nothing_moves():
    state = _state(Ann=["古書"])
    assert _transfer(state, {"from": "Ann", "to": "Bea", "item": "古書"}, actor="")["refusal"] == "not_actors_item"


def test_the_kp_assistant_may_move_an_item_between_other_investigators():
    state = _state(Ann=["古書"])
    result = tool_dispatch.execute_tool(state, "transfer_item", {"from": "Ann", "to": "Bea", "item": "古書"}, [], [],
                                        speaker_role="kp_assistant")
    assert result["ok"] and _items()["Bea"] == ["古書"]


def test_a_player_cannot_claim_the_kp_origin_in_the_arguments():
    state = _state(Bea=["古書"])
    result = _transfer(state, {"from": "Bea", "to": "Ann", "item": "古書", "system_origin": "kp_assistant"})
    assert result["refusal"] == "not_actors_item"


def test_two_copies_move_together_and_a_duplicate_stays_a_second_entry():
    state = _state(Ann=["手電筒", "手電筒", "鑰匙"], Bea=["手電筒"])
    result = _transfer(state, {"from": "Ann", "to": "Bea", "item": "手電筒", "quantity": 2})
    assert result["ok"] and result["giver_remaining"] == 0
    assert _items() == {"Ann": ["鑰匙"], "Bea": ["手電筒", "手電筒", "手電筒"], "Anna": []}
    assert group_state.load_state("g").inventory_transfers[0]["quantity"] == 2


def test_the_item_matches_without_regard_to_case_or_padding_and_keeps_the_givers_spelling():
    state = _state(Ann=["Old Book"])
    assert _transfer(state, {"from": "ann", "to": " BEA ", "item": " old book "})["ok"]
    assert _items()["Bea"] == ["Old Book"]


def test_a_failure_after_the_giver_changed_leaves_both_inventories_as_they_were():
    state = _state(Ann=["古書"])
    with patch.object(observability, "current_context", side_effect=RuntimeError("boom")):
        result = _transfer(state, {"from": "Ann", "to": "Bea", "item": "古書"})  # execute_tool reports the error
    assert result["ok"] is False and "boom" in result["error"]
    assert _items() == {"Ann": ["古書"], "Bea": [], "Anna": []}
    assert group_state.load_state("g").inventory_transfers == []


def test_the_tool_is_declared_for_the_kp_assistant_and_recorded_as_game_canon():
    from app.keeper_tools import registry
    spec = registry.REGISTRY["transfer_item"]
    assert spec.kp_assistant and spec.kp_canonical_game
    assert "transfer_item" in registry.KP_ASSISTANT_ALLOWED_TOOL_NAMES
    assert set(spec.schema["input_schema"]["required"]) == {"from", "to", "item"}
