"""Keeper combat tool handlers. Combat rules remain in app.combat."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import combat
from app.models import GroupState

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def start_combat(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state

    def mutate(target_state: GroupState) -> None:
        combat.begin_combat(target_state)

    keeper.mutate_tool_state(state, mutate)
    return {"ok": True, "status": combat.status_text(state)}


def add_npc_to_combat(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    npc_name = tool_input["name"]
    requested_hp = int(tool_input.get("hp", 10))

    def mutate(target_state: GroupState) -> Any:
        hp = requested_hp
        index_note = ""
        # The indexed HP is authoritative for a matching scenario NPC.
        index_entry = keeper.find_npc_index_entry(target_state, npc_name)
        if index_entry is not None and isinstance(index_entry.get("hp"), (int, float)):
            canonical_hp = int(index_entry["hp"])
            if canonical_hp != hp:
                index_note = (
                    f"（系統已依 /coc index 索引修正：你傳入的 HP {hp} 跟索引裡「{index_entry.get('name')}」"
                    f"登記的 HP {canonical_hp} 不一致，已強制改用索引值。這隻的數值以索引為準，"
                    "之後同一隻不要再用別的數字。）"
                )
                hp = canonical_hp
        added = combat.add_combatant(
            target_state,
            npc_name,
            int(tool_input.get("dex", 50)),
            hp,
            is_ally=bool(tool_input.get("is_ally", False)),
            armor=tool_input.get("armor"),
            attacks=tool_input.get("attacks"),
            abilities=tool_input.get("abilities"),
        )
        if added.reused:
            return keeper.ToolStateMutation(
                f"（系統偵測到「{added.combatant.name}」已經在戰鬥中且尚未倒下，沒有重複建立第二份——"
                "這隻怪物的血量與狀態沿用原本那份，之後不要為同一隻怪物再呼叫一次 "
                "add_npc_to_combat。）",
                should_save=False,
            )
        if added.defeated_namesake is not None:
            new = added.combatant
            index_note += (
                f"（{combat.defeated_namesake_notice(added)}"
                f"如果這其實是同一隻，請用 damage_combatant 把「{new.display_name}」的 HP 歸零，"
                "並依原本倒下的狀態敘事。）"
            )
        return keeper.ToolStateMutation(index_note, should_save=True)

    index_note = keeper.mutate_tool_state(state, mutate)
    response = {"ok": True, "status": combat.status_text(state)}
    if index_note:
        response["note"] = index_note
    return response


def initialize_combat(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    enemies = call.input.get("enemies") or []

    def mutate(target_state: GroupState) -> Any:
        combat.begin_combat(target_state)
        results: list[dict[str, Any]] = []
        # Two entries sharing one name in this same array are two distinct
        # individuals (see the spec's decided design constraint), not a
        # duplicate to reuse — but add_combatant's own duplicate check would
        # otherwise treat the second one as "already in this fight" the
        # moment the first is added to target_state. Disambiguate same-batch
        # repeats before calling it, so that check only ever fires for a name
        # that was already in the fight before this call started.
        seen_counts: dict[str, int] = {}
        for entry in enemies:
            # A bad entry is reported, not fatal: the array's other entries
            # still get added, per the spec's decided partial-success design.
            try:
                requested_name = entry["name"]
                seen_counts[requested_name] = seen_counts.get(requested_name, 0) + 1
                occurrence = seen_counts[requested_name]
                npc_name = requested_name if occurrence == 1 else f"{requested_name} ({occurrence})"
                added = combat.add_combatant(
                    target_state, npc_name, int(entry.get("dex", 50)), int(entry.get("hp", 10)),
                    is_ally=bool(entry.get("is_ally", False)),
                    armor=entry.get("armor"), attacks=entry.get("attacks"), abilities=entry.get("abilities"),
                )
            except (KeyError, ValueError, TypeError) as exc:
                results.append({"name": entry.get("name"), "ok": False, "error": str(exc)})
                continue
            results.append({"name": added.combatant.display_name, "ok": True})
        return keeper.ToolStateMutation(results, should_save=True)

    entry_results = keeper.mutate_tool_state(state, mutate)
    return {"ok": True, "status": combat.status_text(state), "enemies": entry_results}


def get_combat_status(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    keeper.refresh_tool_state(call.state)
    return {
        "ok": True,
        "status": combat.status_text(
            call.state, include_private=(call.speaker_role == "kp_assistant")
        ),
    }


def advance_combat_turn(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    def mutate(target_state: GroupState) -> Any:
        return keeper.skip_save_if_blocked(combat.advance_turn(target_state))

    return keeper.mutate_tool_state(call.state, mutate)


def damage_combatant(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        return keeper.skip_save_if_blocked(
            combat.damage_combatant(target_state, tool_input["name"], int(tool_input["delta"]))
        )

    result = keeper.mutate_tool_state(call.state, mutate)
    return keeper.filter_public_combat_damage_result(result, call.speaker_role)


def plan_enemy_turn(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    def mutate(target_state: GroupState) -> Any:
        return keeper.skip_save_if_blocked(
            combat.plan_enemy_turn(target_state, call.input.get("enemy", ""))
        )

    return keeper.mutate_tool_state(call.state, mutate)


def resolve_enemy_action(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        return keeper.skip_save_if_blocked(combat.resolve_enemy_action(
            target_state,
            tool_input["plan_id"],
            outcome=tool_input.get("outcome"),
        ))

    return keeper.mutate_tool_state(call.state, mutate)


def apply_combat_damage(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        return keeper.skip_save_if_blocked(combat.apply_combat_damage(
            target_state,
            tool_input["target"],
            int(tool_input["raw_damage"]),
            damage_type=tool_input.get("damage_type", "physical"),
            tags=tool_input.get("tags") or [],
            source_id=tool_input.get("source_id", ""),
        ))

    result = keeper.mutate_tool_state(call.state, mutate)
    return keeper.filter_public_combat_damage_result(result, call.speaker_role)


def apply_final_combat_damage(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        return keeper.skip_save_if_blocked(combat.apply_final_combat_damage(
            target_state,
            tool_input["target"],
            int(tool_input["final_damage"]),
            damage_type=tool_input.get("damage_type", "physical"),
            tags=tool_input.get("tags") or [],
            source_id=tool_input.get("source_id", ""),
        ))

    result = keeper.mutate_tool_state(call.state, mutate)
    return keeper.filter_public_combat_damage_result(result, call.speaker_role)


def add_combat_effect(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    tool_input = call.input

    def mutate(target_state: GroupState) -> dict[str, Any]:
        return combat.add_combat_effect(
            target_state,
            tool_input["target"],
            tool_input["label"],
            timing=tool_input.get("timing", "turn_start"),
            damage=tool_input.get("damage", ""),
            damage_type=tool_input.get("damage_type", "physical"),
            remaining_rounds=tool_input.get("remaining_rounds"),
            tags=tool_input.get("tags") or [],
            source_id=tool_input.get("source_id", ""),
            public_description=tool_input.get("public_description", ""),
        )

    return keeper.mutate_tool_state(call.state, mutate)


def end_combat(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    def mutate(target_state: GroupState) -> None:
        combat.end_combat(target_state)

    keeper.mutate_tool_state(call.state, mutate)
    return {"ok": True}
