"""A scripted ``DicePort`` for the check engine's tests.

It returns the d100 values a test chose, classified by the same
``dice.evaluate_roll`` production uses, and refuses to be asked for a roll the
test did not script — so "this path must not roll" and "this path rolls exactly
once" are assertions, not hopes.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from app import dice


class ScriptedDice:
    def __init__(
        self, rolls: tuple[int, ...] | list[int] = (), *, san_losses: tuple[int, ...] | list[int] = (),
        madness: tuple[int, ...] | list[int] = (),
    ) -> None:
        self._rolls = list(rolls)
        self._san_losses = list(san_losses)
        self._madness = list(madness)
        self._lock = threading.Lock()
        self.skill_calls: list[tuple[int, dict[str, Any]]] = []
        self.madness_calls = 0

    def _take(self, queue: list[int], label: str) -> int:
        with self._lock:
            if not queue:
                raise AssertionError(f"{label} was rolled but the test scripted no more values")
            return queue.pop(0)

    @property
    def rolls_taken(self) -> int:
        return len(self.skill_calls)

    def skill_check(self, skill_value: int, **options: Any) -> dice.SkillCheckResult:
        roll = self._take(self._rolls, "skill_check")
        self.skill_calls.append((skill_value, dict(options)))
        return dice.evaluate_roll(
            skill_value, roll,
            bonus_dice=options.get("bonus_dice", 0), penalty_dice=options.get("penalty_dice", 0),
            required_tier=options.get("required_tier", "regular"),
        )

    def sanity_check(self, current_san: int, loss_success: str, loss_failure: str) -> dice.SanityCheckResult:
        check = self.skill_check(current_san)
        loss = self._take(self._san_losses, "sanity loss")
        return dice.SanityCheckResult(
            check=check, san_before=current_san, san_after=max(0, current_san - loss), loss=loss,
            loss_expression=str(loss), risk_of_madness=loss >= 5,
        )

    def roll_madness(self, realtime: bool = True) -> dict:
        roll = self._take(self._madness, "madness table")
        self.madness_calls += 1
        symptom, guidance = dice.MADNESS_TABLE_REALTIME[roll]
        return {"roll": roll, "symptom": symptom, "guidance": guidance, "duration": "1D10 個戰鬥輪"}


@contextmanager
def module_dice(script: ScriptedDice) -> Iterator[ScriptedDice]:
    """Route every ``app.dice`` roll through ``script``, for doors that take no dice port."""
    with (
        patch("app.dice.skill_check", side_effect=script.skill_check),
        patch("app.dice.sanity_check", side_effect=script.sanity_check),
        patch("app.dice.roll_madness", side_effect=script.roll_madness),
    ):
        yield script
