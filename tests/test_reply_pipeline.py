"""The reply steps run in a declared order, and a list that breaks the order is refused."""
from __future__ import annotations

import inspect
import unittest
from dataclasses import replace

from app.agents import reply_pipeline
from app.agents.reply_pipeline import STEPS, Step, validate


def reordered(first: str, second: str) -> list[Step]:
    steps = list(STEPS)
    names = [step.name for step in steps]
    i, j = names.index(first), names.index(second)
    steps[i], steps[j] = steps[j], steps[i]
    return steps


class ReplyPipelineOrderTests(unittest.TestCase):
    def test_the_shipped_order(self):
        self.assertEqual([step.name for step in STEPS], [
            "consistency", "guard", "consistency_after_guard", "obligations", "party_size", "finalize",
            "player_text",
        ])
        validate(STEPS)

    def test_only_the_guard_and_the_obligation_gate_wait(self):
        """A step that starts to await adds a round trip to the player's turn; that must be a visible change."""
        waits = [step.name for step in STEPS if inspect.iscoroutinefunction(step.run)]
        self.assertEqual(waits, ["guard", "obligations"])

    def test_every_rule_names_steps_that_exist(self):
        names = {step.name for step in STEPS}
        for earlier, later, why in reply_pipeline.ORDER_RULES:
            with self.subTest(rule=(earlier, later)):
                self.assertLessEqual({earlier, later}, names)
                self.assertTrue(why)

    def test_each_rule_is_enforced(self):
        for earlier, later, _why in reply_pipeline.ORDER_RULES:
            with self.subTest(rule=(earlier, later)), self.assertRaises(ValueError):
                validate(reordered(earlier, later))

    def test_a_missing_step_is_refused(self):
        with self.assertRaises(ValueError):
            validate([step for step in STEPS if step.name != "party_size"])

    def test_nothing_that_rewrites_text_may_follow_finalize(self):
        late = Step("shorten", lambda ctx, draft: None)
        with self.assertRaises(ValueError) as caught:
            validate([*STEPS, late])
        self.assertIn("shorten", str(caught.exception))

    def test_a_lossless_step_may_follow_finalize(self):
        validate([*STEPS, replace(Step("log", lambda ctx, draft: None), lossless=True)])


if __name__ == "__main__":
    unittest.main()
