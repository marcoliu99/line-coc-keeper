import unittest
from unittest.mock import AsyncMock, patch

from app.domain.models import AgentMessage
from app.models import GroupState
from app.services import prompt_config


class ExecutorRagReusePromptTests(unittest.TestCase):
    def test_executor_prompt_keeps_rag_context_and_explains_when_to_reuse_or_search(self):
        context = "第 10 頁：地下室樓梯年久失修；Push 失敗會跌落。"

        prompt = prompt_config.build_executor_dynamic_prompt_with_context(
            "目前場景。", context, "過去曾探索地下室。"
        )

        self.assertIn(f"【劇本相關內容】\n{context}", prompt)
        self.assertIn("不要為了確認或改寫查詢而再次呼叫 search_scenario", prompt)
        self.assertIn("只有在缺少一項會影響本次判定", prompt)
        self.assertIn("【過去記憶】\n過去曾探索地下室。", prompt)
        self.assertIn("不會自行建立檢定、擲骰、改變角色狀態或推進場景", prompt)

    def test_empty_rag_context_keeps_search_available_for_missing_facts(self):
        prompt = prompt_config.build_executor_dynamic_prompt_with_context(
            "目前場景。", "", ""
        )

        self.assertNotIn("【劇本相關內容】\n", prompt)
        self.assertIn("沒有可用的【劇本相關內容】", prompt)
        self.assertIn("仍可照常搜尋", prompt)

    def test_narrator_context_does_not_receive_executor_retrieval_policy(self):
        prompt = prompt_config.build_dynamic_prompt_with_context(
            "目前場景。", "地下室樓梯資訊。", ""
        )

        self.assertIn("地下室樓梯資訊。", prompt)
        self.assertNotIn("Executor 劇本檢索規則", prompt)


class ExecutorRagReuseIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_receives_reuse_policy_and_still_has_search_tool(self):
        from app.agents import executor

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "state": state,
            "text": "查看地下室樓梯",
            "user_id": "u1",
            "display_name": "調查員",
            "speaker_role": "player",
            "rag_context": "第 10 頁：地下室樓梯年久失修。",
        })
        fake_run_conversation = AsyncMock(return_value="ignored")
        fake_provider = type("P", (), {"run_conversation": fake_run_conversation})()

        with patch.object(executor, "_PROVIDERS", {"openai": fake_provider}), \
                patch.object(executor, "LLM_PROVIDER", "openai"), \
                patch("app.keeper.SCENARIO_RAG_ENABLED", True):
            await executor.run_executor(message)

        dynamic_prompt = fake_run_conversation.call_args.args[1]
        offered_tools = fake_run_conversation.call_args.args[2]
        self.assertIn("第 10 頁：地下室樓梯年久失修。", dynamic_prompt)
        self.assertIn("不要為了確認或改寫查詢而再次呼叫 search_scenario", dynamic_prompt)
        self.assertIn("search_scenario", {tool["name"] for tool in offered_tools})


if __name__ == "__main__":
    unittest.main()
