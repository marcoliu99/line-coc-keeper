"""Call of Cthulhu 7th Edition dice mechanics.

Everything here is pure and side-effect free so it can be called directly
from command handlers or wrapped as a tool the Keeper (Claude) invokes.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

_DICE_RE = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)


@dataclass
class RollResult:
    expression: str
    rolls: list[int]
    modifier: int
    total: int

    def describe(self) -> str:
        rolls_str = "+".join(str(r) for r in self.rolls)
        mod_str = f"{self.modifier:+d}" if self.modifier else ""
        return f"{self.expression} = [{rolls_str}]{mod_str} = {self.total}"


def roll_expression(expression: str) -> RollResult:
    """Roll a NdM+K style expression, e.g. '1d100', '3d6+2', 'd4'."""
    m = _DICE_RE.match(expression)
    if not m:
        raise ValueError(f"無法解析骰子表示式: {expression!r}（範例：1d100、3d6+2）")
    n = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    modifier = int(m.group(3).replace(" ", "")) if m.group(3) else 0
    if n < 1 or n > 100:
        raise ValueError("骰子數量必須介於 1 到 100 之間")
    if sides < 2 or sides > 1000:
        raise ValueError("骰子面數必須介於 2 到 1000 之間")
    rolls = [random.randint(1, sides) for _ in range(n)]
    return RollResult(expression=expression, rolls=rolls, modifier=modifier, total=sum(rolls) + modifier)


def d100() -> int:
    """A single percentile roll, 1-100 (00 tens + 0 ones counts as 100)."""
    return random.randint(1, 100)


TIER_RANK = {"fumble": 0, "fail": 1, "regular": 2, "hard": 3, "extreme": 4, "critical": 5}


def resolve_opposed(defender_tier: str, attacker_tier: str) -> str:
    """COC7e opposed-roll resolution (e.g. Dodge/Fight Back vs. an attack):
    compare degree of success. Ties go to the active/attacking side — but if
    *both* sides failed outright, neither effect happens at all (COC7e calls
    this out as its own case, distinct from a tie between two successes).
    Returns one of "defender_wins" (attack negated), "tie_attacker_wins" or
    "attacker_wins" (attack lands), or "both_miss" (neither connects)."""
    d_rank, a_rank = TIER_RANK[defender_tier], TIER_RANK[attacker_tier]
    if d_rank <= TIER_RANK["fail"] and a_rank <= TIER_RANK["fail"]:
        return "both_miss"
    if d_rank > a_rank:
        return "defender_wins"
    if d_rank == a_rank:
        return "tie_attacker_wins"
    return "attacker_wins"


@dataclass
class SkillCheckResult:
    skill_value: int
    roll: int
    bonus_dice: int
    penalty_dice: int
    tier: str  # fumble | fail | regular | hard | extreme | critical
    success: bool

    def describe(self, skill_name: str) -> str:
        tier_zh = {
            "fumble": "大失敗",
            "fail": "失敗",
            "regular": "成功",
            "hard": "困難成功",
            "extreme": "極難成功",
            "critical": "大成功",
        }[self.tier]
        dice_note = ""
        if self.bonus_dice:
            dice_note = f"（獎勵骰 x{self.bonus_dice}）"
        elif self.penalty_dice:
            dice_note = f"（懲罰骰 x{self.penalty_dice}）"
        return f"{skill_name} {self.skill_value}%，擲出 {self.roll}{dice_note} → {tier_zh}"


def roll_percentile_with_dice_pool(bonus_dice: int = 0, penalty_dice: int = 0) -> int:
    """Roll a d100 applying COC7e bonus/penalty dice (net of the two)."""
    net = max(0, bonus_dice) - max(0, penalty_dice)
    ones = random.randint(0, 9)
    tens_options = [random.randint(0, 9)]
    for _ in range(abs(net)):
        tens_options.append(random.randint(0, 9))
    tens = min(tens_options) if net > 0 else max(tens_options) if net < 0 else tens_options[0]
    value = tens * 10 + ones
    return 100 if value == 0 else value


def skill_check(skill_value: int, bonus_dice: int = 0, penalty_dice: int = 0) -> SkillCheckResult:
    """Resolve a COC7e skill/characteristic check against a percentile value."""
    skill_value = max(0, min(100, skill_value))
    roll = roll_percentile_with_dice_pool(bonus_dice, penalty_dice)

    if roll == 1:
        tier = "critical"
    elif roll == 100 or (skill_value < 50 and roll >= 96):
        tier = "fumble"
    elif roll <= skill_value // 5:
        tier = "extreme"
    elif roll <= skill_value // 2:
        tier = "hard"
    elif roll <= skill_value:
        tier = "regular"
    else:
        tier = "fail"

    success = tier in ("critical", "extreme", "hard", "regular")
    return SkillCheckResult(
        skill_value=skill_value,
        roll=roll,
        bonus_dice=bonus_dice,
        penalty_dice=penalty_dice,
        tier=tier,
        success=success,
    )


@dataclass
class SanityCheckResult:
    check: SkillCheckResult
    san_before: int
    san_after: int
    loss: int
    loss_expression: str
    risk_of_madness: bool

    def describe(self) -> str:
        outcome = "通過" if self.check.success else "失敗"
        return (
            f"理智檢定（SAN {self.san_before}）擲出 {self.check.roll} → {outcome}，"
            f"損失 {self.loss} 點理智（{self.san_before} → {self.san_after}）"
        )


def sanity_check(current_san: int, loss_success: str, loss_failure: str) -> SanityCheckResult:
    """Roll SAN vs current_san, then apply the matching loss expression (e.g. '0', '1d4', '1d6')."""
    check = skill_check(current_san)
    loss_expr = loss_success if check.success else loss_failure
    loss_expr = (loss_expr or "0").strip()
    if loss_expr in ("0", ""):
        loss = 0
    elif loss_expr.isdigit():
        loss = int(loss_expr)
    else:
        loss = roll_expression(loss_expr).total
    loss = max(0, loss)
    san_after = max(0, current_san - loss)
    return SanityCheckResult(
        check=check,
        san_before=current_san,
        san_after=san_after,
        loss=loss,
        loss_expression=loss_expr,
        risk_of_madness=loss >= 5,
    )
