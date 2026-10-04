"""Player-facing wording of a settled check: tier names, opposed-roll text, the
roll line and the Keeper's hand-off message.

Pure text. Nothing here rolls dice or changes state; ``rules`` draws the dice and
applies consequences, then asks this module how to say it.
"""
from __future__ import annotations

from app import dice


def skill_names_match(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


# Code review: this used to be its own independently-maintained copy of
# dice.TIER_ZH, and had silently drifted from app/discord_bot.py's copy on
# "regular" ("成功" vs "一般成功"). Now a plain alias to the single source.
CHECK_TIER_ZH = dice.TIER_ZH

NATURAL_1_BONUS_PROMPT = (
    "【大成功額外獎勵】\n"
    "玩家本次 d100 檢定擲出自然 1，取得大成功。除了正常處理這次成功應得到的結果外，"
    "請根據當前劇本、場景與玩家行動，自行給予一個合理、有限的額外 bonus。\n"
    "優先考慮：提升資訊品質、提高效率、避免眼前危險、取得位置／情境優勢，"
    "或其他立即生效且不需要後續追蹤的額外收益。\n"
    "不要因此跳過核心挑戰、直接揭露尚未應該知道的劇本核心秘密、改寫既有劇本事實，"
    "或給予需要在未來回合記住與兌現的延後機械效果。"
)


def tier_zh_for_tier(tier: str, required: str) -> str:
    """Human-readable outcome for one (tier, required_tier) pair, accounting
    for a required difficulty tier (dice.SkillCheckResult.required_tier —
    see keeper.py's skill_check tool's `difficulty` param) higher than the
    roll's own intrinsic tier. COC7e: a task flagged Hard/Extreme needs a
    roll of at least that tier to count as a success at all — a Regular-tier
    roll against a Hard-required task is simply a failure, not a partial
    success, and must be displayed as one rather than misleadingly showing
    "成功" for a check that actually failed."""
    if required == "regular" or tier not in ("regular", "hard"):
        return CHECK_TIER_ZH[tier]
    if dice.TIER_RANK[tier] >= dice.TIER_RANK[required]:
        return CHECK_TIER_ZH[tier]
    required_zh = {"hard": "困難成功", "extreme": "極難成功"}[required]
    return f"失敗（擲骰達到「{CHECK_TIER_ZH[tier]}」，但這次判定需要至少「{required_zh}」）"


def tier_zh_for_result(r) -> str:
    return tier_zh_for_tier(r.tier, getattr(r, "required_tier", "regular"))


def natural_1_bonus_prompt_for_result(r: dice.SkillCheckResult) -> str:
    """Return the Natural 1 bonus prompt only when the original SkillCheckResult.roll is 1.

    This deliberately checks the raw roll instead of tier == "critical", so a
    later Luck-spend tier upgrade cannot be mistaken for a natural 1.
    """
    return NATURAL_1_BONUS_PROMPT if r.roll == 1 else ""


def describe_opposed_outcome(defender_name: str, is_counter: bool, defender_tier: str, attacker_tier: str) -> str:
    """COC7e opposed-roll narration for a Dodge/Fight Back choice (see
    dice.resolve_opposed) — always names both sides' tiers explicitly rather
    than just stating the verdict, so it's auditable in the channel, not a
    black box."""
    outcome = dice.resolve_opposed(defender_tier, attacker_tier, is_counter)
    attacker_zh = CHECK_TIER_ZH[attacker_tier]
    if outcome == "both_miss":
        return f"對抗檢定：攻擊方「{attacker_zh}」，雙方都沒成功，這次攻擊沒有命中，{defender_name}沒有受傷，也沒有造成傷害。"
    if outcome == "defender_wins":
        if is_counter:
            return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級更高，攻擊被化解，反擊命中，可以對攻擊方造成傷害。"
        return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級更高，成功閃避，沒有受到傷害。"
    if outcome == "tie_defender_wins":
        # Only reachable when is_counter is False (Dodge) — resolve_opposed
        # never returns this for a Fight Back choice, where a tie instead
        # favors the attacker (RAW: a tied Dodge protects the fully
        # defensive side, unlike a tied Fight Back).
        return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級與攻擊方打平（平手，依規則閃避方獲勝），成功閃避，沒有受到傷害。"
    tie_note = "（平手，依規則攻擊方獲勝）" if outcome == "tie_attacker_wins" else ""
    counter_note = "，反擊沒有生效" if is_counter else ""
    return f"對抗檢定：攻擊方「{attacker_zh}」，攻擊方成功等級較高{tie_note}，攻擊命中，{defender_name}受到傷害{counter_note}。"


def build_check_narration(
    char, skill_name: str, display_label: str | None, value: int, r, bonus: int, penalty: int,
    luck_spent: int = 0, original_tier: str | None = None, attacker_tier: str | None = None,
    major_wound_trigger: bool = False, ranged_opposed_text: str | None = None, is_counter: bool = False,
) -> tuple[str, str]:
    """Builds (roll_line, keeper_message) for a resolved skill/choice check —
    shared by the immediate-finalize path and handle_luck_decision (after a
    Luck spend has overridden r.tier). luck_spent > 0 adds a note both humans
    and the Keeper can see that the tier was bought up, not rolled naturally.
    attacker_tier (only set for a melee Dodge/Fight Back choice — see
    keeper.py's offer_check_choice/npc_skill_check) triggers the COC7e
    opposed-roll comparison, named explicitly in both messages.
    ranged_opposed_text (only set for a ranged offer_npc_attack_defense_choice
    — see keeper.py's is_ranged branch) is the ALREADY-RESOLVED narration
    from resolve_ranged_defense_outcome, computed once by the caller (never
    computed in here) since that function rolls the attacker's shot as a side
    effect and must not be invoked more than once per resolved defender roll.
    attacker_tier and ranged_opposed_text are mutually exclusive — a given
    choice check is either the melee opposed-roll path or the ranged path,
    never both.
    is_counter (only meaningful when attacker_tier is set) must be computed
    by the caller from the original option dict via dice.is_counter_option()
    — this function no longer re-derives it from display_label's free text
    (code review: a future label wording change could silently break a bare
    "反擊" in display_label substring match here).
    major_wound_trigger (see keeper.py's adjust_character tool) is the CON
    check chained onto a single hit dealing >= half max HP — unlike the Bout
    of Madness INT check this flows through the normal Luck-spend path
    (success here is a plain good outcome), so the status_tags side effect
    on failure has to live here, in the one place both the immediate and the
    Luck-spend-decision paths converge, rather than in an early-return branch."""
    tier_zh = tier_zh_for_result(r)
    dice_note = f"（獎勵骰x{bonus}）" if bonus else f"（懲罰骰x{penalty}）" if penalty else ""
    luck_note = ""
    if luck_spent:
        original_zh = tier_zh_for_tier(original_tier or "regular", getattr(r, "required_tier", "regular"))
        luck_note = f"（花費 {luck_spent} 點 Luck，將結果從「{original_zh}」提升為「{tier_zh}」）"

    opposed_line = ""
    opposed_message = ""
    if attacker_tier is not None:
        opposed_text = describe_opposed_outcome(char.name, is_counter, r.tier, attacker_tier)
        opposed_line = f"\n⚔️ {opposed_text}"
        opposed_message = f"（{opposed_text}）"
    elif ranged_opposed_text:
        opposed_line = f"\n⚔️ {ranged_opposed_text}"
        opposed_message = f"（{ranged_opposed_text}）"

    major_wound_line = ""
    major_wound_message = ""
    if major_wound_trigger:
        if r.success:
            major_wound_line = "\n💪 重傷 CON 檢定通過，勉強撐住意識，沒有昏迷"
            major_wound_message = (
                "（這次「CON」檢定是 COC7e 重傷規則：這次單一傷害達到角色最大 HP 一半以上，本來有"
                "當場昏迷的風險，但檢定通過了，角色勉強撐住意識——請描述角色忍痛維持行動能力的樣子，"
                "這仍然是一次重傷，不要讓角色表現得行動如常。）"
            )
        else:
            major_wound_line = "\n💥 重傷 CON 檢定失敗，角色當場昏迷倒地！"
            major_wound_message = (
                "（這次「CON」檢定是 COC7e 重傷規則：這次單一傷害達到角色最大 HP 一半以上，檢定失敗，"
                "角色當場昏迷倒地——已經加上「昏迷」「倒地」狀態標籤。請描述角色失去意識倒下的過程；"
                "昏迷期間角色沒辦法自主行動或說話，直到有人處理或角色之後自然甦醒，記得呼叫 "
                "remove_status_tag 移除這兩個標籤。）"
            )

    if display_label is not None:
        roll_line = f"🎲 {char.name} 選擇「{display_label}」（{skill_name} {value}%{dice_note}），擲出 {r.roll} → {tier_zh}{luck_note}{opposed_line}{major_wound_line}"
        keeper_message = (
            f"（{char.name} 在多個選項裡選了「{display_label}」，擲骰做了一次「{skill_name}」檢定："
            f"技能值 {value}%{dice_note}，擲出 {r.roll} → {tier_zh}{luck_note}。這是已經確定的結果，請根據這個結果"
            f"描述後續發展，不要重新判定或改變這個結果，也不要質疑玩家選了哪個選項。）{opposed_message}{major_wound_message}"
        )
    else:
        roll_line = f"🎲 {char.name} 的「{skill_name}」檢定：{value}%{dice_note}，擲出 {r.roll} → {tier_zh}{luck_note}{opposed_line}{major_wound_line}"
        keeper_message = (
            f"（{char.name} 擲骰做了一次「{skill_name}」檢定：技能值 {value}%{dice_note}，"
            f"擲出 {r.roll} → {tier_zh}{luck_note}。這是已經確定的結果，請根據這個結果描述後續發展，"
            f"不要重新判定或改變這個結果。）{opposed_message}{major_wound_message}"
        )
    natural_1_bonus_prompt = natural_1_bonus_prompt_for_result(r)
    if natural_1_bonus_prompt:
        keeper_message = f"{keeper_message}\n\n{natural_1_bonus_prompt}"
    return roll_line, keeper_message


def build_split_check_feedback(
    character_name: str,
    check_label: str,
    value_text: str,
    roll: int,
    outcome_text: str,
    opposed_text: str = "",
    result_line: str | None = None,
) -> tuple[str, str]:
    opposed_line = f"\n⚔️ {opposed_text}" if opposed_text else ""
    result_line = result_line or f"{roll} → {outcome_text}"
    roll_feedback_text = (
        f"🎲 {character_name}｜{check_label} {value_text}\n"
        f"{result_line}{opposed_line}\n\n"
        "後續結果由守密人處理中……"
    )
    keeper_header = f"🎭 {character_name}｜{check_label}{outcome_text}"
    return roll_feedback_text, keeper_header
