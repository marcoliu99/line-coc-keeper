"""Regression tests for logging the actual query text on every call site that
triggers a scenario/memory RAG search (i.e., every place that can make an
embeddings API call for a query string):

- app/keeper.py's _execute_tool, for the Keeper explicitly calling the
  search_scenario/search_memory tools.
- app/agents/context_builder.py's build_context, which runs scenario/memory
  RAG *proactively* once per turn (memory unconditionally whenever the
  speaker has a character, scenario when SCENARIO_RAG_ENABLED) — a call
  site a first pass at this fix missed entirely, discovered during review.
- app/legacy_commands.py's _find_room_via_rag, the Map/Scene Engine's RAG
  fallback for room-name resolution.

Before this, none of these captured the query string itself anywhere in the
logs — only counts (candidate_count, result_count, ...) via their
observability spans — so there was no way to tell, e.g., whether a Keeper
turn that appeared to call search_scenario multiple times was searching
distinct keywords, repeating one, or partly attributable to the proactive
per-turn context_builder search instead of any explicit tool call at all."""
import unittest
from unittest.mock import patch

from app import keeper, legacy_commands, memory_rag, scenario_rag
from app.models import Character, GroupState


class RagQueryLoggingTests(unittest.TestCase):
    def _state(self) -> GroupState:
        state = GroupState(group_id="g")
        state.scenario_text = "some scenario text"
        return state

    def test_search_scenario_logs_the_query_text(self):
        with patch.object(scenario_rag, "get_index", return_value="fake-index"), \
             patch.object(scenario_rag, "search", return_value=[]), \
             patch.object(scenario_rag, "format_results", return_value="（沒有找到相關內容）"), \
             self.assertLogs("app.keeper", level="INFO") as logs:
            keeper._execute_tool(
                self._state(), "search_scenario", {"query": "卡西迪"}, [], [], "player"
            )
        self.assertTrue(any("search_scenario" in line and "卡西迪" in line for line in logs.output))

    def test_search_memory_logs_the_query_text(self):
        with patch.object(memory_rag, "search_memory", return_value=[]), \
             patch.object(memory_rag, "format_results", return_value="（沒有找到相關內容）"), \
             self.assertLogs("app.keeper", level="INFO") as logs:
            keeper._execute_tool(
                self._state(), "search_memory", {"query": "地下室"}, [], [], "player"
            )
        self.assertTrue(any("search_memory" in line and "地下室" in line for line in logs.output))

    def test_repeated_identical_queries_are_both_visible_in_the_log(self):
        """The whole point: two search_scenario calls in the same turn with
        the same query text must both show up, so a human reading the log
        can tell they were duplicates rather than distinct searches."""
        with patch.object(scenario_rag, "get_index", return_value="fake-index"), \
             patch.object(scenario_rag, "search", return_value=[]), \
             patch.object(scenario_rag, "format_results", return_value="（沒有找到相關內容）"), \
             self.assertLogs("app.keeper", level="INFO") as logs:
            keeper._execute_tool(self._state(), "search_scenario", {"query": "地窖"}, [], [], "player")
            keeper._execute_tool(self._state(), "search_scenario", {"query": "地窖"}, [], [], "player")
        matching = [line for line in logs.output if "search_scenario" in line and "地窖" in line]
        self.assertEqual(len(matching), 2)

    def test_find_room_via_rag_logs_the_query_text(self):
        with patch.object(scenario_rag, "get_index", return_value="fake-index"), \
             patch.object(scenario_rag, "search", return_value=[]), \
             self.assertLogs("app.legacy_commands", level="INFO") as logs:
            legacy_commands._find_room_via_rag("g", "scenario text", {"rooms": []}, "廚房")
        self.assertTrue(any("_find_room_via_rag" in line and "廚房" in line for line in logs.output))


class ContextBuilderQueryLoggingTests(unittest.IsolatedAsyncioTestCase):
    """context_builder.build_context runs scenario/memory RAG proactively,
    not in response to an explicit Keeper tool call — see this file's module
    docstring for why this call site needed the same fix."""

    def _state(self, *, with_character: bool) -> GroupState:
        state = GroupState(group_id="g")
        state.scenario_text = "some scenario text"
        state.scenario_title = "Some Title"
        if with_character:
            state.characters["u1"] = Character(name="P1", owner_id="u1")
        return state

    async def test_proactive_memory_rag_logs_the_query_text(self):
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", False), \
             patch.object(memory_rag, "search_memory", return_value=[]), \
             patch.object(memory_rag, "format_results", return_value=""), \
             self.assertLogs("app.agents.context_builder", level="INFO") as logs:
            await context_builder.build_context(
                state=self._state(with_character=True), user_id="u1", display_name="P1",
                text="我想查一下密室的線索", resolved_location=None, speaker_role="player",
                conversation_id="g",
            )
        self.assertTrue(
            any("context_builder.memory_rag" in line and "我想查一下密室的線索" in line for line in logs.output)
        )

    async def test_proactive_scenario_rag_logs_the_query_text_when_enabled(self):
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
             patch.object(scenario_rag, "get_index", return_value="fake-index"), \
             patch.object(scenario_rag, "search", return_value=[]), \
             patch.object(scenario_rag, "format_results", return_value=""), \
             patch.object(memory_rag, "search_memory", return_value=[]), \
             patch.object(memory_rag, "format_results", return_value=""), \
             self.assertLogs("app.agents.context_builder", level="INFO") as logs:
            await context_builder.build_context(
                state=self._state(with_character=True), user_id="u1", display_name="P1",
                text="調查廚房", resolved_location=None, speaker_role="player", conversation_id="g",
            )
        self.assertTrue(
            any("context_builder.scenario_rag" in line and "調查廚房" in line for line in logs.output)
        )


if __name__ == "__main__":
    unittest.main()
