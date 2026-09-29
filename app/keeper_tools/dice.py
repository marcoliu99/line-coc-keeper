"""Keeper dice and weapon-damage handlers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import dice

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def roll_dice(call: ToolCall) -> dict[str, Any]:
    roll_result = dice.roll_expression(call.input["expression"])
    return {
        "ok": True, "expression": roll_result.expression, "rolls": roll_result.rolls,
        "modifier": roll_result.modifier, "total": roll_result.total,
    }


def roll_impaling_damage(call: ToolCall) -> dict[str, Any]:
    try:
        impale_result = dice.calculate_impaling_damage(
            call.input["weapon_damage"], call.input.get("damage_bonus") or "0",
            bool(call.input.get("impaling")),
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "total": impale_result.total,
        "max_weapon_damage": impale_result.max_weapon_damage,
        "max_damage_bonus": impale_result.max_damage_bonus,
        "impaling": impale_result.impaling,
        "reroll_total": impale_result.reroll.total if impale_result.reroll else None,
        "describe": impale_result.describe(),
    }


def roll_weapon_damage(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    char = keeper.find_character(call.state, call.input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{call.input.get('investigator')}」"}
    try:
        weapon_result = dice.roll_weapon_damage(call.input["weapon_damage"], char.damage_bonus)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "investigator": char.name,
        "weapon_damage_roll": weapon_result.weapon_roll.total,
        "damage_bonus": char.damage_bonus,
        "damage_bonus_roll": weapon_result.damage_bonus_total,
        "total": weapon_result.total,
        "describe": weapon_result.describe(),
    }
