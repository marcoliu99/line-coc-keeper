"""Keeper tool registry behavior and provider ordering."""
from collections.abc import Callable
from unittest.mock import patch

import pytest

from app import keeper
from app.keeper_tools import registry
from app.models import Character, GroupState


def test_tool_spec_requires_an_explicit_handler() -> None:
    with pytest.raises(TypeError):
        registry.ToolSpec(schema={"name": "unhandled", "input_schema": {"type": "object"}})


def test_registry_covers_schemas_and_orders_provider_tools() -> None:
    assert all(name == spec.schema["name"] and callable(spec.handler)
               for name, spec in registry.REGISTRY.items())
    player_schemas = [
        spec.schema for name, spec in registry.REGISTRY.items()
        if name not in {"search_scenario", "report_summary"} and not spec.followup_only
    ]
    assert keeper.TOOLS == player_schemas

    with patch.object(keeper, "SCENARIO_RAG_ENABLED", False):
        assert keeper._tools_for_speaker_role("player") == player_schemas
    with patch.object(keeper, "SCENARIO_RAG_ENABLED", True):
        available = [*player_schemas, registry.SEARCH_SCENARIO_TOOL]
        assert keeper._tools_for_speaker_role("player") == available
        assert [schema["name"] for schema in keeper._tools_for_speaker_role("kp_assistant")] == [
            schema["name"] for schema in available if registry.REGISTRY[schema["name"]].kp_assistant
        ]


def test_summary_schema_does_not_become_a_keeper_tool() -> None:
    assert registry.SUMMARY_TOOL not in keeper.TOOLS
    with patch.object(keeper.mutation_admission, "assert_admitted"):
        summary_result = keeper._execute_tool(
            GroupState(group_id="summary"), "report_summary", {"summary": "done"}, [], [],
        )
        unknown_result = keeper._execute_tool(
            GroupState(group_id="summary"), "missing_tool", {}, [], [],
        )
    assert summary_result == {"ok": False, "error": "未知工具 report_summary"}
    assert unknown_result == {"ok": False, "error": "未知工具 missing_tool"}


def test_check_family_returns_expected_result() -> None:
    with patch.object(keeper.mutation_admission, "assert_admitted"):
        result = keeper._execute_tool(
            GroupState(group_id="npc-check"), "npc_skill_check", {"skill_value": 100}, [], [],
        )
    assert result["ok"] and result["skill_value"] == 100


def test_dice_family_returns_expected_result() -> None:
    state = GroupState(group_id="dice-family")
    state.characters["p1"] = Character(name="Investigator", owner_id="p1", occupation="Detective")
    with patch.object(keeper.mutation_admission, "assert_admitted"):
        ordinary = keeper._execute_tool(state, "roll_dice", {"expression": "1d2"}, [], [])
        impaling = keeper._execute_tool(
            state, "roll_impaling_damage",
            {"weapon_damage": "1d2", "damage_bonus": "0", "impaling": False}, [], [],
        )
        weapon = keeper._execute_tool(
            state, "roll_weapon_damage",
            {"investigator": "Investigator", "weapon_damage": "1d2"}, [], [],
        )
    assert ordinary["ok"] and ordinary["total"] in {1, 2}
    assert impaling["ok"] and impaling["total"] == 2
    assert weapon["ok"] and weapon["investigator"] == "Investigator"


def test_character_family_returns_expected_result() -> None:
    state = GroupState(group_id="character-family")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")
    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(keeper, "refresh_tool_state")):
        result = keeper._execute_tool(
            state, "get_character_sheet", {"investigator": "Ada"}, [], [],
        )
    assert result["ok"] and result["sheet"]["name"] == "Ada"


def test_inventory_family_returns_expected_result() -> None:
    state = GroupState(group_id="inventory-family")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")

    def mutate(current: GroupState, callback: Callable[[GroupState], object]) -> object:
        result = callback(current)
        return result.value if isinstance(result, keeper.ToolStateMutation) else result

    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(keeper, "mutate_tool_state", side_effect=mutate)):
        added = keeper._execute_tool(
            state, "add_carried_item", {"investigator": "Ada", "item": " key "}, [], [],
        )
        assert added["ok"] and state.characters["p1"].carried_items == ["key"]
        removed = keeper._execute_tool(
            state, "remove_carried_item", {"investigator": "Ada", "item": "key "}, [], [],
        )
    assert removed["ok"] and state.characters["p1"].carried_items == []
    assert state.consumed_or_removed_items[-1]["item"] == "key"


def test_scenario_search_family_returns_expected_result() -> None:
    from app import memory_rag

    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(memory_rag, "search_memory", return_value=[]),
          patch.object(memory_rag, "format_results", return_value="none")):
        result = keeper._execute_tool(
            GroupState(group_id="scenario-search"), "search_memory", {"query": "door"}, [], [],
        )
    assert result == {"ok": True, "results": "none"}


def test_messaging_family_delivers_privately() -> None:
    state = GroupState(group_id="private-message")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")
    messages: list[tuple[str, str]] = []
    with patch.object(keeper.mutation_admission, "assert_admitted"):
        result = keeper._execute_tool(
            state, "send_private_info", {"investigator": "Ada", "message": "secret"}, messages, [],
        )
    assert result == {"ok": True, "delivered_to": "Ada"}
    assert messages == [("p1", "secret")]


def test_combat_family_returns_status_and_ends() -> None:
    state = GroupState(group_id="combat-family")

    def mutate(current: GroupState, callback: Callable[[GroupState], object]) -> object:
        result = callback(current)
        return result.value if isinstance(result, keeper.ToolStateMutation) else result

    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(keeper, "mutate_tool_state", side_effect=mutate),
          patch.object(keeper, "refresh_tool_state")):
        started = keeper._execute_tool(state, "start_combat", {}, [], [])
        added = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "Cultist", "dex": 50, "hp": 10}, [], [],
        )
        status = keeper._execute_tool(state, "get_combat_status", {}, [], [])
        ended = keeper._execute_tool(state, "end_combat", {}, [], [])

    assert started["ok"] and added["ok"] and status["ok"] and ended["ok"]
    assert "Cultist" in status["status"]
    assert not state.combat.active
