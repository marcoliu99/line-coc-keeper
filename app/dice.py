"""Call of Cthulhu 7th Edition dice mechanics.

Everything here is pure and side-effect free so it can be called directly
from command handlers or wrapped as a tool the Keeper (Claude) invokes.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

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


@dataclass
class WeaponDamageResult:
    weapon_damage_expr: str
    damage_bonus_expr: str
    weapon_roll: RollResult
    damage_bonus_roll: RollResult | None  # None when damage_bonus_expr is a flat int (e.g. "-1"/"0"), not a dice term
    damage_bonus_total: int
    total: int

    def describe(self) -> str:
        weapon_part = f"武器傷害 {self.weapon_roll.describe()}"
        if self.damage_bonus_total:
            db_part = self.damage_bonus_roll.describe() if self.damage_bonus_roll else str(self.damage_bonus_total)
            return f"{weapon_part}，傷害加值 {db_part} → 總傷害 {self.total}"
        return f"{weapon_part} → 總傷害 {self.total}"


def roll_weapon_damage(weapon_damage_expr: str, damage_bonus_expr: str) -> WeaponDamageResult:
    """A normal (non-Extreme-success) weapon hit's damage: roll the weapon's
    own damage dice, roll the character's damage bonus (DB) if it's a dice
    expression (a flat "-2"/"-1"/"0" needs no roll), and return the correctly
    combined total — the caller never has to add two separate roll results
    together itself, or (worse) try to jam both into one dice expression
    string like "1d8+1d4", which roll_expression's regex can't parse at all
    (it only supports one dice term plus a single flat modifier). This is
    exactly the gap that meant DB only ever got auto-applied on the Extreme-
    success path (calculate_impaling_damage above) and nowhere else — an
    ordinary hit still needed the Keeper to manually splice DB into a
    roll_dice call, which is both the "1d8+1d4" parse failure above and
    real arithmetic for an LLM to get wrong."""
    weapon_roll = roll_expression(weapon_damage_expr)
    db_clean = (damage_bonus_expr or "0").strip()
    if not db_clean or db_clean == "0" or re.fullmatch(r"[+-]?\d+", db_clean):
        damage_bonus_roll = None
        damage_bonus_total = int(db_clean) if db_clean and db_clean != "0" else 0
    else:
        damage_bonus_roll = roll_expression(db_clean.lstrip("+"))
        damage_bonus_total = damage_bonus_roll.total
    return WeaponDamageResult(
        weapon_damage_expr=weapon_damage_expr,
        damage_bonus_expr=damage_bonus_expr,
        weapon_roll=weapon_roll,
        damage_bonus_roll=damage_bonus_roll,
        damage_bonus_total=damage_bonus_total,
        total=weapon_roll.total + damage_bonus_total,
    )


def d100() -> int:
    """A single percentile roll, 1-100 (00 tens + 0 ones counts as 100)."""
    return random.randint(1, 100)


TIER_RANK = {"fumble": 0, "fail": 1, "regular": 2, "hard": 3, "extreme": 4, "critical": 5}

# Single source of truth for tier display text — code review flagged that
# app/legacy_commands.py and app/discord_bot.py each maintained their own
# independent copy of this same tier->Chinese mapping, and the two had
# silently drifted apart on "regular" ("成功" vs "一般成功"). Both call sites
# now import this instead.
TIER_ZH = {
    "fumble": "大失敗", "fail": "失敗", "regular": "一般成功",
    "hard": "困難成功", "extreme": "極難成功", "critical": "大成功",
}


def is_counter_option(option: dict) -> bool:
    """Whether a defense-choice option (offer_npc_attack_defense_choice /
    offer_check_choice's {label, skill, ..., kind?} dict) represents Fight
    Back, as opposed to Dodge.

    Code review flagged that this used to be a bare "反擊" in label
    substring match, independently re-implemented at four call sites across
    app/keeper.py, app/legacy_commands.py and app/discord_bot.py — a future
    change to the Fight Back option's wording (e.g. "反擊！" or an alternate
    phrasing an LLM caller might use) would silently break all four without
    raising anything.

    Prefers the structured "kind" field ("dodge"/"counter") a caller can now
    set explicitly. Falls back to the substring match when "kind" is absent
    — offer_check_choice's options aren't always a Dodge/Fight Back pair (it's
    also used for ordinary multi-choice prompts unrelated to combat), so an
    unset "kind" must not be assumed to mean "not Fight Back"; it means "no
    structured signal was given, fall back to the label"."""
    kind = option.get("kind")
    if kind in ("dodge", "counter"):
        return kind == "counter"
    return "反擊" in str(option.get("label", ""))


