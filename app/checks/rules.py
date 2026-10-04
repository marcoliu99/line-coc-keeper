"""The dice-and-arithmetic half of a check, with the dice behind a port.

Functions here take the values a check needs and a ``DicePort`` and return what
happened; none reads or writes ``GroupState``. ``service`` applies the results.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import dice
from app.checks import luck as luck_policy
from app.checks import narration
from app.checks.dice_port import DEFAULT_DICE, DicePort
from app.luck import LuckOption


@dataclass
class RolledSkill:
    """A d100 against a target, and the Luck options that roll leaves open."""

    result: dice.SkillCheckResult
    luck_options: list[LuckOption]


def roll_skill(
    port: DicePort, *, skill_value: int, bonus_dice: int, penalty_dice: int, difficulty: str,
    luck_balance: int, luck_allowed: bool,
) -> RolledSkill:
    result = port.skill_check(
        skill_value, bonus_dice=bonus_dice, penalty_dice=penalty_dice, required_tier=difficulty,
    )
    options = luck_policy.offer(
        skill_value, result.roll, result.tier, luck_balance, difficulty, allowed=luck_allowed,
    )
    return RolledSkill(result, options)


def settle_with_luck(pending: dict[str, Any], tier: str) -> dice.SkillCheckResult:
    """The roll a Luck decision settles on: the stored roll at the chosen tier."""
    required = pending.get("difficulty", "regular")
    return dice.SkillCheckResult(
        skill_value=pending["value"], roll=pending["roll"], bonus_dice=pending["bonus_dice"],
        penalty_dice=pending["penalty_dice"], tier=tier,
        success=luck_policy.success_at(tier, required), required_tier=required,
    )


def resolve_ranged_defense_outcome(
    defender_name: str, dive_success: bool, ranged_attacker: dict[str, int], port: DicePort = DEFAULT_DICE,
) -> str:
    """COC7e ranged-attack resolution — deliberately NOT dice.resolve_opposed
    (that function is for melee Dodge/Fight Back only; verified against RAW,
    see docs/specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md). A ranged
    attack is never an opposed roll: the defender's only option is diving for
    cover, an independent Dodge check that — if successful — gives the
    attacker's shot one penalty die but does not by itself stop the shot.
    The attacker's own roll alone (<=skill value, no Push allowed on a
    firearm attack) determines whether it connects.

    Rolls the attacker's shot exactly once — callers must call this exactly
    once per resolved defender roll (not once per narration message) and
    reuse the returned text everywhere it's needed, or the attacker would be
    rolled twice with potentially different results for the same turn."""
    penalty = ranged_attacker["penalty_dice"] + (1 if dive_success else 0)
    attacker_result = port.skill_check(
        ranged_attacker["skill_value"], bonus_dice=ranged_attacker["bonus_dice"], penalty_dice=penalty
    )
    attacker_zh = narration.CHECK_TIER_ZH[attacker_result.tier]
    dive_text = (
        "撲向掩體成功，這次射擊被迫多承受 1 個懲罰骰" if dive_success
        else "撲向掩體失敗，沒有讓攻擊方受到任何懲罰"
    )
    if attacker_result.success:
        return f"遠程攻擊判定：{defender_name}{dive_text}；攻擊方仍然擲出「{attacker_zh}」，命中了，{defender_name}受到傷害。"
    return f"遠程攻擊判定：{defender_name}{dive_text}；攻擊方擲出「{attacker_zh}」，沒有命中，{defender_name}沒有受到傷害。"


def apply_major_wound_failure(char: Any) -> None:
    """A failed major-wound CON check leaves the investigator unconscious and prone."""
    for tag in ("昏迷", "倒地"):
        if tag not in char.status_tags:
            char.status_tags.append(tag)
