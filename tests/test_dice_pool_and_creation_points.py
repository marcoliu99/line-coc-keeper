"""Two rule corrections from the main_v2 review: a bonus die compares whole rolls (00 is 100, the worst), and
character creation hands out CoC 7e skill points (EDU×4, INT×2), not five times that."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app import creation, dice
from app.models import GroupState


class DicePoolTests(unittest.TestCase):
    def test_a_bonus_die_never_turns_a_roll_into_100(self):
        # ones 0, tens 3, bonus tens 0: the tens-0 die reads 100, so the bonus keeps 30.
        with patch("app.dice.random.randint", side_effect=[0, 3, 0]):
            self.assertEqual(dice.roll_percentile_with_dice_pool(bonus_dice=1), 30)

    def test_a_penalty_die_takes_the_100(self):
        with patch("app.dice.random.randint", side_effect=[0, 3, 0]):
            self.assertEqual(dice.roll_percentile_with_dice_pool(penalty_dice=1), 100)

    def test_ordinary_rolls_are_unchanged(self):
        with patch("app.dice.random.randint", side_effect=[7, 2, 5]):
            self.assertEqual(dice.roll_percentile_with_dice_pool(bonus_dice=1), 27)
        with patch("app.dice.random.randint", side_effect=[7, 2, 5]):
            self.assertEqual(dice.roll_percentile_with_dice_pool(penalty_dice=1), 57)
        with patch("app.dice.random.randint", side_effect=[0, 0]):
            self.assertEqual(dice.roll_percentile_with_dice_pool(), 100)


class CreationPointsTests(unittest.TestCase):
    def test_skill_points_follow_the_rulebook(self):
        state = GroupState(group_id="g")
        with patch("app.creation.random.randint", return_value=3):
            session = creation.start_creation(state, "p1", "阿明", None)
        # 2d6×5+30 with every die 3: EDU 60, INT 60.
        self.assertEqual((session.edu, session.int_), (60, 60))
        self.assertEqual(session.occ_points_total, 60 * 4)
        self.assertEqual(session.interest_points_total, 60 * 2)


if __name__ == "__main__":
    unittest.main()