def resolve_opposed(defender_tier: str, attacker_tier: str, is_counter: bool) -> str:
    """COC7e opposed-roll resolution for a melee Dodge/Fight Back choice vs.
    an attack: compare degree of success. If *both* sides failed outright,
    neither effect happens at all (COC7e calls this out as its own case,
    distinct from a tie between two successes) — regardless of is_counter.

    A tie between two successes is NOT resolved the same way for both
    defensive choices (verified against RAW, not assumed):
    - Fight Back (is_counter=True): a tie favors the attacker — fighting
      back is inherently offensive ("trading damage"), so on a tie the
      attacker's blow lands first.
    - Dodge (is_counter=False): a tie favors the DEFENDER — dodging is a
      fully committed defensive act (the character gives up any chance to
      hurt the attacker to focus entirely on getting out of the way), so
      RAW protects the purely defensive side on a tie. This is the bug this
      is_counter parameter was added to fix: the previous version always
      resolved ties in the attacker's favor regardless of which defensive
      choice the player made, silently misjudging every tied Dodge as a hit.

    Returns one of "defender_wins" (attack negated), "tie_defender_wins"
    (attack negated on a tied Dodge), "tie_attacker_wins" or "attacker_wins"
    (attack lands), or "both_miss" (neither connects)."""
    d_rank, a_rank = TIER_RANK[defender_tier], TIER_RANK[attacker_tier]
    if d_rank <= TIER_RANK["fail"] and a_rank <= TIER_RANK["fail"]:
        return "both_miss"
    if d_rank > a_rank:
        return "defender_wins"
    if d_rank == a_rank:
        return "tie_attacker_wins" if is_counter else "tie_defender_wins"
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


def tier_upper_bound(skill_value: int, tier: str) -> int | None:
    """The maximum roll (inclusive) that lands at least the given success
    tier, for the tiers where "roll <= X" is a meaningful target to aim for
    (extreme/hard/regular). Returns None for "critical" (a fixed "roll 01",
    not a skill-value-derived fraction) and "fail"/"fumble" (there's no
    upper-bound worth aiming for — any non-fumble roll already clears "at
    least fail").

    Single source of truth for the thresholds skill_check() below resolves a
    roll against — code review flagged that app/discord_bot.py's player-
    facing "you need <= X%" hint used to hardcode this same skill_value//5,
    skill_value//2, skill_value formula as its own separate copy, so a
    future tweak to these fractions could silently drift between what the
    server actually resolves and what the hint promises the player."""
    if tier == "extreme":
        return skill_value // 5
    if tier == "hard":
        return skill_value // 2
    if tier == "regular":
        return skill_value
    return None


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

    extreme_bound = tier_upper_bound(skill_value, "extreme")
    hard_bound = tier_upper_bound(skill_value, "hard")
    regular_bound = tier_upper_bound(skill_value, "regular")
    assert extreme_bound is not None and hard_bound is not None and regular_bound is not None, (
        "tier_upper_bound only returns None for tiers other than "
        "extreme/hard/regular — these three literals always resolve to an int"
    )

    if roll == 1:
        tier = "critical"
    elif roll == 100 or (skill_value < 50 and roll >= 96):
        tier = "fumble"
    elif roll <= extreme_bound:
        tier = "extreme"
    elif roll <= hard_bound:
        tier = "hard"
    elif roll <= regular_bound:
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


