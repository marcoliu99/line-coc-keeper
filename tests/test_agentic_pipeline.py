import time
import unittest
from unittest.mock import patch

from app.domain.models import MechanicResult, StateDelta
from app.models import Character, GroupState


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
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
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

    async def test_scenario_and_memory_rag_are_gathered_and_one_failure_is_isolated(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        def slow_scenario_search(*_args, **_kwargs):
            time.sleep(0.08)
            raise RuntimeError("scenario index unavailable")

        def slow_memory_search(*_args, **_kwargs):
            time.sleep(0.08)
            return [{"label": "old", "text": "memory"}]

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value="fake-index"), \
                patch.object(scenario_rag, "search", side_effect=slow_scenario_search), \
                patch.object(memory_rag, "search_memory", side_effect=slow_memory_search), \
                patch.object(scenario_rag, "format_results", return_value=""), \
                patch.object(memory_rag, "format_results", return_value="memory context"):
            started = time.perf_counter()
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.15)
        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["rag_status"], "error")
        self.assertEqual(message.payload["memory_context"], "memory context")
        self.assertEqual(message.payload["memory_status"], "success")

    async def test_proactive_rag_does_not_inject_bm25_fallback_context(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        index = type("Index", (), {"chunks": [1], "has_embeddings": False, "index_cache": "memory"})()

        def memory_search(_group_id, _query, *, metrics):
            metrics["has_embeddings"] = False
            return [{"label": "old", "text": "fallback memory"}]

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value=index), \
                patch.object(scenario_rag, "search", return_value=[{"page": 1, "text": "fallback scenario"}]), \
                patch.object(scenario_rag, "format_results", return_value="fallback scenario context"), \
                patch.object(memory_rag, "search_memory", side_effect=memory_search), \
                patch.object(memory_rag, "format_results", return_value="fallback memory context"):
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["rag_status"], "fallback")
        self.assertEqual(message.payload["memory_context"], "")
        self.assertEqual(message.payload["memory_status"], "fallback")

    async def test_proactive_rag_empty_results_are_not_formatted_into_prompt(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value="fake-index"), \
                patch.object(scenario_rag, "search", return_value=[]), \
                patch.object(scenario_rag, "format_results", return_value="should not be used"), \
                patch.object(memory_rag, "search_memory", return_value=[]), \
                patch.object(memory_rag, "format_results", return_value="should not be used"):
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["rag_status"], "empty")
        self.assertEqual(message.payload["memory_context"], "")
        self.assertEqual(message.payload["memory_status"], "empty")

    def test_scenario_context_budget_keeps_structural_boundaries(self):
        from app import keeper

        scenario = "--- 第 1 頁 ---\n第一頁完整內容\n--- 第 2 頁 ---\n第二頁完整內容"
        with patch.object(keeper, "MAX_SCENARIO_CHARS", 30):
            bounded = keeper._bounded_scenario_context(scenario)

        self.assertLessEqual(len(bounded), 30)
        self.assertIn("第一頁完整內容", bounded)
        self.assertNotIn("第二頁完整內容", bounded)


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
