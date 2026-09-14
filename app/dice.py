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


def _max_value_of_expression(expr: str) -> int:
    """Highest possible value of an NdM+K expression (every die at its top
    face, plus the modifier) — or, for a plain signed integer like a flat
    damage bonus ("-2"/"-1"/"0"), just that integer. Used for COC7e's
    Extreme-success "maximum damage" rule below, which needs a die's
    ceiling, not a random roll of it."""
    expr = (expr or "").strip()
    if not expr:
        return 0
    if re.fullmatch(r"[+-]?\d+", expr):
        return int(expr)
    m = _DICE_RE.match(expr.lstrip("+"))
    if not m:
        raise ValueError(f"無法解析傷害表示式: {expr!r}（範例：1d10+2、+1d4、-1）")
    n = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    modifier = int(m.group(3).replace(" ", "")) if m.group(3) else 0
    return n * sides + modifier


@dataclass
class ImpalingDamageResult:
    weapon_damage_expr: str
    damage_bonus_expr: str
    impaling: bool
    max_weapon_damage: int
    max_damage_bonus: int
    reroll: RollResult | None  # None for a non-impaling weapon — RAW gives no extra roll there
    total: int

    def describe(self) -> str:
        base = f"武器最大傷害 {self.max_weapon_damage}"
        if self.max_damage_bonus:
            base += f"，傷害加值最大 {self.max_damage_bonus}"
        if self.impaling and self.reroll is not None:
            return f"穿刺武器極限成功：{base}，額外重擲武器傷害 {self.reroll.describe()} → 總傷害 {self.total}"
        return f"極限成功（非穿刺武器，不重骰）：{base} → 總傷害 {self.total}"


def calculate_impaling_damage(weapon_damage_expr: str, damage_bonus_expr: str, impaling: bool) -> ImpalingDamageResult:
    """COC7e Extreme-success weapon damage (Keeper Rulebook, 戰鬥／確定攻擊順序
    一節，經官方原文核對過，不是憑印象轉述)：攻擊方擲出 Extreme 成功命中時
    （反擊不適用這條——呼叫端只該在真正的主動攻擊擲骰上用這個函式），傷害
    先算到武器傷害＋傷害加值的最大可能值；如果攻擊武器屬於穿刺武器（刀劍、
    長矛、大多數槍械子彈等），在這個最大值之上，再額外擲一次武器本身的傷害骰
    （不重擲傷害加值）加上去；非穿刺武器（棍棒、拳頭等鈍器）只算最大值，
    不會有這次額外重骰。

    damage_bonus_expr 可以是純數字（COC7e 規則書列出的 "-2"／"-1"／"0"）或骰子
    表示式（"+1d4"／"+1d6" 等，對應體型較大角色的傷害加值），都用
    _max_value_of_expression 算出各自的最大值。"""
    max_weapon = _max_value_of_expression(weapon_damage_expr)
    max_db = _max_value_of_expression(damage_bonus_expr)
    max_base = max_weapon + max_db
    reroll = roll_expression(weapon_damage_expr) if impaling else None
    total = max_base + (reroll.total if reroll is not None else 0)
    return ImpalingDamageResult(
        weapon_damage_expr=weapon_damage_expr,
        damage_bonus_expr=damage_bonus_expr,
        impaling=impaling,
        max_weapon_damage=max_weapon,
        max_damage_bonus=max_db,
        reroll=reroll,
        total=total,
    )


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
    tier: str  # fumble | fail | regular | hard | extreme | critical — the roll's own intrinsic tier
    success: bool  # whether tier meets required_tier (see skill_check's required_tier param)
    required_tier: str = "regular"  # the task's difficulty — RAW: opponent skill/attribute >=50 or a
    # very difficult task needs "hard"; >=90 or near the limit of human ability needs "extreme"

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
        # A roll that hit a nominal success tier (regular/hard) can still be an
        # overall failure if the task demanded a higher tier than that (RAW: a
        # Hard-difficulty task needs at least a Hard-tier roll — a Regular-tier
        # roll against it is simply a failure, not a partial success).
        if not self.success and self.tier in ("regular", "hard"):
            required_zh = {"hard": "困難成功", "extreme": "極難成功"}[self.required_tier]
            return (
                f"{skill_name} {self.skill_value}%，擲出 {self.roll}{dice_note} → "
                f"失敗（達到「{tier_zh}」門檻，但這次判定需要至少「{required_zh}」）"
            )
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


_VALID_REQUIRED_TIERS = ("regular", "hard", "extreme")


def skill_check(
    skill_value: int, bonus_dice: int = 0, penalty_dice: int = 0, required_tier: str = "regular"
) -> SkillCheckResult:
    """Resolve a COC7e skill/characteristic check against a percentile value.

    required_tier (RAW's difficulty-tier rule): a check against a strong
    opponent (skill/attribute >= 50) or an inherently very difficult task
    needs at least a Hard-tier roll to count as a success at all; against an
    opponent >= 90 or a task near the limit of human ability, at least
    Extreme. Defaults to "regular" (the ordinary case — opponent < 50 or a
    standard task), matching every existing caller that doesn't pass this.
    An unrecognized value falls back to "regular" rather than raising, same
    defensive style as the rest of this module."""
    skill_value = max(0, min(100, skill_value))
    if required_tier not in _VALID_REQUIRED_TIERS:
        required_tier = "regular"
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

    success = TIER_RANK[tier] >= TIER_RANK[required_tier]
    return SkillCheckResult(
        skill_value=skill_value,
        roll=roll,
        bonus_dice=bonus_dice,
        penalty_dice=penalty_dice,
        tier=tier,
        success=success,
        required_tier=required_tier,
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
