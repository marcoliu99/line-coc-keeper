"""Regression test for docs/specs/enhancement-conversation-lock-and-tool-
loop-latency.md's item 3 decision: MAX_TOOL_ITERATIONS lowered 8->5 (relying
on PR #55's forced wrap-up so "cut off early" still means real narration),
with HIGH_ITERATION_WATERMARK = 4 (cap - 1) as a separate, lower
observability alarm."""
import unittest


class ToolIterationConfigDefaultsTests(unittest.TestCase):
    def test_max_tool_iterations_default_is_five(self):
        from app import config

        self.assertEqual(config.MAX_TOOL_ITERATIONS, 5)

    def test_high_iteration_watermark_default_is_four(self):
        from app import config

        self.assertEqual(config.HIGH_ITERATION_WATERMARK, 4)

    def test_watermark_is_below_the_hard_cap(self):
        # The whole point of a separate watermark is to fire one iteration
        # before actually hitting the wall - equal to the cap would only
        # ever coincide with PR #55's own wrap-up-triggered log line.
        from app import config

        self.assertLess(config.HIGH_ITERATION_WATERMARK, config.MAX_TOOL_ITERATIONS)


if __name__ == "__main__":
    unittest.main()
