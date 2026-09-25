import unittest

from app import combat
from app.keeper import _CombatStatusToolGate
from app.models import Combatant, CombatState, GroupState


class CombatStatusToolGateTests(unittest.TestCase):
    def setUp(self):
        self.tools = [
            {"name": "skill_check"},
            {"name": "get_combat_status"},
        ]

    def _active_state(self) -> GroupState:
        state = GroupState(group_id="g")
        state.combat = CombatState(
            active=True,
            round_number=1,
            order=[Combatant(name="Investigator", dex=70, hp=10, hp_max=10, is_pc=True)],
        )
        return state

    def test_withholds_initial_status_lookup_when_snapshot_is_complete(self):
        gate = _CombatStatusToolGate(self._active_state())

        self.assertEqual(gate.tools_for_request(self.tools), [{"name": "skill_check"}])

    def test_keeps_status_lookup_when_combat_snapshot_is_missing(self):
        state = GroupState(group_id="g")
        state.combat = CombatState(active=True, round_number=1)
        gate = _CombatStatusToolGate(state)

        self.assertEqual(gate.tools_for_request(self.tools), self.tools)

    def test_failed_mutation_does_not_reopen_status_tool(self):
        gate = _CombatStatusToolGate(self._active_state())
        gate.observe_tool_result("advance_combat_turn", {"ok": False, "error": "no combat"})

        self.assertEqual(gate.tools_for_request(self.tools), [{"name": "skill_check"}])

    def test_successful_mutation_without_complete_status_reopens_tool(self):
        gate = _CombatStatusToolGate(self._active_state())
        gate.observe_tool_result("advance_combat_turn", {"ok": True, "current_turn": "Cultist"})

        self.assertEqual(gate.tools_for_request(self.tools), self.tools)

    def test_enemy_turn_start_damage_reopens_status_tool(self):
        state = self._active_state()
        combat.add_npc(
            state,
            "Cultist",
            dex=30,
            hp=5,
            attacks=[{"label": "claw", "skill_name": "Fighting", "skill_value": 40, "damage": "1d3"}],
        )
        enemy = next(combatant for combatant in state.combat.order if combatant.name == "Cultist")
        combat.add_combat_effect(state, "Cultist", "burning", timing="turn_start", damage="1")
        gate = _CombatStatusToolGate(state)

        result = combat.plan_enemy_turn(state, "Cultist")

        self.assertTrue(result["ok"])
        self.assertEqual(enemy.hp, 4)
        gate.observe_tool_result("plan_enemy_turn", result)
        self.assertEqual(gate.tools_for_request(self.tools), self.tools)

    def test_successful_mutation_with_complete_status_keeps_tool_withheld(self):
        gate = _CombatStatusToolGate(self._active_state())
        gate.observe_tool_result(
            "add_npc_to_combat", {"ok": True, "status": "戰鬥中 - 第 1 輪\n=> Cultist"}
        )

        self.assertEqual(gate.tools_for_request(self.tools), [{"name": "skill_check"}])

    def test_non_combat_tools_do_not_reopen_status_tool(self):
        gate = _CombatStatusToolGate(self._active_state())
        gate.observe_tool_result("skill_check", {"ok": True, "result": "success"})

        self.assertEqual(gate.tools_for_request(self.tools), [{"name": "skill_check"}])


if __name__ == "__main__":
    unittest.main()
