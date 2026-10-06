"""Keeper inventory, ammunition, and status-tag handlers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app import combat_resources, observability
from app.keeper_tools import operation_ids, resource_bridge, support
from app.models import GroupState

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def adjust_ammo(call: ToolCall) -> dict[str, Any]:

    state = call.state
    tool_input = call.input
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    weapon = tool_input.get("weapon", "")
    char = resource_bridge.effective(state, char)
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
        target_char = support.require_character(target_state, tool_input.get("investigator", ""))
        if resource_bridge.participating(target_state, target_char):
            combat_resources.adjust_ammo(
                target_state, target_char, weapon, int(tool_input.get('delta') or 0),
                event_id=resource_bridge.mutation_id(call.name, tool_input),
                reason=str(tool_input.get('reason') or 'Keeper ammunition adjustment'),
                reload_full=bool(tool_input.get('reload_full')),
            )
            return
        if target_state.combat.active:
            raise ValueError('Active combat lacks admitted resource evidence')
        target_entry = target_char.weapons.get(weapon)
        if target_entry is None:
            raise ValueError(f"「{weapon}」的彈藥欄位已不存在，請重新查詢角色資料")
        if tool_input.get("reload_full"):
            target_entry["ammo"] = target_entry["ammo_max"]
        else:
            target_entry["ammo"] = max(0, min(target_entry["ammo_max"], target_entry["ammo"] + int(tool_input.get("delta") or 0)))
    support.mutate_tool_state(state, _apply_ammo_change)
    refreshed_char = resource_bridge.effective(state, support.require_character(state, tool_input.get("investigator", "")))
    refreshed_entry = refreshed_char.weapons.get(weapon)
    if refreshed_entry is None:
        return {"ok": False, "error": f"「{weapon}」的彈藥欄位已不存在，請重新查詢角色資料"}
    return {"ok": True, "investigator": refreshed_char.name, "weapon": weapon, "ammo": refreshed_entry["ammo"], "ammo_max": refreshed_entry["ammo_max"], "provisional": resource_bridge.participating(state, refreshed_char)}


def add_carried_item(call: ToolCall) -> dict[str, Any]:

    state = call.state
    tool_input = call.input
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    item = tool_input.get("item", "").strip()
    if not item:
        return {"ok": False, "error": "item 不能是空字串"}
    def _mutate_add_item(target_state: GroupState) -> Any:
        target_char = support.require_character(target_state, tool_input.get("investigator", ""))
        changed = item not in target_char.carried_items
        if changed:
            target_char.carried_items.append(item)
        return support.ToolStateMutation((target_char.name, target_char.carried_items), should_save=changed)
    investigator, carried_items = support.mutate_tool_state(state, _mutate_add_item)
    return {"ok": True, "investigator": investigator, "carried_items": carried_items}


def remove_carried_item(call: ToolCall) -> dict[str, Any]:

    state = call.state
    tool_input = call.input
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    # .strip() to match add_carried_item's own normalization above — otherwise
    # an item with incidental whitespace ("鑰匙 " vs "鑰匙") would silently fail
    # to remove (the no-op-skip logic below would report "unchanged" since the
    # stripped, stored string never string-equals the unstripped one being removed).
    item = tool_input.get("item", "").strip()
    def _mutate_remove_item(target_state: GroupState) -> Any:
        target_char = support.require_character(target_state, tool_input.get("investigator", ""))
        changed = item in target_char.carried_items
        if changed:
            target_char.carried_items.remove(item)
            target_state.consumed_or_removed_items.append({
                "item": item,
                "character_id": target_char.owner_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source_event_id": tool_input.get("source_event_id") or uuid4().hex,
            })
        return support.ToolStateMutation((target_char.name, target_char.carried_items), should_save=changed)
    investigator, carried_items = support.mutate_tool_state(state, _mutate_remove_item)
    return {"ok": True, "investigator": investigator, "carried_items": carried_items}


class _TransferRefused(Exception):
    """A hand-off that must write nothing; ``code`` is stable and queryable, the message is for the Keeper."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _same_item(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def _transfer_quantity(raw: Any) -> int:
    if raw is None:
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise _TransferRefused("invalid_quantity", f"quantity 必須是至少為 1 的整數，收到 {raw!r}")
    return raw


