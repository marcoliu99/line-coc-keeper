"""An attack the engine settles in the call that declares it is told to the player in one fixed line, and a deferral
answered over it is sent back with the right disposition (a 2026-10-11 run: the shot that killed the enemy read
「你的回合尚未到來」; a landed punch read 「拳頭尚未揮出」)."""
from __future__ import annotations

import unittest

from app.agents import executor
from app.services import turn_delivery


def _receipt(**result):
    return {"ok": True, "completed": True, "phase": "READY", "provisional": True, "result": result}


class SettledAttackLineTests(unittest.TestCase):
    def test_a_hit_says_the_target_the_damage_and_the_fall(self):
        outcome = turn_delivery.observe_tool("declare_combat_action", _receipt(
            hit=True, opposed="attacker_wins", damage={"target": "幼體（深海夢魘）", "final_damage": 9, "defeated": True}), 1)
        self.assertEqual(outcome.audience, "public")
        self.assertTrue(outcome.public_text.startswith("⚔️ 命中，幼體（深海夢魘） 受到 9 點傷害，倒下了。"), outcome.public_text)
        standing = turn_delivery.observe_tool("declare_combat_action", _receipt(
            hit=True, opposed="attacker_wins", damage={"target": "深潛者混血", "final_damage": 3, "defeated": False}), 1)
        self.assertIn("深潛者混血 受到 3 點傷害。", standing.public_text)
        self.assertNotIn("倒下", standing.public_text)

    def test_a_miss_armor_counter_and_malfunction_are_said_as_such(self):
        self.assertIn("沒有命中", turn_delivery.observe_tool("declare_combat_action", _receipt(hit=False, opposed="both_miss"), 1).public_text)
        armored = turn_delivery.observe_tool("run_combat_action", _receipt(
            hit=True, damage={"target": "幼體", "final_damage": 0, "armor_label": "鱗片", "armor_reduction": 3}), 1)
        self.assertIn("全被 幼體 的護甲擋下", armored.public_text)
        counter = turn_delivery.observe_tool("declare_combat_action", _receipt(
            hit=True, opposed="defender_wins", damage={"target": "Celia", "final_damage": 2}), 1)
        self.assertIn("對方反擊得手，Celia 受到 2 點傷害", counter.public_text)
        self.assertIn("武器故障", turn_delivery.observe_tool("declare_combat_action", _receipt(hit=False, malfunction=True), 1).public_text)

    def test_an_attack_still_waiting_on_the_roll_says_nothing(self):
        waiting = turn_delivery.observe_tool("declare_combat_action", {"ok": True, "completed": False, "phase": "PLAYER_ROLL"}, 1)
        self.assertEqual(waiting.public_text, "")
        self.assertEqual(waiting.audience, "internal")


class DeferralOverASettledAttackTests(unittest.TestCase):
    def test_a_settled_attack_is_recognised(self):
        settled = {"name": "declare_combat_action", "result": _receipt(hit=True, damage={"target": "x", "final_damage": 1})}
        self.assertTrue(executor._attack_settled([settled]))
        self.assertFalse(executor._attack_settled([{"name": "declare_combat_action", "result": {"ok": True, "completed": False}}]))
        self.assertFalse(executor._attack_settled([{"name": "search_scenario", "result": {"ok": True}}]))


if __name__ == "__main__":
    unittest.main()
