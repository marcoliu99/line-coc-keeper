"""Four replies from the 2026-10-10 soak runs: a Keeper that gave up with evidence in hand, a wait on nobody, a
combat prompt addressed to the wrong player, and a fight opening lost to the warning."""
from __future__ import annotations

import unittest

from app.agents import executor
from app.domain.models import MechanicResult, StateDelta, TurnResolution
from app.models import Combatant, CombatState, GroupState
from app.services import prompt_config, turn_fallback


def _event(name: str, ok: bool = True, **result: object) -> dict:
    return {"name": name, "arguments": {}, "result": {"ok": ok, **result},
            "gameplay_before": {"v": 1}, "gameplay_after": {"v": 1}}


def _result(disposition: str, code: str = "validated", *, tool_calls: tuple = (), actor: str = "", **status) -> MechanicResult:
    return MechanicResult(
        success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
        check_status={"pending": None, "pending_luck": None, **status},
        turn_resolution=TurnResolution(disposition=disposition, validation_code=code, actor_character_id=actor),
        tool_calls=tool_calls, fallback_reason="unsupported_action" if disposition == "blocked" else None,
    )


class GaveUpWithEvidenceTests(unittest.TestCase):
    def test_successful_searches_with_text_qualify(self):
        self.assertTrue(executor._gave_up_with_evidence(
            [_event("search_scenario", results="Harold and Janice look to capture the investigators")]))

    def test_no_text_a_mutation_or_a_refusal_do_not(self):
        self.assertFalse(executor._gave_up_with_evidence([]))
        self.assertFalse(executor._gave_up_with_evidence([_event("search_scenario", results="")]))
        self.assertFalse(executor._gave_up_with_evidence([_event("search_scenario", results="x"), _event("skill_check")]))
        self.assertFalse(executor._gave_up_with_evidence([_event("search_scenario", results="x"), _event("roll_dice", ok=False)]))
        self.assertFalse(executor._gave_up_with_evidence([_event("search_scenario", results="x", complete_for_action=False)]))


class WaitOnNobodyTests(unittest.TestCase):
    def test_an_unverified_wait_with_nothing_pending_is_no_action(self):
        state = GroupState(group_id="g")
        result = _result("incomplete", "deferral_not_verified")
        self.assertEqual(turn_fallback.classify(result, state, "p1"), "executor_no_action")

    def test_a_real_pending_check_or_a_tool_call_keeps_the_pending_reason(self):
        state = GroupState(group_id="g")
        state.pending_checks["p2"] = {"check_id": "c"}
        self.assertEqual(turn_fallback.classify(_result("incomplete", "deferral_not_verified"), state, "p1"),
                         "unresolved_pending_state")
        self.assertEqual(turn_fallback.classify(
            _result("incomplete", "deferral_not_verified", tool_calls=(("search_scenario", True),)), GroupState(group_id="g"), "p1"),
            "unresolved_pending_state")


def _battle() -> GroupState:
    state = GroupState(group_id="g")
    state.combat = CombatState(
        active=True, round_number=1, current_index=0, phase="READY",
        order=[Combatant(name="Julian Price", side="pc", is_pc=True, combatant_id="pc:julian", character_id="legacy-user:p4",
                         hp=12, hp_max=12),
               Combatant(name="Youngling（1）", side="enemy", hp=6, hp_max=6),
               Combatant(name="Youngling（2）", side="enemy", hp=0, hp_max=6, defeated=True)])
    return state


class CombatGuidanceAddresseeTests(unittest.TestCase):
    def test_the_current_actor_is_told_it_is_their_turn_and_who_they_can_hit(self):
        text = turn_fallback.combat_guidance(_battle(), "unsupported_action", "legacy-user:p4")
        self.assertIn("現在輪到你（Julian Price）", text)
        self.assertIn("「Youngling（1）」", text)
        self.assertNotIn("Youngling（2）", text)
        self.assertNotIn("請稍候", text)

    def test_anyone_else_is_still_told_to_wait(self):
        text = turn_fallback.combat_guidance(_battle(), "unsupported_action", "legacy-user:p1")
        self.assertIn("現在輪到「Julian Price」行動", text)
        self.assertIn("請稍候", text)

    def test_the_reply_passes_the_acting_character(self):
        reply = prompt_config.enforce_mechanic_check_consistency(
            "x", _result("blocked", actor="legacy-user:p4"), state=_battle())
        self.assertIn("現在輪到你", reply)


class FightOpeningKeptTests(unittest.TestCase):
    def test_a_fight_opened_this_turn_is_a_transition_not_a_tool_name(self):
        state = _battle()
        opened = [{**_event("add_npc_to_combat"), "combat_active_before": False}]
        self.assertTrue(executor._combat_opened(state, opened))
        already = [{**_event("initialize_combat"), "combat_active_before": True}]
        self.assertFalse(executor._combat_opened(state, already))
        state.combat.active = False
        self.assertFalse(executor._combat_opened(state, opened))

    def test_an_incomplete_turn_that_started_the_fight_keeps_its_narration(self):
        opened = _result("incomplete", "model_incomplete", tool_calls=(("initialize_combat", True), ("sanity_check", True)),
                         state_changed=True, combat_opened=True,
                         pending_luck={"investigator": "George", "roll": 50, "options": []})
        reply = prompt_config.enforce_mechanic_check_consistency("木板後的乾屍睜開眼。", opened, state=GroupState(group_id="g"))
        self.assertTrue(reply.startswith("木板後的乾屍睜開眼。"))
        self.assertIn("Luck", reply)

    def test_the_repair_runs_before_and_after_the_guard_without_doubling(self):
        opened = _result("incomplete", "model_incomplete", tool_calls=(("initialize_combat", True),),
                         state_changed=True, combat_opened=True,
                         pending_luck={"investigator": "George", "roll": 50, "options": []})
        state = GroupState(group_id="g")
        once = prompt_config.enforce_mechanic_check_consistency("乾屍睜開眼。", opened, state=state)
        self.assertEqual(prompt_config.enforce_mechanic_check_consistency(once, opened, state=state), once)
        self.assertEqual(once.count("這次行動尚未完整處理"), 1)
        deferred = _result("deferred", state_changed=True, waiting_for_name="George")
        first = prompt_config.enforce_mechanic_check_consistency("乾屍睜開眼。", deferred)
        self.assertEqual(prompt_config.enforce_mechanic_check_consistency(first, deferred), first)

    def test_an_incomplete_turn_without_a_fight_start_does_not(self):
        plain = _result("incomplete", "model_incomplete", tool_calls=(("search_scenario", True),))
        reply = prompt_config.enforce_mechanic_check_consistency("某段敘事。", plain, state=GroupState(group_id="g"))
        self.assertNotIn("某段敘事", reply)


if __name__ == "__main__":
    unittest.main()
