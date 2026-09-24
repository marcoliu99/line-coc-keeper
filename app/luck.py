"""COC7e Luck-spend rules engine.

A player may spend Luck points to buy their roll down onto a better success
tier (regular/hard/extreme) after seeing it, provided the roll wasn't already
that good and they can afford the cost. Pure and side-effect free — like
app/dice.py, callers apply the result (deducting Luck, overriding the tier)
themselves; see app/commands.py's _finalize_check_result and
handle_luck_decision.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.dice import TIER_RANK as _TIER_RANK

# Ordered worst-cost-affordable to best, matching dice.skill_check's own tier
# thresholds: regular=skill, hard=skill//2, extreme=skill//5.
_BUYABLE_TIERS = ("regular", "hard", "extreme")


def _threshold(tier: str, skill_value: int) -> int:
    if tier == "regular":
        return skill_value
    if tier == "hard":
        return skill_value // 2
    return skill_value // 5  # extreme


@dataclass
class LuckOption:
    tier: str
    threshold: int
    cost: int


def _candidates(skill_value: int, roll: int, current_tier: str, required_tier: str = "regular") -> list[LuckOption]:
    if current_tier == "fumble":
        return []  # COC7e optional rule: a Fumble can never be bought off with Luck
    current_rank = _TIER_RANK.get(current_tier, 1)
    required_rank = _TIER_RANK.get(required_tier, _TIER_RANK["regular"])
    candidates = []
    for tier in _BUYABLE_TIERS:
        if _TIER_RANK[tier] < required_rank:
            continue  # wouldn't actually meet this check's difficulty (see dice.py's
            # required_tier) even after buying up — pointless to spend Luck reaching it
        threshold = _threshold(tier, skill_value)
        if threshold >= roll:
            continue  # already achieved (or the roll doesn't need buying down to get here)
        if _TIER_RANK[tier] <= current_rank:
            continue  # not actually better than what was already rolled
        candidates.append(LuckOption(tier=tier, threshold=threshold, cost=roll - threshold))
    return candidates


def buyable_options(
    skill_value: int, roll: int, current_tier: str, luck_available: int, required_tier: str = "regular"
) -> list[LuckOption]:
    """Every tier strictly better than current_tier — and, when this check
    has a difficulty requirement above "regular" (see dice.py's
    required_tier), at or above that requirement too, since a tier that
    still wouldn't count as a success isn't a meaningful upgrade — that a
    positive Luck spend would reach, affordable with luck_available —
    cheapest first."""
    candidates = [c for c in _candidates(skill_value, roll, current_tier, required_tier) if c.cost <= luck_available]
    return sorted(candidates, key=lambda c: c.cost)


def cheapest_cost(skill_value: int, roll: int, current_tier: str, required_tier: str = "regular") -> int | None:
    """Min cost among all better-ranked (and difficulty-sufficient) tiers,
    ignoring affordability. Not currently called from app/ — both call
    sites (app/keeper.py, app/legacy_commands.py) used to gate the
    proactive Luck-spend prompt to near-misses (cost <= 7) with this, but
    that gate was removed (see docs/specs/enhancement-luck-buyup-always-
    offered.md); buyable_options alone (which already filters by
    affordability) now decides whether to prompt. Left in place as a
    small, independently-meaningful utility rather than deleted."""
    candidates = _candidates(skill_value, roll, current_tier, required_tier)
    return min((c.cost for c in candidates), default=None)
