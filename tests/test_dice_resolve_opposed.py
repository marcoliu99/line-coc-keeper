import unittest
from unittest.mock import patch

from app import dice


class ResolveOpposedTieRuleTests(unittest.TestCase):
    """docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md §1: a tied
    Fight Back favors the attacker, but a tied Dodge favors the DEFENDER —
    verified against RAW (Chaosium's own combat Q&A + community rules
    breakdowns). The previous implementation always resolved ties in the
    attacker's favor regardless of is_counter, silently misjudging every
    tied Dodge in melee combat as a hit."""

    def test_tied_fight_back_favors_attacker(self):
        self.assertEqual(dice.resolve_opposed("hard", "hard", is_counter=True), "tie_attacker_wins")

    def test_tied_dodge_favors_defender(self):
        self.assertEqual(dice.resolve_opposed("hard", "hard", is_counter=False), "tie_defender_wins")

    def test_tied_critical_dodge_still_favors_defender(self):
        # Both roll Critical — the defender still wins on a Dodge tie, even
        # at the highest tier (this is the scenario that makes Dodge still
        # winnable against a Critical attacker, unlike Fight Back).
        self.assertEqual(dice.resolve_opposed("critical", "critical", is_counter=False), "tie_defender_wins")

    def test_defender_strictly_higher_always_wins_regardless_of_is_counter(self):
        self.assertEqual(dice.resolve_opposed("extreme", "hard", is_counter=True), "defender_wins")
        self.assertEqual(dice.resolve_opposed("extreme", "hard", is_counter=False), "defender_wins")

    def test_attacker_strictly_higher_always_wins_regardless_of_is_counter(self):
        self.assertEqual(dice.resolve_opposed("hard", "extreme", is_counter=True), "attacker_wins")
        self.assertEqual(dice.resolve_opposed("hard", "extreme", is_counter=False), "attacker_wins")

    def test_both_fail_is_both_miss_regardless_of_is_counter(self):
        self.assertEqual(dice.resolve_opposed("fail", "fumble", is_counter=True), "both_miss")
        self.assertEqual(dice.resolve_opposed("fail", "fumble", is_counter=False), "both_miss")


class TierUpperBoundTests(unittest.TestCase):
    """Code-review regression: app/discord_bot.py's player-facing "you need
    <= X%" hint used to hardcode skill_value//5, skill_value//2, skill_value
    as its own separate copy of the exact formula skill_check() below
    resolves a roll against — a future tweak to those fractions could
    silently drift between what the server actually resolves and what the
    hint promises the player. tier_upper_bound() is now the single source
    both read from; this test pins its own correctness, and
    test_discord_defense_hint.py separately pins that the hint actually
    delegates to it rather than reintroducing a hardcoded copy."""

    def test_extreme_is_floor_fifth(self):
        self.assertEqual(dice.tier_upper_bound(45, "extreme"), 9)

    def test_hard_is_floor_half(self):
        self.assertEqual(dice.tier_upper_bound(45, "hard"), 22)

    def test_regular_is_the_full_skill_value(self):
        self.assertEqual(dice.tier_upper_bound(45, "regular"), 45)

    def test_critical_and_fail_and_fumble_have_no_upper_bound(self):
        for tier in ("critical", "fail", "fumble"):
            self.assertIsNone(dice.tier_upper_bound(45, tier))

    def test_matches_skill_check_s_own_tier_boundaries(self):
        """Cross-check against skill_check() itself with the underlying die
        roll pinned via mock — a roll exactly at tier_upper_bound(skill,
        tier) must land in that tier, and one past it must fall to the next
        tier down. This is the regression guard that actually catches drift
        if skill_check()'s inline logic and tier_upper_bound() are ever
        edited independently (a plain call to tier_upper_bound() from
        within skill_check() wouldn't catch a hand-edited literal creeping
        back into one call site but not the other)."""
        for skill_value in (5, 34, 45, 47, 90, 99):
            extreme_bound = dice.tier_upper_bound(skill_value, "extreme")
            hard_bound = dice.tier_upper_bound(skill_value, "hard")
            regular_bound = dice.tier_upper_bound(skill_value, "regular")
            assert extreme_bound is not None and hard_bound is not None and regular_bound is not None

            with patch("app.dice.roll_percentile_with_dice_pool", return_value=regular_bound):
                self.assertEqual(dice.skill_check(skill_value).tier, "regular")
            if regular_bound < 100:
                with patch(
                    "app.dice.roll_percentile_with_dice_pool", return_value=regular_bound + 1
                ):
                    self.assertNotEqual(dice.skill_check(skill_value).tier, "regular")
            if hard_bound >= 2:  # roll=1 is always "critical" regardless of bounds
                with patch("app.dice.roll_percentile_with_dice_pool", return_value=hard_bound):
                    self.assertEqual(dice.skill_check(skill_value).tier, "hard")
            if extreme_bound >= 2:
                with patch("app.dice.roll_percentile_with_dice_pool", return_value=extreme_bound):
                    self.assertEqual(dice.skill_check(skill_value).tier, "extreme")


if __name__ == "__main__":
    unittest.main()