def _validated_transfer(state: GroupState, call: ToolCall, item: str, quantity: int) -> tuple[Any, Any, list[str]]:
    """Resolve both ends exactly and check every rule against ``state``; raises ``_TransferRefused`` before any write."""
    tool_input = call.input
    giver, why = support.resolve_active_character_exactly(state, str(tool_input.get("from", "")))
    if giver is None:
        raise _TransferRefused("unknown_giver" if why == "unknown" else "ambiguous_giver",
                               f"找不到給出者「{tool_input.get('from')}」" if why == "unknown" else f"給出者名稱有歧義：{why[10:]}")
    receiver, why = support.resolve_active_character_exactly(state, str(tool_input.get("to", "")))
    if receiver is None:
        raise _TransferRefused("unknown_receiver" if why == "unknown" else "ambiguous_receiver",
                               f"找不到接收者「{tool_input.get('to')}」" if why == "unknown" else f"接收者名稱有歧義：{why[10:]}")
    if giver is receiver:
        raise _TransferRefused("same_character", "給出者與接收者是同一個角色")
    if call.system_origin != "kp_assistant" and (not call.actor_id or giver.owner_id != call.actor_id):
        raise _TransferRefused("not_actors_item", f"只有「{giver.name}」自己的玩家能交出他的物品")
    held = [entry for entry in giver.carried_items if _same_item(entry, item)]
    if len(held) < quantity:
        raise _TransferRefused("item_not_held", f"「{giver.name}」沒有 {quantity} 份「{item}」（持有 {len(held)} 份）")
    return giver, receiver, held[:quantity]


def _character_key(char: Any) -> str:
    return char.character_id or char.owner_id


def _transfer_fingerprint(state: GroupState, call: ToolCall, item: str, quantity: int) -> str:
    """Identifies the operation for replay: server-resolved ids where both ends resolve, normalised text otherwise."""
    ends = []
    for field_name in ("from", "to"):
        raw = str(call.input.get(field_name, ""))
        char, _ = support.resolve_active_character_exactly(state, raw)
        ends.append(_character_key(char) if char is not None else "?" + raw.strip().casefold())
    return json.dumps([*ends, item.strip().casefold(), quantity], ensure_ascii=False)


