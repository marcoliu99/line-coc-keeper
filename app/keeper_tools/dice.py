"""Keeper dice and weapon-damage handlers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import dice
from app.keeper_tools import support

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


# A bare percentile roll. A scenario's "roll 1D100 and compare it with the investigator's APP or Credit Rating" is a
# characteristic check: `skill_check` rolls it, grades the tier and lets the player press the button, while a public
# `roll_dice` here left the Keeper with a number no check could settle and the player with a fallback.
PERCENTILE_REFUSAL = (
    "1D100 不用這個工具擲：要和調查員的技能或特徵（外貌、信用評級、幸運、心理學……）比對，就用 skill_check 擲那個技能或"
    "特徵，玩家自己按鈕擲骰、系統判定成功等級；守密人自己要暗擲 1D100 才用 roll_dice，並設 secret: true。"
)


def roll_dice(call: ToolCall) -> dict[str, Any]:
    expression = call.input["expression"]  # required by the schema
    # The KP Assistant rolls for a human Keeper who asked for that number, a random table or a private pick
    # included: its percentile is not a check. The role comes from the turn, never from the model's input, and only
    # a real boolean secret counts (a provider that skipped schema validation may send "false").
    if (isinstance(expression, str) and dice.is_percentile(expression) and call.input.get("secret") is not True
            and call.speaker_role != "kp_assistant"):
        return {"ok": False, "error": PERCENTILE_REFUSAL}
    roll_result = dice.roll_expression(expression)  # anything but a string is refused by the grammar, as before
    return {
        "ok": True, "expression": roll_result.expression, "rolls": roll_result.rolls,
        "modifier": roll_result.modifier, "total": roll_result.total,
        # A Keeper's own roll: the result reaches the Executor only, never the player or the narration.
        **({"secret": True} if call.input.get("secret") else {}),
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

    char = support.find_character(call.state, call.input.get("investigator", ""))
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
