"""Regression tests for logging the actual query text on search_scenario/
search_memory tool calls (app/keeper.py's _execute_tool).

Before this, app/keeper.py's rag.search/memory.search observability spans
captured only counts (candidate_count, result_count, ...) — never the query
string itself — so there was no way to tell from the logs whether a Keeper
turn that called search_scenario multiple times was searching for distinct
keywords or repeating the same one."""
import logging
import unittest
from unittest.mock import patch

from app import keeper, memory_rag, scenario_rag
from app.models import GroupState


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


if __name__ == "__main__":
    unittest.main()
