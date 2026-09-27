"""Coverage for WP5: a speaker's own unresolved Luck decision is answered from
state, with no model request and without building the turn's context.

Measured before implementing (20-turn live session, see the design spec):
16 of 20 turns resolved await_luck at 2-4 model requests, 36k-86k input
tokens and 10-18 seconds each, holding the conversation lock throughout, to
produce text prompt_config already had."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.agents import supervisor
from app.models import GroupState

LUCK = {
    "skill_name": "偵查", "value": 55, "roll": 69, "original_tier": "failure",
    "difficulty": "regular", "options": [{"tier": "regular", "cost": 14}],
    "decision_id": "decision-1",
}


def _state(**pending):
    state = GroupState(group_id="g")
    state.timeline_id = "timeline-test"
    for owner, decision in pending.get("luck", {}).items():
        state.pending_luck_decisions[owner] = dict(decision)
    for owner, check in pending.get("checks", {}).items():
        state.pending_checks[owner] = dict(check)
    return state


def _run(state, user_id="u1", text="我往樓梯走過去", speaker_role="player", **kwargs):
    """Run a turn with every downstream stage failing loudly if reached."""
    async def explode_context(**_kwargs):
        raise AssertionError("build_context ran")

    async def explode_narrator(_message):
        raise AssertionError("narrator ran")

    with patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
            patch.object(supervisor.context_builder, "build_context", explode_context), \
            patch.object(supervisor.narrator, "run_narrator", explode_narrator), \
            patch.object(supervisor.executor, "run_executor", AsyncMock(
                side_effect=AssertionError("executor ran"))):
        return asyncio.run(supervisor.run_turn(
            state, user_id, "Marco", text, None, speaker_role, "g", **kwargs))


class PendingLuckShortCircuitTests(unittest.TestCase):
    def test_a_held_decision_is_answered_without_running_the_turn(self):
        reply, private, images = _run(_state(luck={"u1": LUCK}))
        self.assertIn("仍等待 Luck 決定", reply)
        self.assertIn("/coc luck skip", reply)
        self.assertIn("/coc luck regular", reply)
        self.assertEqual((private, images), ([], []))

    def test_no_held_decision_runs_the_ordinary_turn(self):
        with self.assertRaises(AssertionError) as caught:
            _run(_state())
        self.assertEqual(str(caught.exception), "build_context ran")

    def test_another_player_is_unaffected(self):
        # The decision belongs to u1; u2 acting must not be answered from it.
        with self.assertRaises(AssertionError) as caught:
            _run(_state(luck={"u1": LUCK}), user_id="u2")
        self.assertEqual(str(caught.exception), "build_context ran")

    def test_a_pending_check_alone_still_reaches_the_executor(self):
        """A check has not been rolled and the player may still withdraw it,
        so short-circuiting one would break the cancelled path."""
        with self.assertRaises(AssertionError) as caught:
            _run(_state(checks={"u1": {"check_id": "c1", "skill": "偵查"}}))
        self.assertEqual(str(caught.exception), "build_context ran")

    def test_someone_else_mid_decision_keeps_the_full_pipeline(self):
        """luck_takes_precedence keys on the waited-for party, not the speaker,
        so a speaker holding Luck may still defer to another player's check."""
        with self.assertRaises(AssertionError) as caught:
            _run(_state(luck={"u1": LUCK}, checks={"u2": {"check_id": "c2"}}))
        self.assertEqual(str(caught.exception), "build_context ran")

    def test_pure_roleplay_is_not_answered_from_state(self):
        # "好" and parenthesised text are the classifier's only non-gameplay
        # inputs; answering them with a Luck prompt would be a wrong reply.
        for text in ("好", "（等我想一下）"):
            with self.subTest(text=text), self.assertRaises(AssertionError) as caught:
                _run(_state(luck={"u1": LUCK}), text=text)
            self.assertEqual(str(caught.exception), "build_context ran")

    def test_kp_assistant_is_not_answered_from_state(self):
        with self.assertRaises(AssertionError) as caught:
            _run(_state(luck={"u1": LUCK}), speaker_role="kp_assistant")
        self.assertEqual(str(caught.exception), "build_context ran")

    def test_a_resolved_check_followup_still_runs(self):
        with self.assertRaises(AssertionError) as caught:
            _run(_state(luck={"u1": LUCK}), turn_kind="resolved_check_followup",
                 resolved_check_context={"investigator": "Marco", "roll": 30})
        self.assertEqual(str(caught.exception), "build_context ran")

    def test_the_short_circuit_is_reported(self):
        with patch.object(supervisor.observability, "event") as event:
            _run(_state(luck={"u1": LUCK}))
        reported = [call for call in event.call_args_list if call.args[0] == "turn.short_circuit"]
        self.assertEqual(len(reported), 1)
        self.assertEqual(reported[0].kwargs["reason"], "pending_luck")


if __name__ == "__main__":
    unittest.main()
