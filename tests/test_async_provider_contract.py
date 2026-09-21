import inspect
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers import anthropic_provider, gemini_provider, openai_provider


class AsyncProviderContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await openai_provider.shutdown_async_client()
        await anthropic_provider.shutdown_async_client()
        await gemini_provider.shutdown_async_client()

    async def asyncTearDown(self):
        await openai_provider.shutdown_async_client()
        await anthropic_provider.shutdown_async_client()
        await gemini_provider.shutdown_async_client()

    def test_all_conversation_adapters_are_coroutines(self):
        self.assertTrue(inspect.iscoroutinefunction(openai_provider.run_conversation))
        self.assertTrue(inspect.iscoroutinefunction(anthropic_provider.run_conversation))
        self.assertTrue(inspect.iscoroutinefunction(gemini_provider.run_conversation))

    async def test_openai_tool_calls_are_awaited_in_sdk_order(self):
        first = SimpleNamespace(
            output=[
                SimpleNamespace(type="function_call", name="first", arguments="{}", call_id="1"),
                SimpleNamespace(type="function_call", name="second", arguments="{}", call_id="2"),
            ],
            id="response-1",
        )
        final = SimpleNamespace(output=[], output_text="done", id="response-2")
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=[first, final])))
        fake_openai = types.SimpleNamespace(AsyncOpenAI=MagicMock(return_value=client))
        calls: list[str] = []

        async def execute_tool(name, _args):
            calls.append(name)
            return {"ok": True, "name": name}

        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(openai_provider, "OPENAI_API_KEY", "test-key"), \
                patch.object(openai_provider, "_unsupported_params", set()):
            result = await openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", execute_tool, 2
            )

        self.assertEqual(result, "done")
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(client.responses.create.await_count, 2)

    async def test_async_openai_client_is_reused_and_closed(self):
        client = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock()),
            aclose=AsyncMock(),
        )
        fake_openai = types.SimpleNamespace(AsyncOpenAI=MagicMock(return_value=client))
        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(openai_provider, "OPENAI_API_KEY", "test-key"):
            first = await openai_provider.get_async_client()
            second = await openai_provider.get_async_client()
            await openai_provider.shutdown_async_client()

        self.assertIs(first, second)
        fake_openai.AsyncOpenAI.assert_called_once_with(api_key="test-key", max_retries=0)
        client.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
