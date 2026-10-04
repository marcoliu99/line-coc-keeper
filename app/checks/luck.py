"""Who may spend Luck, and the spend itself.

One place states the project's Luck policy, so a command, a button and a tool
cannot disagree about it:

* a Sanity check never offers Luck, and neither does the INT check that decides
  a bout of madness (spending Luck to pass it would push toward the worse
  outcome for the character);
* a Pushed Roll's result is final;
* a Fumble can never be bought off (``app.luck``);
* a check that carries ``allow_luck: False`` (set by the combat engine for
  injury, dying and stabilisation checks) offers none.

Combat attack and defence rolls are *not* in that list: the managed combat flow
has always offered them Luck and an existing test pins it. The architecture
spec describes combat checks as Luck-free; that conflict is recorded in
``docs/refactor/phase2-result.md`` rather than silently changed here.

The tier arithmetic itself is ``app.luck`` (pure). Deducting the points happens
in the caller's state transaction, after the balance is re-read from the latest
state, so a balance spent by another action in between refuses the spend instead
of going negative.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app import luck
from app.dice import TIER_RANK

CheckKind = Literal["skill", "choice", "sanity", "madness", "major_wound"]
LUCK_FREE_KINDS: frozenset[str] = frozenset({"sanity", "madness"})

SpendError = Literal["invalid_option", "insufficient_luck"]


def luck_allowed(kind: CheckKind, *, pushed: bool = False, allow_luck: bool = True) -> bool:
    if kind in LUCK_FREE_KINDS or pushed:
        return False
    return allow_luck


def offer(
    skill_value: int, roll: int, tier: str, luck_balance: int, required_tier: str = "regular",
    *, allowed: bool = True,
) -> list[luck.LuckOption]:
    """Affordable better tiers for a settled roll, cheapest first; empty when not allowed."""
    if not allowed:
        return []
    return luck.buyable_options(skill_value, roll, tier, luck_balance, required_tier)


@dataclass(frozen=True)
class Spend:
    cost: int
    tier: str


def choose(
    options: list[dict[str, Any]], choice: str, luck_balance: int, original_tier: str,
) -> Spend | SpendError:
    """Resolve a player's ``skip`` or tier choice against the stored options.

    ``skip`` keeps the original tier at no cost. Anything else must be one of
    the stored options and affordable *now*.
    """
    if choice == "skip":
        return Spend(0, original_tier)
    option = next((entry for entry in options if entry["tier"] == choice), None)
    if option is None:
        return "invalid_option"
    if luck_balance < option["cost"]:
        return "insufficient_luck"
    return Spend(int(option["cost"]), choice)


def success_at(tier: str, required_tier: str) -> bool:
    return TIER_RANK[tier] >= TIER_RANK[required_tier]