# COC7e Bout of Madness (短暫瘋狂) tables — RAW: losing 5+ SAN in one go
# (SanityCheckResult.risk_of_madness above) triggers an INT check; *succeeding*
# means the character truly grasps the horror and suffers an immediate Bout of
# Madness (roll here), *failing* means they repress it and nothing happens —
# easy to get backwards, since a passed check usually means "good outcome"
# everywhere else in this project. app/keeper.py and app/legacy_commands.py
# either register that INT check for the player or resolve it immediately,
# according to the group's autoroll mode.
#
# Table content is paraphrased (Keeper guidance in our own words, not a
# verbatim rulebook quote) from the same coc-kp-host reference this project
# already cites for other RAW gaps (docs/references/rules_reference.md) —
# https://github.com/SumanasJ/coc-kp-host/blob/main/references/rules_reference.md
# — itself already a condensed paraphrase of the official tables, not the
# copyrighted rulebook text. Real-time (Table VII) is for a Bout of Madness
# triggered mid-scene, lasting roughly 1D10 combat rounds; summary (Table
# VIII) is for one triggered during a stretch of narrated-in-summary time,
# lasting roughly 1D10 hours. This project always uses the real-time table
# (see roll_madness's own docstring for why) — the summary table is here for
# completeness in case a caller ever needs to distinguish the two.
MADNESS_TABLE_REALTIME: dict[int, tuple[str, str]] = {
    1: ("失憶", "角色搞不清楚自己身在何處、剛剛發生了什麼事，行為顯得困惑茫然。"),
    2: ("偽裝殘疾", "角色深信自己失去了某種感官或肢體（失明、失聰、肢體麻痺等），演出對應的恐慌與行動障礙。"),
    3: ("暴力衝動", "角色不分對象地攻擊身邊的人事物，可能對隊友或周遭物品出手。"),
    4: ("偏執", "角色認定周遭的人都是威脅，表現得多疑、防衛心重、動輒指控他人。"),
    5: ("情感依附", "角色把在場某個人當成救命稻草般的重要人物，緊黏著對方、對其言聽計從。"),
    6: ("昏厥", "角色當場昏倒失去意識，過一段時間後才甦醒。"),
    7: ("逃避衝動", "角色感到一股強烈的衝動想逃離現場，會不顧一切拔腿就跑。"),
    8: ("歇斯底里", "角色情緒失控——可能突然大哭、狂笑，或語無倫次地咆哮。"),
    9: ("恐懼症", "角色對某個具體事物產生壓倒性的恐懼——可以自己指定一個符合情境的合理恐懼對象。"),
    10: ("狂躁症", "角色陷入某種偏執而亢奮的執念行為，情緒亢奮、被這股衝動驅使——可以自己指定一個符合情境的合理執念。"),
}

MADNESS_TABLE_SUMMARY: dict[int, tuple[str, str]] = {
    1: ("失憶", "角色醒來時，對這段失常期間發生的事完全沒有記憶。"),
    2: ("失竊", "角色的貴重物品在這段期間遺失了（可以視情況判斷是被偷、弄丟，或直接沒收）。"),
    3: ("自傷", "角色在這段期間傷害了自己，HP 降到最大值的一半（不算重傷）。"),
    4: ("暴力失控", "角色在這段期間捲入了打鬥，可以描述造成的附帶損害與傷勢。"),
    5: ("偏激信念", "角色在失常期間形成了一個偏激而固執的信念，清醒後依然堅信不疑。"),
    6: ("尋找重要之人", "角色在失常期間不顧一切地跑去找某個對自己重要的人，可能鬧出了一些場面。"),
    7: ("被收容", "角色在失常期間被人發現並帶去收容——精神病院、拘留所或醫院。"),
    8: ("逃亡在外", "角色在失常期間逃離現場，醒來時發現自己身處一個離原本地點很遠的地方。"),
    9: ("新恐懼症", "角色從這次失常經驗中，留下了一個新的、長期的具體恐懼——可以自己指定一個符合情境的合理恐懼對象。"),
    10: ("新狂躁症", "角色從這次失常經驗中，留下了一個新的、長期的強迫性偏執行為——可以自己指定一個符合情境的合理執念。"),
}


def roll_madness(realtime: bool = True) -> dict:
    """Rolls 1D10 on the Bout of Madness table and returns
    {"roll": int, "symptom": str, "guidance": str, "duration": str}. Always
    realtime=True in this project for now — distinguishing "did this trigger
    mid-scene vs. during a summarized stretch of time" isn't something this
    bot tracks anywhere, so rather than guess, every trigger uses the
    real-time table (roughly 1D10 rounds) and leaves it to the Keeper's own
    narration to stretch that out if the fictional moment calls for longer."""
    table = MADNESS_TABLE_REALTIME if realtime else MADNESS_TABLE_SUMMARY
    roll = random.randint(1, 10)
    symptom, guidance = table[roll]
    duration = "1D10 個戰鬥輪" if realtime else "1D10 小時"
    return {"roll": roll, "symptom": symptom, "guidance": guidance, "duration": duration}
