"""Verify Keeper tool registry coverage, routing, and provider order."""
from collections.abc import Callable
from unittest.mock import patch

from app import keeper
from app.agents import narrator, tool_gateway
from app.keeper_tools import registry
from app.models import Character, GroupState
from app.services import turn_context, turn_resolution

PLAYER_TOOL_ORDER = (
    "roll_dice", "roll_impaling_damage", "roll_weapon_damage", "skill_check",
    "offer_check_choice", "npc_skill_check", "offer_npc_attack_defense_choice",
    "clear_pending_check", "sanity_check", "adjust_character", "adjust_ammo",
    "add_carried_item", "remove_carried_item", "record_established_fact",
    "record_clue", "add_status_tag", "remove_status_tag", "set_skill",
    "get_character_sheet", "start_combat", "add_npc_to_combat", "get_combat_status",
    "advance_combat_turn", "damage_combatant", "plan_enemy_turn",
    "resolve_enemy_action", "apply_combat_damage", "apply_final_combat_damage",
    "add_combat_effect", "end_combat", "send_private_info",
    "search_scenario_images", "advance_scenario_chapter", "show_scenario_image",
    "search_memory",
)

def test_capability_consumers_use_registry_properties() -> None:
    assert keeper.READ_ONLY_TOOL_NAMES == registry.READ_ONLY_TOOL_NAMES
    assert keeper.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES == registry.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES
    assert keeper._KP_ASSISTANT_ALLOWED_TOOL_NAMES == registry.KP_ASSISTANT_ALLOWED_TOOL_NAMES
    assert keeper._KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES == registry.KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES
    assert keeper._COMBAT_STATUS_INVALIDATING_TOOLS == registry.COMBAT_STATUS_INVALIDATING_TOOLS
    assert tool_gateway.BOUNDED_QUERY_TOOLS == registry.BOUNDED_QUERY_TOOLS
    assert tool_gateway._CHECK_REGISTRATION_TOOLS == registry.CHECK_REGISTRATION_TOOLS
    assert narrator._OPENING_TOOL_NAMES == registry.OPENING_TOOL_NAMES
    assert turn_context._CHECK_CREATION_TOOLS == registry.CHECK_CREATION_TOOLS
    assert turn_resolution.INFORMATION_QUERY_TOOLS == registry.INFORMATION_QUERY_TOOLS


def test_registry_covers_schemas_and_preserves_provider_order() -> None:
    assert tuple(registry.REGISTRY) == (*PLAYER_TOOL_ORDER, "search_scenario", "report_summary")
    assert all(name == spec.schema["name"] and callable(spec.handler)
               for name, spec in registry.REGISTRY.items())
    assert [tool["name"] for tool in keeper.TOOLS] == list(PLAYER_TOOL_ORDER)

    with patch.object(keeper, "SCENARIO_RAG_ENABLED", False):
        assert [tool["name"] for tool in keeper._tools_for_speaker_role("player")] == list(PLAYER_TOOL_ORDER)
    with patch.object(keeper, "SCENARIO_RAG_ENABLED", True):
        assert [tool["name"] for tool in keeper._tools_for_speaker_role("player")] == [
            *PLAYER_TOOL_ORDER, "search_scenario",
        ]
        assert [tool["name"] for tool in keeper._tools_for_speaker_role("kp_assistant")] == [
            name for name in (*PLAYER_TOOL_ORDER, "search_scenario") if name in registry.KP_ASSISTANT_ALLOWED_TOOL_NAMES
        ]


def test_summary_schema_is_not_a_turn_tool() -> None:
    state = GroupState(group_id="summary-only")
    with patch.object(keeper.mutation_admission, "assert_admitted"):
        result = keeper._execute_tool(state, "report_summary", {"summary": "notes"}, [], [])
    assert result == {"ok": False, "error": "未知工具 report_summary"}


def test_check_family_dispatches_without_legacy_cascade() -> None:
    from app.models import GroupState

    with patch.object(keeper.mutation_admission, "assert_admitted"):
        result = keeper._execute_tool(
            GroupState(group_id="npc-check"), "npc_skill_check", {"skill_value": 100}, [], [],
        )
    assert result["ok"] and result["skill_value"] == 100


def test_dice_family_dispatches_without_legacy_cascade() -> None:
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


def test_character_family_dispatches_without_legacy_cascade() -> None:
    from app.models import Character, GroupState

    state = GroupState(group_id="character-family")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")
    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(keeper, "refresh_tool_state")):
        result = keeper._execute_tool(
            state, "get_character_sheet", {"investigator": "Ada"}, [], [],
        )
    assert result["ok"] and result["sheet"]["name"] == "Ada"


def test_inventory_family_dispatches_without_legacy_cascade() -> None:
    from app.models import Character, GroupState

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


def test_scenario_search_family_dispatches_without_legacy_cascade() -> None:
    from app import memory_rag
    from app.models import GroupState

    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(memory_rag, "search_memory", return_value=[]),
          patch.object(memory_rag, "format_results", return_value="none")):
        result = keeper._execute_tool(
            GroupState(group_id="scenario-search"), "search_memory", {"query": "door"}, [], [],
        )
    assert result == {"ok": True, "results": "none"}


def test_messaging_family_delivers_privately_without_legacy_cascade() -> None:
    from app.models import Character, GroupState

    state = GroupState(group_id="private-message")
    state.characters["p1"] = Character(name="Ada", owner_id="p1", occupation="Detective")
    messages: list[tuple[str, str]] = []
    with patch.object(keeper.mutation_admission, "assert_admitted"):
        result = keeper._execute_tool(
            state, "send_private_info", {"investigator": "Ada", "message": "secret"}, messages, [],
        )
    assert result == {"ok": True, "delivered_to": "Ada"}
    assert messages == [("p1", "secret")]


def test_combat_family_uses_registered_handlers_without_legacy_cascade() -> None:
    combat_names = {
        "start_combat", "add_npc_to_combat", "get_combat_status",
        "advance_combat_turn", "damage_combatant", "plan_enemy_turn",
        "resolve_enemy_action", "apply_combat_damage", "apply_final_combat_damage",
        "add_combat_effect", "end_combat",
    }
    assert all(registry.REGISTRY[name].handler.__module__ == "app.keeper_tools.combat"
               for name in combat_names)

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
