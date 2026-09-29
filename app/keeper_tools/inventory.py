"""Keeper inventory, ammunition, and status-tag handlers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.models import GroupState

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def adjust_ammo(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    weapon = tool_input.get("weapon", "")
    entry = char.weapons.get(weapon)
    if entry is None:
        available = "、".join(char.weapons.keys()) or "（沒有登記彈藥的槍械）"
        return {"ok": False, "error": f"「{char.name}」的彈藥欄位裡沒有「{weapon}」，目前有：{available}"}
    if "ammo_max" not in entry:
        # A recognized weapon whose ammo isn't tracked (melee, or an
        # ammo category this project's table doesn't cover) — see
        # pregen_extractor._resolve_weapon_ammo, which stores these as
        # {}. Without this check, `entry["ammo_max"]` below would
        # KeyError instead of giving the Keeper a usable error.
        return {"ok": False, "error": f"「{weapon}」沒有追蹤彈藥數（近戰武器或未登記彈藥表的槍械），不需要（也無法）裝填。"}
    def _apply_ammo_change(target_state: GroupState) -> None:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        target_entry = target_char.weapons.get(weapon)
        if target_entry is None:
            raise ValueError(f"「{weapon}」的彈藥欄位已不存在，請重新查詢角色資料")
        if tool_input.get("reload_full"):
            target_entry["ammo"] = target_entry["ammo_max"]
        else:
            target_entry["ammo"] = max(0, min(target_entry["ammo_max"], target_entry["ammo"] + int(tool_input.get("delta") or 0)))
    keeper.mutate_tool_state(state, _apply_ammo_change)
    refreshed_char = keeper.require_character(state, tool_input.get("investigator", ""))
    refreshed_entry = refreshed_char.weapons.get(weapon)
    if refreshed_entry is None:
        return {"ok": False, "error": f"「{weapon}」的彈藥欄位已不存在，請重新查詢角色資料"}
    return {"ok": True, "investigator": refreshed_char.name, "weapon": weapon, "ammo": refreshed_entry["ammo"], "ammo_max": refreshed_entry["ammo_max"]}


def add_carried_item(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    item = tool_input.get("item", "").strip()
    if not item:
        return {"ok": False, "error": "item 不能是空字串"}
    def _mutate_add_item(target_state: GroupState) -> Any:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        changed = item not in target_char.carried_items
        if changed:
            target_char.carried_items.append(item)
        return keeper.ToolStateMutation((target_char.name, target_char.carried_items), should_save=changed)
    investigator, carried_items = keeper.mutate_tool_state(state, _mutate_add_item)
    return {"ok": True, "investigator": investigator, "carried_items": carried_items}


def remove_carried_item(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    # .strip() to match add_carried_item's own normalization above — otherwise
    # an item with incidental whitespace ("鑰匙 " vs "鑰匙") would silently fail
    # to remove (the no-op-skip logic below would report "unchanged" since the
    # stripped, stored string never string-equals the unstripped one being removed).
    item = tool_input.get("item", "").strip()
    def _mutate_remove_item(target_state: GroupState) -> Any:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        changed = item in target_char.carried_items
        if changed:
            target_char.carried_items.remove(item)
            target_state.consumed_or_removed_items.append({
                "item": item,
                "character_id": target_char.owner_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source_event_id": tool_input.get("source_event_id") or uuid4().hex,
            })
        return keeper.ToolStateMutation((target_char.name, target_char.carried_items), should_save=changed)
    investigator, carried_items = keeper.mutate_tool_state(state, _mutate_remove_item)
    return {"ok": True, "investigator": investigator, "carried_items": carried_items}


def add_status_tag(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    tag = tool_input.get("tag", "").strip()
    if not tag:
        return {"ok": False, "error": "tag 不能是空字串"}
    def _mutate_add_tag(target_state: GroupState) -> Any:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        changed = tag not in target_char.status_tags
        if changed:
            target_char.status_tags.append(tag)
        return keeper.ToolStateMutation((target_char.name, target_char.status_tags), should_save=changed)
    investigator, tags = keeper.mutate_tool_state(state, _mutate_add_tag)
    return {"ok": True, "investigator": investigator, "status_tags": tags}


def remove_status_tag(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    # .strip() to match add_status_tag's own normalization above — otherwise a
    # tag with incidental whitespace ("昏迷 " vs "昏迷") would silently fail to
    # remove (the no-op-skip logic below would report "unchanged" since the
    # stripped, stored string never string-equals the unstripped one being removed).
    tag = tool_input.get("tag", "").strip()
    def _mutate_remove_tag(target_state: GroupState) -> Any:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        changed = tag in target_char.status_tags
        if changed:
            target_char.status_tags.remove(tag)
        return keeper.ToolStateMutation((target_char.name, target_char.status_tags), should_save=changed)
    investigator, tags = keeper.mutate_tool_state(state, _mutate_remove_tag)
    return {"ok": True, "investigator": investigator, "status_tags": tags}
