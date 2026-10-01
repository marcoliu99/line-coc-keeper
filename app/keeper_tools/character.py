"""Keeper investigator attribute, skill, and sheet handlers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.keeper_tools import resource_bridge
from app.models import GroupState

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def adjust_character(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    field_name = tool_input["field"]
    attr_map = {"hp": ("hp", "hp_max"), "mp": ("mp", "mp_max"), "san": ("san", "san_max"), "luck": ("luck", None)}
    if field_name not in attr_map:
        return {"ok": False, "error": "field 必須是 hp/mp/san/luck 其中之一"}
    cur_attr, max_attr = attr_map[field_name]
    new_val, major_wound, wound_roll, blocked_hit = keeper.apply_character_attribute_delta(
        state, tool_input, field_name, cur_attr, max_attr
    )
    if blocked_hit is not None:
        return blocked_hit
    refreshed_char = keeper.require_character(state, tool_input.get("investigator", ""))
    response = {"ok": True, "investigator": refreshed_char.name, "field": field_name, "value": new_val,
                "provisional": resource_bridge.participating(state, refreshed_char)}
    if major_wound:
        response["major_wound"] = True
        response["major_wound_check"] = wound_roll
        response["note"] = (
            "這次單一傷害達到重傷門檻（≥ 角色最大 HP 一半），COC7e 規則：角色必須做一次 CON 檢定；"
            "已替玩家建立待處理的 CON 檢定，請等待玩家輸入 /coc check CON。"
            if not state.autoroll_checks
            else "這次單一傷害達到重傷門檻；autoroll 已開啟，CON 檢定已由系統完成，請照 major_wound_check 敘事。"
        )
    return response


def set_skill(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    value = max(0, min(100, int(tool_input["value"])))
    def _mutate_set_skill(target_state: GroupState) -> None:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        target_char.skills[tool_input["skill"]] = value
    keeper.mutate_tool_state(state, _mutate_set_skill)
    refreshed_char = keeper.require_character(state, tool_input.get("investigator", ""))
    return {"ok": True, "investigator": refreshed_char.name, "skill": tool_input["skill"], "value": value}


def get_character_sheet(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    state = call.state
    tool_input = call.input
    keeper.refresh_tool_state(state)
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    return {"ok": True, "sheet": resource_bridge.effective(state, char).to_dict(),
            "provisional": resource_bridge.participating(state, char)}
