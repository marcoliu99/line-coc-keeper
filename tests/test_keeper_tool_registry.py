"""Pin the old tool lists while handlers migrate to the registry."""
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

READ_ONLY = frozenset({
    "roll_dice", "roll_impaling_damage", "roll_weapon_damage", "get_character_sheet",
    "get_combat_status", "search_scenario_images", "search_memory",
    "search_scenario", "report_summary",
})
CHECKS = frozenset({
    "skill_check", "sanity_check", "offer_check_choice", "offer_npc_attack_defense_choice",
})
KP_ALLOWED = frozenset({
    "get_character_sheet", "get_combat_status", "search_memory", "search_scenario",
    "roll_dice", "skill_check", "sanity_check", "offer_check_choice",
    "npc_skill_check", "offer_npc_attack_defense_choice", "clear_pending_check",
    "roll_weapon_damage", "roll_impaling_damage", "apply_combat_damage",
    "apply_final_combat_damage", "add_combat_effect", "search_scenario_images",
    "show_scenario_image", "advance_scenario_chapter", "record_established_fact",
    "record_clue",
})
KP_CANONICAL = frozenset({
    "skill_check", "sanity_check", "offer_check_choice", "npc_skill_check",
    "offer_npc_attack_defense_choice", "clear_pending_check", "roll_weapon_damage",
    "roll_impaling_damage", "apply_combat_damage", "apply_final_combat_damage",
    "add_combat_effect",
})
COMBAT_INVALIDATING = frozenset({
    "start_combat", "add_npc_to_combat", "plan_enemy_turn", "advance_combat_turn",
    "damage_combatant", "resolve_enemy_action", "apply_combat_damage",
    "apply_final_combat_damage", "end_combat",
})
BOUNDED_QUERY = READ_ONLY - {"roll_dice", "roll_weapon_damage", "roll_impaling_damage"}
OPENING = BOUNDED_QUERY | {"send_private_info", "show_scenario_image"}
INFORMATION_QUERY = frozenset({
    "search_scenario", "search_memory", "get_character_sheet", "get_combat_status",
    "search_scenario_images",
})


def test_derived_sets_match_pre_registry_literals() -> None:
    expected = {
        "read_only": READ_ONLY,
        "resolved_check_followup": READ_ONLY | {
            "apply_combat_damage", "apply_final_combat_damage", "advance_combat_turn",
        },
        "kp_assistant": KP_ALLOWED,
        "kp_canonical_game": KP_CANONICAL,
        "invalidates_combat_status": COMBAT_INVALIDATING,
        "bounded_query": BOUNDED_QUERY,
        "creates_check": CHECKS,
        "opening": OPENING,
        "information_query": INFORMATION_QUERY,
    }
    for flag, old_names in expected.items():
        assert registry.names_with(flag) == old_names, flag

    assert keeper.READ_ONLY_TOOL_NAMES == READ_ONLY
    assert keeper.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES == expected["resolved_check_followup"]
    assert keeper._KP_ASSISTANT_ALLOWED_TOOL_NAMES == KP_ALLOWED
    assert keeper._KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES == KP_CANONICAL
    assert keeper._COMBAT_STATUS_INVALIDATING_TOOLS == COMBAT_INVALIDATING
    assert tool_gateway.BOUNDED_QUERY_TOOLS == BOUNDED_QUERY
    assert tool_gateway._CHECK_REGISTRATION_TOOLS == CHECKS
    assert narrator._OPENING_TOOL_NAMES == OPENING
    assert turn_context._CHECK_CREATION_TOOLS == CHECKS
    assert turn_resolution.INFORMATION_QUERY_TOOLS == INFORMATION_QUERY


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
            name for name in (*PLAYER_TOOL_ORDER, "search_scenario") if name in KP_ALLOWED
        ]


def test_check_family_dispatches_without_legacy_cascade() -> None:
    from app.models import GroupState

    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(keeper, "execute_legacy_tool", side_effect=AssertionError("legacy check dispatch"))):
        result = keeper._execute_tool(
            GroupState(group_id="npc-check"), "npc_skill_check", {"skill_value": 100}, [], [],
        )
    assert result["ok"] and result["skill_value"] == 100


def test_dice_family_dispatches_without_legacy_cascade() -> None:
    state = GroupState(group_id="dice-family")
    state.characters["p1"] = Character(name="Investigator", owner_id="p1", occupation="Detective")
    with (patch.object(keeper.mutation_admission, "assert_admitted"),
          patch.object(keeper, "execute_legacy_tool", side_effect=AssertionError("legacy dice dispatch"))):
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
          patch.object(keeper, "execute_legacy_tool", side_effect=AssertionError("legacy character dispatch")),
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
          patch.object(keeper, "execute_legacy_tool", side_effect=AssertionError("legacy inventory dispatch")),
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
