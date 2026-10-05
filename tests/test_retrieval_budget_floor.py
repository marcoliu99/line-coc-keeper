"""The scenario search keeps a minimum budget, and a missing tokenizer no longer triples the cost of Chinese text."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app import config, scenario_retrieval
from app.services import input_budget


class FallbackEstimateTests(unittest.TestCase):
    def test_chinese_costs_a_token_and_a_half_per_character_not_three(self):
        self.assertEqual(input_budget.fallback_tokens("中" * 100), 150)
        self.assertEqual(len(("中" * 100).encode("utf-8")), 300, "what the estimate used to be")

    def test_ascii_costs_a_token_per_three_bytes_rounded_up(self):
        self.assertEqual(input_budget.fallback_tokens("abc"), 1)
        self.assertEqual(input_budget.fallback_tokens("abcd"), 2)
        self.assertEqual(input_budget.fallback_tokens(""), 0)

    def test_mixed_text_adds_the_two(self):
        self.assertEqual(input_budget.fallback_tokens("中文abc"), 3 + 1)

    def test_japanese_korean_and_fullwidth_count_as_wide(self):
        for text in ("ひらがな", "カタカナ", "한국어", "，。！？"):
            with self.subTest(text=text):
                self.assertEqual(input_budget.fallback_tokens(text), (3 * len(text) + 1) // 2)

    def test_it_never_undercounts_a_realistic_one_token_per_character(self):
        sample = "房東低聲說：他像是從跪姿向前倒下的，衣服上的血已經乾了。" * 20
        self.assertGreaterEqual(input_budget.fallback_tokens(sample), len(sample))

    def test_without_a_tokenizer_estimate_uses_it_and_says_so(self):
        with patch.object(input_budget, "_encoding", lambda _model: None):
            self.assertEqual(input_budget.estimate("中" * 10, "any"), 15)
            self.assertEqual(input_budget.tokenizer_method("any"), input_budget.FALLBACK_METHOD)
            self.assertEqual(input_budget.estimate({"k": "中" * 10}, "any"), input_budget.fallback_tokens('{"k":"' + "中" * 10 + '"}'))


class BudgetFloorTests(unittest.TestCase):
    def budget(self, context_tokens: int, **settings) -> tuple[int, dict]:
        defaults = {"SCENARIO_CONTEXT_TOKEN_CEILING": 32000, "SCENARIO_OUTPUT_TOKEN_RESERVE": 4096,
                    "SCENARIO_CONTEXT_SAFETY_TOKENS": 2048, "SCENARIO_RETRIEVAL_TOKEN_BUDGET": 6000,
                    "SCENARIO_RETRIEVAL_MIN_TOKENS": 3000, **settings}
        patches = [patch.object(config, name, value) for name, value in defaults.items()]
        with patch.object(input_budget, "estimate", lambda *_a: context_tokens), \
                patch.object(input_budget, "tokenizer_method", lambda _m: "tokenizer_estimate"), \
                patch.object(scenario_retrieval.observability, "event") as event:
            for p in patches:
                p.start()
            try:
                return scenario_retrieval.remaining_budget("ctx", "m"), event.call_args.kwargs
            finally:
                for p in patches:
                    p.stop()

    def test_plenty_of_room_gives_the_normal_budget_and_no_warning(self):
        budget, fields = self.budget(10000)
        self.assertEqual((budget, fields["budget_floor_applied"], fields["budget_before_floor"]), (6000, False, 6000))

    def test_some_room_gives_what_is_left(self):
        budget, fields = self.budget(23000)  # 32000 - 23000 - 6144 = 2856, under the floor of 3000
        self.assertEqual((budget, fields["budget_floor_applied"], fields["budget_before_floor"]), (3000, True, 2856))
        budget, fields = self.budget(20000)  # 5856 left, above the floor
        self.assertEqual((budget, fields["budget_floor_applied"]), (5856, False))

    def test_a_full_context_still_gets_the_floor_and_the_event_says_the_ceiling_is_too_tight(self):
        budget, fields = self.budget(60000)
        self.assertEqual((budget, fields["budget_floor_applied"], fields["budget_before_floor"]), (3000, True, 0))

    def test_zero_restores_a_budget_that_can_reach_zero(self):
        self.assertEqual(self.budget(60000, SCENARIO_RETRIEVAL_MIN_TOKENS=0)[0], 0)

    def test_the_floor_never_exceeds_the_normal_budget(self):
        self.assertEqual(self.budget(60000, SCENARIO_RETRIEVAL_MIN_TOKENS=9000)[0], 6000)

    def test_a_higher_ceiling_alone_restores_the_full_budget(self):
        """The no-code route: SCENARIO_CONTEXT_TOKEN_CEILING in .env."""
        self.assertEqual(self.budget(60000, SCENARIO_CONTEXT_TOKEN_CEILING=120000)[0], 6000)


class SettingTests(unittest.TestCase):
    def test_the_default_and_the_environment_override(self):
        self.assertEqual(config.SCENARIO_RETRIEVAL_MIN_TOKENS, 3000)
        with patch.dict(os.environ, {"SCENARIO_RETRIEVAL_MIN_TOKENS": "1500"}):
            self.assertEqual(config._env_int("SCENARIO_RETRIEVAL_MIN_TOKENS", 3000), 1500)
        with patch.dict(os.environ, {"SCENARIO_RETRIEVAL_MIN_TOKENS": "-5"}):
            self.assertEqual(config._env_int("SCENARIO_RETRIEVAL_MIN_TOKENS", 3000), 0)


if __name__ == "__main__":
    unittest.main()
