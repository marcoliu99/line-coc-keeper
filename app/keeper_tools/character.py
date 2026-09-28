"""Keeper investigator attribute, skill, and sheet handlers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import combat, dice
from app.check_identity import pending_check_blocker
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
    blocked_hit: dict[str, Any] | None = None

    def _apply_attribute_delta(
        target_state: GroupState,
    ) -> Any:
        nonlocal blocked_hit
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        target_cap = getattr(target_char, max_attr) if max_attr else 999
        delta = int(tool_input["delta"])
        new_val = max(0, min(target_cap, getattr(target_char, cur_attr) + delta))
        # COC7e major wound rule, code-enforced the same way Bout of
        # Madness is (see sanity_check above): a single hit dealing >=
        # half of max HP knocks the investigator unconscious unless they
        # pass a CON roll. Skipped when this hit already dropped HP to
        # 0 or below — RAW already treats that as unconscious/dying on
        # its own, so a second CON check on top would be redundant.
        is_major_wound = field_name == "hp" and delta < 0 and new_val > 0 and -delta >= target_char.hp_max / 2
        # Checked against the reloaded state, so a check registered by
        # another path since this turn loaded can't slip past.
        blocker = (
            pending_check_blocker(target_state, target_char.owner_id)
            if is_major_wound and not target_state.autoroll_checks
            else None
        )
        if blocker:
            blocked_hit = combat.major_wound_blocked(
                target_state, target_char, blocker, entry_point="adjust_character"
            )
            return keeper.ToolStateMutation((getattr(target_char, cur_attr), False, None), should_save=False)
        setattr(target_char, cur_attr, new_val)

        major_wound = False
        wound_roll: dict[str, Any] | None = None
        if is_major_wound:
            con_value = keeper.resolve_skill_value(target_char, "CON")
            if target_state.autoroll_checks:
                major_wound = True
                con_result = dice.skill_check(con_value)
                wound_roll = {
                    "skill": "CON",
                    "skill_value": con_value,
                    "roll": con_result.roll,
                    "tier": con_result.tier,
                    "success": con_result.success,
                }
                if not con_result.success:
                    for tag in ("昏迷", "倒地"):
                        if tag not in target_char.status_tags:
                            target_char.status_tags.append(tag)
            else:
                major_wound = True
                target_state.pending_checks[target_char.owner_id] = {
                    "type": "skill",
                    "skill": "CON",
                    "skill_value": con_value,
                    "bonus_dice": 0,
                    "penalty_dice": 0,
                    "difficulty": "regular",
                    "major_wound_trigger": True,
                    **keeper.pending_check_metadata(
                        target_state,
                        target_char.owner_id,
                        {"action_context": f"{target_char.name} 因為重傷需要做 CON 檢定"},
                    ),
                }
        return keeper.ToolStateMutation((new_val, major_wound, wound_roll))

    new_val, major_wound, wound_roll = keeper.mutate_tool_state(state, _apply_attribute_delta)
    if blocked_hit is not None:
        return blocked_hit
    refreshed_char = keeper.require_character(state, tool_input.get("investigator", ""))
    response = {"ok": True, "investigator": refreshed_char.name, "field": field_name, "value": new_val}
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
    return {"ok": True, "sheet": char.to_dict()}
