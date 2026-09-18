import sys
import types
import unittest

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault(
    "app.pdf_loader",
    types.SimpleNamespace(
        extract_text=lambda pdf_bytes: ("", [], False, {}, {}),
        guess_title=lambda text, file_name="": file_name or "Untitled",
    ),
)

from app import commands, dice
from app.models import Character


def make_check_result(
    *, roll: int, tier: str, success: bool = True, skill_value: int = 70, required_tier: str = "regular"
) -> dice.SkillCheckResult:
    return dice.SkillCheckResult(
        skill_value=skill_value,
        roll=roll,
        bonus_dice=0,
        penalty_dice=0,
        tier=tier,
        success=success,
        required_tier=required_tier,
    )


class Natural1BonusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.char = Character(name="陳月", owner_id="u1")

    def test_natural_1_appends_bonus_prompt_to_keeper_message_only(self):
        result = make_check_result(roll=1, tier="critical")

        roll_line, keeper_message = commands._build_check_narration(
            self.char, "偵查", None, 70, result, 0, 0
        )

        self.assertIn(commands.NATURAL_1_BONUS_PROMPT, keeper_message)
        self.assertIn("【大成功額外獎勵】", keeper_message)
        self.assertNotIn(commands.NATURAL_1_BONUS_PROMPT, roll_line)
        self.assertNotIn("【大成功額外獎勵】", roll_line)

    def test_non_one_extreme_success_does_not_append_bonus_prompt(self):
        result = make_check_result(roll=2, tier="extreme")

        roll_line, keeper_message = commands._build_check_narration(
            self.char, "偵查", None, 70, result, 0, 0
        )

        self.assertNotIn(commands.NATURAL_1_BONUS_PROMPT, keeper_message)
        self.assertNotIn("【大成功額外獎勵】", keeper_message)
        self.assertNotIn(commands.NATURAL_1_BONUS_PROMPT, roll_line)
        self.assertNotIn("【大成功額外獎勵】", roll_line)

    def test_luck_spend_tier_upgrade_does_not_trigger_natural_1_bonus(self):
        result = make_check_result(roll=52, tier="regular")

        roll_line, keeper_message = commands._build_check_narration(
            self.char,
            "偵查",
            None,
            70,
            result,
            0,
            0,
            luck_spent=5,
            original_tier="fail",
        )

        self.assertIn("花費 5 點 Luck", keeper_message)
        self.assertNotIn(commands.NATURAL_1_BONUS_PROMPT, keeper_message)
        self.assertNotIn("【大成功額外獎勵】", keeper_message)
        self.assertNotIn(commands.NATURAL_1_BONUS_PROMPT, roll_line)

    def test_helper_uses_only_roll_equals_one(self):
        natural_one = make_check_result(roll=1, tier="critical")
        non_one_critical = make_check_result(roll=2, tier="critical")

        self.assertEqual(
            commands._natural_1_bonus_prompt_for_result(natural_one),
            commands.NATURAL_1_BONUS_PROMPT,
        )
        self.assertEqual(commands._natural_1_bonus_prompt_for_result(non_one_critical), "")


if __name__ == "__main__":
    unittest.main()
