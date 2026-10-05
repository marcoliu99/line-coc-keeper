"""The order of awaited steps inside ``supervisor.run_turn``.

Splitting ``run_turn`` into stages (prepare, mechanics, narrate, gate, commit) and the reply steps into
``reply_pipeline`` is meant to change structure only. This pins what a reader of the old code could see: which
model calls happen, in which order, around which lock hand-off, and that nothing is awaited twice or added.
The expected sequences were recorded against the pre-split ``run_turn``.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app import config, presentation
from app.agents import assistant, context_builder, executor, guard, narrator, obligation_gate, supervisor
from app.domain.models import AgentMessage, MechanicResult, StateDelta
from app.models import Character, GroupState
from app.services import prompt_config, turn_delivery

CONFLICT = "（這次回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）"


class SpyHandoff:
    def __init__(self, events):
        self.events = events

    async def to_narration(self):
        self.events.append("handoff.to_narration")


def make_state():
    s = GroupState(group_id="g", active=True, game_started=True)
    s.timeline_id = "t"
    s.characters["u1"] = Character(name="Marco", owner_id="u1")
    return s


async def run(state, *, turn_kind="player_action", text="我推開門", commit=True, owed=None, candidates=False,
              narration_failed=False, extra=None):
    """Run one turn with every collaborator replaced by a recorder; return (events, result)."""
    events: list[str] = []

    async def build_context(**kwargs):
        events.append("build_context")
        return AgentMessage(dict(kwargs, state=state, character=state.characters["u1"], rag_context="",
                                 memory_context=""))

    async def run_executor(message):
        events.append("executor")
        return MechanicResult(success=True, action_type="none", narrative_facts=[], state_delta=StateDelta())

    async def run_narrator(message):
        events.append("narrator")
        if narration_failed:
            message.payload["narration_failed"] = True
        return "敘事", [], []

    async def run_assistant(message):
        events.append("assistant")
        return "助手", [], []

    async def enforce_narrative_safety(message, reply):
        events.append("guard")
        return reply

    def possible(evidence, mechanic_result):
        events.append("obligation.possible")
        return candidates

    async def enforce(*args, **kwargs):
        events.append("obligation.enforce")
        return owed or []

    def finalize(message, reply):
        events.append("finalize")
        return reply, []

    def party(reply, count):
        events.append("party_size")
        return reply

    def resolved_consistency(reply, context, *, new_pending_check):
        events.append("consistent.resolved")
        return reply

    def mechanic_consistency(reply, public_result):
        events.append("consistent.mechanic")
        return reply

    def commit_turn_result(*args, **kwargs):
        events.append("commit")
        return commit

    with patch.object(config, "NARRATION_OUTSIDE_MUTATION_LOCK", True), \
            patch.object(supervisor.turn_commit, "ensure_turn_timeline", return_value="t"), \
            patch.object(context_builder, "build_context", build_context), \
            patch.object(executor, "run_executor", run_executor), \
            patch.object(narrator, "run_narrator", run_narrator), \
            patch.object(assistant, "run_assistant", run_assistant), \
            patch.object(guard, "enforce_narrative_safety", enforce_narrative_safety), \
            patch.object(obligation_gate, "possible", possible), \
            patch.object(obligation_gate, "enforce", enforce), \
            patch.object(turn_delivery, "finalize", finalize), \
            patch.object(presentation, "enforce_party_size", party), \
            patch.object(prompt_config, "enforce_resolved_check_consistency", resolved_consistency), \
            patch.object(prompt_config, "enforce_mechanic_check_consistency", mechanic_consistency), \
            patch.object(supervisor.turn_commit, "commit_turn_result", commit_turn_result):
        result = await supervisor.run_turn(
            state, "u1", "Marco", text, None, extra.get("speaker_role", "player") if extra else "player", "g",
            turn_kind=turn_kind, handoff=SpyHandoff(events),
            **({"resolved_check_context": {"investigator": "Marco", "roll": 30, "outcome": "成功"}}
               if turn_kind == "resolved_check_followup" else {}),
        )
    return events, result


class RunTurnSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_ordinary_turn(self):
        events, result = await run(make_state())
        self.assertEqual(events, [
            "build_context", "executor", "obligation.possible", "handoff.to_narration", "narrator",
            "consistent.mechanic", "guard", "consistent.mechanic", "party_size", "finalize", "commit",
        ])
        self.assertEqual(result[0], "敘事")

    async def test_a_turn_with_an_obligation_candidate_keeps_the_mutation_lock(self):
        events, result = await run(make_state(), candidates=True)
        self.assertEqual(events, [
            "build_context", "executor", "obligation.possible", "narrator", "consistent.mechanic", "guard",
            "consistent.mechanic", "obligation.enforce", "party_size", "finalize", "commit",
        ])
        self.assertEqual(result[0], "敘事")

    async def test_a_resolved_check_followup_goes_straight_to_the_narrator(self):
        events, result = await run(make_state(), turn_kind="resolved_check_followup")
        self.assertEqual(events, [
            "build_context", "obligation.possible", "narrator", "consistent.resolved", "guard",
            "consistent.resolved", "party_size", "finalize", "commit",
        ])
        self.assertEqual(result[0], "敘事")

    async def test_a_stale_timeline_is_not_delivered(self):
        events, result = await run(make_state(), commit=False)
        self.assertEqual(events[-1], "commit")
        self.assertEqual(result, (CONFLICT, [], []))

    async def test_a_failed_opening_stops_before_the_guard(self):
        events, result = await run(make_state(), turn_kind="opening_fallback", narration_failed=True)
        self.assertEqual(events, ["build_context", "narrator"])
        self.assertEqual(result[1:], ([], []))

    async def test_the_kp_assistant_never_reaches_the_player_pipeline(self):
        events, result = await run(make_state(), extra={"speaker_role": "kp_assistant"}, text="@KP 幫我看一下劇本")
        self.assertEqual(events, ["build_context", "assistant"])
        self.assertEqual(result[0], "助手")


if __name__ == "__main__":
    unittest.main()
