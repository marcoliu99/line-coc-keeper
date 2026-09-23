import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault(
    "app.pdf_loader",
    types.SimpleNamespace(
        extract_text=lambda pdf_bytes: ("", [], False, {}, {}),
        guess_title=lambda text, file_name="": file_name or "Untitled",
        extract_preview=lambda pdf_bytes: "",
    ),
)

from app import discord_bot


class TierPercentageHintTests(unittest.TestCase):
    """docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md §4.2: the
    %-threshold shown to the player must match COC7e's actual floor-division
    tier thresholds (dice.py's own skill_check uses the same formulas)."""

    def test_regular_threshold_is_the_full_skill_value(self):
        self.assertEqual(discord_bot._tier_percentage_hint("regular", 45), "≤45")

    def test_hard_threshold_is_floor_half(self):
        self.assertEqual(discord_bot._tier_percentage_hint("hard", 45), "≤22")

    def test_extreme_threshold_is_floor_fifth(self):
        self.assertEqual(discord_bot._tier_percentage_hint("extreme", 45), "≤9")

    def test_critical_is_a_fixed_roll_not_a_fraction_of_skill(self):
        self.assertEqual(discord_bot._tier_percentage_hint("critical", 45), "骰出 01")

    def test_floor_division_edge_case_odd_skill_value(self):
        # 47 // 2 = 23, 47 // 5 = 9 — exercises the floor (not round) behavior.
        self.assertEqual(discord_bot._tier_percentage_hint("hard", 47), "≤23")
        self.assertEqual(discord_bot._tier_percentage_hint("extreme", 47), "≤9")

    def test_delegates_to_dice_tier_upper_bound_instead_of_reimplementing_it(self):
        """Code-review regression: this hint used to hardcode
        skill_value//5, skill_value//2, skill_value as its own separate
        copy of dice.py's tier-threshold formula. If a future rule tweak
        changes app/dice.py::tier_upper_bound() without this call site
        picking it up, the hint would silently promise the player a
        different threshold than what the server actually resolves — this
        test would only catch that if the delegation itself is intact, so
        it patches dice.tier_upper_bound with an obviously-wrong stub and
        asserts the hint reflects the stub, proving it isn't computing its
        own independent value."""
        with patch.object(discord_bot.dice, "tier_upper_bound", return_value=999):
            self.assertEqual(discord_bot._tier_percentage_hint("hard", 45), "≤999")


class DefenseChoiceHintTests(unittest.TestCase):
    """docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md §4: Dodge and
    Fight Back need DIFFERENT thresholds against the same attacker_tier — a
    tied Dodge favors the defender (§1), so Dodge only needs to match
    attacker_tier, while a tied Fight Back favors the attacker, so Fight Back
    needs to strictly beat it. This is not the same number with different
    wording; the two options can point at genuinely different tiers."""

    def test_dodge_needs_to_match_not_exceed_attacker_tier(self):
        check = {
            "attacker_tier": "hard",
            "options": [{"label": "閃避", "skill": "閃避", "skill_value": 45}],
        }
        hint = discord_bot._defense_choice_hint(check)
        self.assertIn("困難成功", hint)
        self.assertIn("達到或高於", hint)
        self.assertIn("≤22", hint)

    def test_fight_back_needs_to_strictly_exceed_attacker_tier(self):
        check = {
            "attacker_tier": "hard",
            "options": [{"label": "反擊", "skill": "格鬥", "skill_value": 60}],
        }
        hint = discord_bot._defense_choice_hint(check)
        # hard's next tier up is extreme — Fight Back must reach extreme,
        # not just match hard, since a tied Fight Back favors the attacker.
        self.assertIn("極難成功", hint)
        self.assertIn("高於", hint)
        self.assertNotIn("達到或高於", hint)  # must be the strict "高於", not the Dodge wording
        self.assertIn(f"≤{60 // 5}", hint)

    def test_dodge_and_fight_back_thresholds_differ_for_the_same_attacker_tier(self):
        check = {
            "attacker_tier": "regular",
            "options": [
                {"label": "閃避", "skill": "閃避", "skill_value": 45},
                {"label": "反擊", "skill": "格鬥", "skill_value": 45},
            ],
        }
        hint = discord_bot._defense_choice_hint(check)
        self.assertIn("一般成功", hint)  # Dodge: match "regular"
        self.assertIn("困難成功", hint)  # Fight Back: must beat it, needs "hard"

    def test_no_hint_when_attacker_tier_unknown(self):
        """A ranged choice never has attacker_tier set at button-render time
        (the attacker's shot is deferred — see keeper.py's is_ranged
        branch), so this must return "" rather than crash or fabricate a
        threshold for a tier comparison that doesn't apply to ranged at all."""
        check = {"options": [{"label": "閃避", "skill": "閃避", "skill_value": 45}]}
        self.assertEqual(discord_bot._defense_choice_hint(check), "")

    def test_fight_back_option_omitted_entirely_when_attacker_rolled_critical(self):
        """Defensive fallback: keeper.py's offer_npc_attack_defense_choice
        already filters Fight Back out server-side when attacker_tier is
        Critical (§4.2), so this option should never actually reach here —
        but if it somehow did, the hint builder must skip it rather than
        crash on "no tier beats Critical"."""
        check = {
            "attacker_tier": "critical",
            "options": [{"label": "反擊", "skill": "格鬥", "skill_value": 60}],
        }
        self.assertEqual(discord_bot._defense_choice_hint(check), "")

    def test_fight_back_threshold_clamped_to_regular_when_attacker_fumbled(self):
        """Code-review regression: attacker_rank + 1 for a fumbled attacker
        (rank 0) lands on "fail" (rank 1), which the hint used to present as
        "almost any roll lands the counterattack". But dice.resolve_opposed
        treats BOTH sides being fail-or-worse as both_miss (no hit at all),
        not a defender win — so a Fight Back that only reaches "fail"
        actually still resolves to both_miss, not a successful
        counterattack. The hint must clamp up to "regular", the real
        minimum that avoids both_miss."""
        check = {
            "attacker_tier": "fumble",
            "options": [{"label": "反擊", "skill": "格鬥", "skill_value": 60}],
        }
        hint = discord_bot._defense_choice_hint(check)
        # Only assert on the "選擇「反擊」需要..." clause's own tier, not the
        # whole string — the leading "對方擲出「大失敗」" sentence legitimately
        # contains "失敗" as a substring of "大失敗" and would make a bare
        # assertNotIn("失敗", hint) a false failure.
        self.assertIn("選擇「反擊」需要高於「一般成功」", hint)
        self.assertNotIn("選擇「反擊」需要高於「失敗」", hint)
        self.assertIn(f"≤{60}", hint)

    def test_dodge_threshold_not_clamped_when_attacker_fumbled(self):
        """Dodge doesn't need the both_miss clamp: both both_miss and an
        outright defender win mean "not hit" for a Dodge, so a low
        needed_rank (matching the attacker's own fumble) is still accurate
        — unlike Fight Back, where both_miss means the counterattack didn't
        land."""
        check = {
            "attacker_tier": "fumble",
            "options": [{"label": "閃避", "skill": "閃避", "skill_value": 60}],
        }
        hint = discord_bot._defense_choice_hint(check)
        self.assertIn("大失敗", hint)


if __name__ == "__main__":
    unittest.main()
