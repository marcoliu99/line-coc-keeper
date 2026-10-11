"""An attack the engine settles in the call that declares it is told to the player in one fixed line, and a deferral
answered over it is sent back with the right disposition (a 2026-10-11 run: the shot that killed the enemy read
「你的回合尚未到來」; a landed punch read 「拳頭尚未揮出」)."""
from __future__ import annotations

import unittest

from app.agents import executor
from app.domain.models import ObservedOutcome
from app.models import GroupState
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
        # An enemy's armour fields are private and stripped before delivery; the public summary still says it was absorbed.
        armored = turn_delivery.observe_tool("run_combat_action", _receipt(hit=True, damage={
            "target": "幼體", "final_damage": 0, "public_summary": "幼體 受到 0 點傷害（戰鬥暫定）（部分傷害被擋下）"}), 1)
        self.assertIn("全被 幼體 的護甲擋下", armored.public_text)
        nothing = turn_delivery.observe_tool("run_combat_action", _receipt(hit=True, damage={
            "target": "Cultist", "final_damage": 0, "public_summary": "Cultist 受到 0 點傷害（戰鬥暫定）"}), 1)
        self.assertIn("命中，Cultist 受到 0 點傷害", nothing.public_text, "no damage before armour is not credited to armour")
        self.assertNotIn("護甲", nothing.public_text)
        chosen = turn_delivery.observe_tool("submit_combat_choice", _receipt(
            hit=True, opposed="attacker_wins", damage={"target": "Celia", "final_damage": 2}), 1)
        self.assertIn("Celia 受到 2 點傷害", chosen.public_text, "a choice that settled the attack tells it too")
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
        chosen = {"name": "submit_combat_choice", "result": _receipt(hit=False, opposed="both_miss")}
        self.assertTrue(executor._attack_settled([chosen]))


if __name__ == "__main__":
    unittest.main()


class ReplayAndNextDefenceTests(unittest.TestCase):
    def test_a_replayed_receipt_that_changed_nothing_is_kept_internal(self):
        line = ObservedOutcome("tool:1", "declare_combat_action", True, "⚔️ 命中，幼體 受到 9 點傷害。", "public")
        observed = [line]
        executor._silence_replayed_attack(observed, "declare_combat_action", {"round": 2}, {"round": 2})
        self.assertEqual((observed[0].public_text, observed[0].audience), ("", "internal"))
        fresh = [line]
        executor._silence_replayed_attack(fresh, "declare_combat_action", {"round": 2}, {"round": 3})
        self.assertEqual(fresh[0].public_text, line.public_text, "a call that changed the game keeps its line")

    def test_the_check_the_turn_opened_is_found(self):
        state = GroupState(group_id="g")
        self.assertIsNone(executor._opened_check(state, {}))
        state.pending_checks["p2"] = {"check_id": "c2", "type": "choice"}
        self.assertEqual(executor._opened_check(state, {}), ("p2", {"check_id": "c2", "type": "choice"}))
        self.assertIsNone(executor._opened_check(state, {"p2": {"check_id": "c2", "type": "choice"}}), "an old one is not new")
