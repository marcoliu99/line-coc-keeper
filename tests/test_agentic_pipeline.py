import unittest
from unittest.mock import patch

from app.domain.models import MechanicResult, StateDelta
from app.models import GroupState


class ContextBuilderScenarioRagGatingTests(unittest.IsolatedAsyncioTestCase):
    """Regression tests for the review finding that context_builder ran
    scenario_rag.get_index/search on every turn regardless of
    SCENARIO_RAG_ENABLED (default off), even though keeper._build_static_prompt
    already embeds the full/chapter scenario text directly in that mode,
    making the proactive search redundant and a real per-turn cost."""

    def _state(self) -> GroupState:
        state = GroupState(group_id="g")
        state.scenario_text = "some scenario text"
        state.scenario_title = "Some Title"
        return state

    async def test_scenario_rag_skipped_when_disabled(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", False), \
                patch.object(scenario_rag, "get_index") as mock_get_index, \
                patch.object(memory_rag, "search_memory", return_value=[]):
            message = await context_builder.build_context(
                state=self._state(), user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        mock_get_index.assert_not_called()
        self.assertEqual(message.payload["rag_context"], "")

    async def test_scenario_rag_runs_when_enabled(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value="fake-index") as mock_get_index, \
                patch.object(scenario_rag, "search", return_value=["chunk"]) as mock_search, \
                patch.object(scenario_rag, "format_results", return_value="formatted rag context"), \
                patch.object(memory_rag, "search_memory", return_value=[]):
            message = await context_builder.build_context(
                state=self._state(), user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        mock_get_index.assert_called_once()
        mock_search.assert_called_once()
        self.assertEqual(message.payload["rag_context"], "formatted rag context")


class SupervisorMechanicResultPayloadTests(unittest.IsolatedAsyncioTestCase):
    """Regression test for the review finding that supervisor.run_turn
    computed mechanic_result from the Executor's tool calls but never wrote
    it back into message.payload — narrator.py falls back to
    PURE_ROLEPLAY_BLOCK whenever it's missing, so every GAMEPLAY_ACTION turn
    was silently narrating as if nothing mechanical had happened."""

    async def test_mechanic_result_is_written_to_payload_for_gameplay_action(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "P1", "text": "attack",
            "resolved_location": None, "speaker_role": "player", "state": state,
            "character": None, "rag_context": "", "memory_context": "",
        })
        fake_result = MechanicResult(
            success=True, action_type="skill_check", narrative_facts=["rolled a 42, success"],
            state_delta=StateDelta(),
        )
        captured_payload: dict = {}

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_executor(msg):
            return fake_result

        async def fake_run_narrator(msg):
            captured_payload.update(msg.payload)
            return "narration", [], []

        with patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
                patch.object(supervisor.executor, "run_executor", fake_run_executor), \
                patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.rule_validator, "validate_narrative", return_value=(True, "")):
            await supervisor.run_turn(
                state=state, user_id="u1", display_name="P1", text="attack",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertIs(captured_payload.get("mechanic_result"), fake_result)


if __name__ == "__main__":
    unittest.main()
