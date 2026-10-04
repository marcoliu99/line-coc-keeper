"""The dice a check may draw, behind a port so a test can script them.

Production uses ``ModuleDice``, which looks the functions up on ``app.dice`` at
call time and forwards the caller's arguments exactly as given, so a test that
patches ``dice.skill_check`` still reaches the engine with the same call shape.
A scripted port feeds chosen d100 values through the same tier classification
(``dice.evaluate_roll``) production uses.
"""
from __future__ import annotations

from typing import Any, Protocol

from app import dice


class DicePort(Protocol):
    def skill_check(self, skill_value: int, **options: Any) -> dice.SkillCheckResult:
        """``options`` may carry ``bonus_dice``, ``penalty_dice`` and ``required_tier``."""
        ...

    def sanity_check(self, current_san: int, loss_success: str, loss_failure: str) -> dice.SanityCheckResult: ...

    def roll_madness(self, realtime: bool = True) -> dict: ...


class ModuleDice:
    """The real dice: every call goes to the current ``app.dice`` attribute."""

    def skill_check(self, skill_value: int, **options: Any) -> dice.SkillCheckResult:
        return dice.skill_check(skill_value, **options)

    def sanity_check(self, current_san: int, loss_success: str, loss_failure: str) -> dice.SanityCheckResult:
        return dice.sanity_check(current_san, loss_success, loss_failure)

    def roll_madness(self, realtime: bool = True) -> dict:
        return dice.roll_madness(realtime=realtime)


DEFAULT_DICE: DicePort = ModuleDice()