def transfer_item(call: ToolCall) -> dict[str, Any]:
    """Move ``quantity`` entries from one investigator to another in one committed step, or write nothing.

    The same hand-off re-emitted in the same turn (a lost reply, a provider retry) returns the stored receipt marked
    ``replayed`` and moves nothing more.
    """
    state = call.state
    tool_input = call.input
    item = str(tool_input.get("item", "")).strip()
    if not item:
        return {"ok": False, "error": "item 不能是空字串", "refusal": "empty_item"}

    def refused(exc: _TransferRefused) -> dict[str, Any]:
        observability.event("inventory.transfer.refused", reason=exc.code)
        return {"ok": False, "error": str(exc), "refusal": exc.code}

    try:
        quantity = _transfer_quantity(tool_input.get("quantity"))
        fingerprint = _transfer_fingerprint(state, call, item, quantity)
        operation_id = operation_ids.allocate("transfer", fingerprint)

        def _mutate_transfer(target_state: GroupState) -> dict[str, Any]:
            giver, receiver, moved = _validated_transfer(target_state, call, item, quantity)
            from_before, to_before = list(giver.carried_items), list(receiver.carried_items)
            for entry in moved:
                giver.carried_items.remove(entry)
            receiver.carried_items.extend(moved)
            target_state.inventory_transfers.append({
                "id": uuid4().hex, "operation_id": operation_id or "",
                "turn_id": str(observability.current_context().get("turn_id", "")),
                "from": giver.name, "to": receiver.name, "from_id": _character_key(giver), "to_id": _character_key(receiver),
                "item": moved[0], "moved_items": list(moved), "quantity": quantity,
                "source_event_id": tool_input.get("source_event_id") or "",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            return {
                "ok": True, "from": giver.name, "to": receiver.name,
                "from_id": _character_key(giver), "to_id": _character_key(receiver),
                "item": moved[0], "moved_items": list(moved), "quantity": quantity, "operation_id": operation_id or "",
                "giver_remaining": sum(1 for entry in giver.carried_items if _same_item(entry, item)),
                "from_before": from_before, "to_before": to_before,
                "from_carried_items": list(giver.carried_items), "to_carried_items": list(receiver.carried_items),
            }
        receipt, replayed = support.mutate_tool_state_once(
            state, _mutate_transfer, action_id=operation_id, request_fingerprint=fingerprint)
    except _TransferRefused as exc:
        return refused(exc)
    if replayed:
        return {**receipt, "replayed": True}
    observability.event("inventory.transfer", quantity=quantity)
    return receipt


def add_status_tag(call: ToolCall) -> dict[str, Any]:

    state = call.state
    tool_input = call.input
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    tag = tool_input.get("tag", "").strip()
    if not tag:
        return {"ok": False, "error": "tag 不能是空字串"}
    def _mutate_add_tag(target_state: GroupState) -> Any:
        target_char = support.require_character(target_state, tool_input.get("investigator", ""))
        effective = resource_bridge.effective(target_state, target_char)
        changed = tag not in effective.status_tags
        if resource_bridge.participating(target_state, target_char):
            if changed:
                combat_resources.set_status_tag(
                    target_state, target_char, tag, True,
                    event_id=resource_bridge.mutation_id(call.name, tool_input),
                    reason=str(tool_input.get('reason') or 'Keeper status adjustment'),
                )
            tags = resource_bridge.effective(target_state, target_char).status_tags
            return support.ToolStateMutation((target_char.name, tags), should_save=changed)
        if target_state.combat.active:
            raise ValueError('Active combat lacks admitted resource evidence')
        changed = tag not in target_char.status_tags
        if changed:
            target_char.status_tags.append(tag)
        return support.ToolStateMutation((target_char.name, target_char.status_tags), should_save=changed)
    investigator, tags = support.mutate_tool_state(state, _mutate_add_tag)
    return {"ok": True, "investigator": investigator, "status_tags": tags, "provisional": resource_bridge.participating(state, char)}


def remove_status_tag(call: ToolCall) -> dict[str, Any]:

    state = call.state
    tool_input = call.input
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    # .strip() to match add_status_tag's own normalization above — otherwise a
    # tag with incidental whitespace ("昏迷 " vs "昏迷") would silently fail to
    # remove (the no-op-skip logic below would report "unchanged" since the
    # stripped, stored string never string-equals the unstripped one being removed).
    tag = tool_input.get("tag", "").strip()
    def _mutate_remove_tag(target_state: GroupState) -> Any:
        target_char = support.require_character(target_state, tool_input.get("investigator", ""))
        effective = resource_bridge.effective(target_state, target_char)
        changed = tag in effective.status_tags
        if resource_bridge.participating(target_state, target_char):
            if changed:
                combat_resources.set_status_tag(
                    target_state, target_char, tag, False,
                    event_id=resource_bridge.mutation_id(call.name, tool_input),
                    reason=str(tool_input.get('reason') or 'Keeper status adjustment'),
                )
            tags = resource_bridge.effective(target_state, target_char).status_tags
            return support.ToolStateMutation((target_char.name, tags), should_save=changed)
        if target_state.combat.active:
            raise ValueError('Active combat lacks admitted resource evidence')
        changed = tag in target_char.status_tags
        if changed:
            target_char.status_tags.remove(tag)
        return support.ToolStateMutation((target_char.name, target_char.status_tags), should_save=changed)
    investigator, tags = support.mutate_tool_state(state, _mutate_remove_tag)
    return {"ok": True, "investigator": investigator, "status_tags": tags, "provisional": resource_bridge.participating(state, char)}
