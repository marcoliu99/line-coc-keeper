import unittest

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


if __name__ == "__main__":
    unittest.main()
