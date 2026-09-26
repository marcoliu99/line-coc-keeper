import unittest
from unittest.mock import AsyncMock, patch

from app import observability
from app.agents import executor
from app.domain.models import AgentMessage
from app.models import GroupState


class ExecutorSearchSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_zero_one_and_multiple_searches_without_query_text(self):
        for calls in (0, 1, 3):
            with self.subTest(calls=calls):
                state = GroupState(group_id="g")
                message = AgentMessage(payload={
                    "state": state, "text": "查看地下室", "user_id": "u1",
                    "display_name": "玩家", "speaker_role": "player",
                    "rag_context": "", "memory_context": "",
                })

                async def run_conversation(*args, _calls=calls, **kwargs):
                    callback = args[5]
                    for index in range(_calls):
                        await callback("search_scenario", {"query": f"private query {index}"})
                    return ""

                fake_provider = type("Provider", (), {"run_conversation": staticmethod(run_conversation)})()
                tool = AsyncMock(return_value={"ok": False, "error": "missing"})
                with patch.object(executor, "LLM_PROVIDER", "openai"), \
                        patch.object(executor, "_PROVIDERS", {"openai": fake_provider}), \
                        patch.object(executor, "make_tool_executor", return_value=tool), \
                        patch.object(observability, "event") as event:
                    await executor.run_executor(message)
                summaries = [call for call in event.call_args_list if call.args[0] == "executor.scenario_search.summary"]
                self.assertEqual(len(summaries), 1)
                self.assertEqual(summaries[0].kwargs, {"count": calls, "status": "success"})
                self.assertNotIn("query", str(summaries[0]))
                self.assertEqual(tool.await_count, calls)

    async def test_reports_error_after_search_tool_call(self):
        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "state": state, "text": "look", "user_id": "u1",
            "display_name": "Player", "speaker_role": "player",
        })

        async def run_conversation(*args, **kwargs):
            await args[5]("search_scenario", {"query": "secret"})
            raise RuntimeError("provider failure")

        fake_provider = type("Provider", (), {"run_conversation": staticmethod(run_conversation)})()
        with patch.object(executor, "LLM_PROVIDER", "anthropic"), \
                patch.object(executor, "_PROVIDERS", {"anthropic": fake_provider}), \
                patch.object(executor, "make_tool_executor", return_value=AsyncMock(return_value={"ok": False})), \
                patch.object(observability, "event") as event:
            result = await executor.run_executor(message)
        self.assertFalse(result.success)
        summaries = [call for call in event.call_args_list if call.args[0] == "executor.scenario_search.summary"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].kwargs, {"count": 1, "status": "error"})


if __name__ == "__main__":
    unittest.main()
