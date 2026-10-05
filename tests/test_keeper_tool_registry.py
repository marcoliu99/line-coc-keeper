"""Keeper tool registry behavior and provider ordering."""
from collections.abc import Callable
from unittest.mock import patch

import pytest

from app import tool_dispatch
from app.keeper_tools import registry, support
from app.keeper_tools import registry as tool_registry
from app.models import Character, GroupState
from app.services import mutation_admission


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
    assert tool_registry.TOOLS == player_schemas

    with patch.object(tool_dispatch, "SCENARIO_RAG_ENABLED", False):
        assert tool_dispatch.tools_for_speaker_role("player") == player_schemas
    with patch.object(tool_dispatch, "SCENARIO_RAG_ENABLED", True):
        available = [*player_schemas, registry.SEARCH_SCENARIO_TOOL]
        assert tool_dispatch.tools_for_speaker_role("player") == available
        assert [schema["name"] for schema in tool_dispatch.tools_for_speaker_role("kp_assistant")] == [
            schema["name"] for schema in available if registry.REGISTRY[schema["name"]].kp_assistant
        ]


def test_summary_schema_does_not_become_a_keeper_tool() -> None:
    assert registry.SUMMARY_TOOL not in tool_registry.TOOLS
    with patch.object(mutation_admission, "assert_admitted"):
        summary_result = tool_dispatch.execute_tool(
            GroupState(group_id="summary"), "report_summary", {"summary": "done"}, [], [],
        )
        unknown_result = tool_dispatch.execute_tool(
            GroupState(group_id="summary"), "missing_tool", {}, [], [],
        )
    assert summary_result == {"ok": False, "error": "未知工具 report_summary"}
    assert unknown_result == {"ok": False, "error": "未知工具 missing_tool"}


def test_check_family_returns_expected_result() -> None:
    with patch.object(mutation_admission, "assert_admitted"):
        result = tool_dispatch.execute_tool(
            GroupState(group_id="npc-check"), "npc_skill_check", {"skill_value": 100}, [], [],
        )
    assert result["ok"] and result["skill_value"] == 100


def test_dice_family_returns_expected_result() -> None:
    state = GroupState(group_id="dice-family")
    state.characters["p1"] = Character(name="Investigator", owner_id="p1", occupation="Detective")
    with patch.object(mutation_admission, "assert_admitted"):
        ordinary = tool_dispatch.execute_tool(state, "roll_dice", {"expression": "1d2"}, [], [])
        impaling = tool_dispatch.execute_tool(
            state, "roll_impaling_damage",
            {"weapon_damage": "1d2", "damage_bonus": "0", "impaling": False}, [], [],
        )
        weapon = tool_dispatch.execute_tool(
            state, "roll_weapon_damage",
            {"investigator": "Investigator", "weapon_damage": "1d2"}, [], [],
        )
    assert ordinary["ok"] and ordinary["total"] in {1, 2}
    assert impaling["ok"] and impaling["total"] == 2
    assert weapon["ok"] and weapon["investigator"] == "Investigator"


def test_character_family_returns_expected_result() -> None:
    state = GroupState(group_id="character-family")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")
    with (patch.object(mutation_admission, "assert_admitted"),
          patch.object(support, "refresh_tool_state")):
        result = tool_dispatch.execute_tool(
            state, "get_character_sheet", {"investigator": "Ada"}, [], [],
        )
    assert result["ok"] and result["sheet"]["name"] == "Ada"


def test_inventory_family_returns_expected_result() -> None:
    state = GroupState(group_id="inventory-family")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")

    def mutate(current: GroupState, callback: Callable[[GroupState], object]) -> object:
        result = callback(current)
        return result.value if isinstance(result, support.ToolStateMutation) else result

    with (patch.object(mutation_admission, "assert_admitted"),
          patch.object(support, "mutate_tool_state", side_effect=mutate)):
        added = tool_dispatch.execute_tool(
            state, "add_carried_item", {"investigator": "Ada", "item": " key "}, [], [],
        )
        assert added["ok"] and state.characters["p1"].carried_items == ["key"]
        removed = tool_dispatch.execute_tool(
            state, "remove_carried_item", {"investigator": "Ada", "item": "key "}, [], [],
        )
    assert removed["ok"] and state.characters["p1"].carried_items == []
    assert state.consumed_or_removed_items[-1]["item"] == "key"


def test_scenario_search_family_returns_expected_result() -> None:
    from app import memory_rag

    with (patch.object(mutation_admission, "assert_admitted"),
          patch.object(memory_rag, "search_memory", return_value=[]),
          patch.object(memory_rag, "format_results", return_value="none")):
        result = tool_dispatch.execute_tool(
            GroupState(group_id="scenario-search"), "search_memory", {"query": "door"}, [], [],
        )
    assert result == {"ok": True, "results": "none"}


def test_messaging_family_delivers_privately() -> None:
    state = GroupState(group_id="private-message")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")
    messages: list[tuple[str, str]] = []
    with patch.object(mutation_admission, "assert_admitted"):
        result = tool_dispatch.execute_tool(
            state, "send_private_info", {"investigator": "Ada", "message": "secret"}, messages, [],
        )
    assert result == {"ok": True, "delivered_to": "Ada"}
    assert messages == [("p1", "secret")]


def test_combat_family_returns_status_and_requires_reviewed_settlement() -> None:
    state = GroupState(group_id="combat-family")

    def mutate(current: GroupState, callback: Callable[[GroupState], object]) -> object:
        result = callback(current)
        return result.value if isinstance(result, support.ToolStateMutation) else result

    with (patch.object(mutation_admission, "assert_admitted"),
          patch.object(support, "mutate_tool_state", side_effect=mutate),
          patch.object(support, "refresh_tool_state")):
        started = tool_dispatch.execute_tool(state, "start_combat", {}, [], [])
        added = tool_dispatch.execute_tool(
            state, "add_npc_to_combat", {"name": "Cultist", "dex": 50, "hp": 10}, [], [],
        )
        status = tool_dispatch.execute_tool(state, "get_combat_status", {}, [], [])
        preview = tool_dispatch.execute_tool(state, "end_combat", {}, [], [])
        assert state.combat.active
        ended = tool_dispatch.execute_tool(state, "confirm_combat_settlement", {
            "combat_id": state.combat.combat_id,
            "settlement_id": preview["preview"]["settlement_id"],
            "reason": "Keeper reviewed the resource differences",
        }, [], [])

    assert started["ok"] and added["ok"] and status["ok"] and ended["ok"]
    assert "Cultist" in status["status"]
    assert not state.combat.active
