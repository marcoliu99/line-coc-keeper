import asyncio
import inspect
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers import (
    anthropic_provider,
    gemini_provider,
    openai_provider,
    shutdown_async_clients,
)
from app.providers.client_lifecycle import AsyncClientLifecycle


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

    async def test_shutdown_waits_for_an_inflight_request_before_closing(self):
        client = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock()),
            aclose=AsyncMock(),
        )
        fake_openai = types.SimpleNamespace(AsyncOpenAI=MagicMock(return_value=client))
        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(openai_provider, "OPENAI_API_KEY", "test-key"):
            async with openai_provider._request_scope() as scoped_client:
                self.assertIs(scoped_client, client)
                shutdown = asyncio.create_task(openai_provider.shutdown_async_client())
                await asyncio.sleep(0.01)
                client.aclose.assert_not_awaited()
            await shutdown

        client.aclose.assert_awaited_once()

    async def test_new_request_waits_for_shutdown_barrier(self):
        first = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock()),
            aclose=AsyncMock(),
        )
        second = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock()),
            aclose=AsyncMock(),
        )
        fake_openai = types.SimpleNamespace(AsyncOpenAI=MagicMock(side_effect=[first, second]))
        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(openai_provider, "OPENAI_API_KEY", "test-key"):
            async with openai_provider._request_scope():
                shutdown = asyncio.create_task(openai_provider.shutdown_async_client())
                await asyncio.sleep(0.01)
                pending_request = asyncio.create_task(openai_provider.get_async_client())
                await asyncio.sleep(0.01)
                self.assertFalse(pending_request.done())
            await shutdown
            self.assertIs(await pending_request, second)
            await openai_provider.shutdown_async_client()

        first.aclose.assert_awaited_once()
        second.aclose.assert_awaited_once()

    async def test_shutdown_async_clients_attempts_all_providers_after_failure(self):
        openai_shutdown = AsyncMock(side_effect=RuntimeError("openai close failed"))
        anthropic_shutdown = AsyncMock()
        gemini_shutdown = AsyncMock()
        with patch.object(openai_provider, "shutdown_async_client", openai_shutdown), \
                patch.object(anthropic_provider, "shutdown_async_client", anthropic_shutdown), \
                patch.object(gemini_provider, "shutdown_async_client", gemini_shutdown), \
                self.assertRaisesRegex(RuntimeError, "openai close failed"):
            await shutdown_async_clients()

        openai_shutdown.assert_awaited_once()
        anthropic_shutdown.assert_awaited_once()
        gemini_shutdown.assert_awaited_once()

    async def test_lifecycle_bounds_transport_close_after_drain(self):
        lifecycle = AsyncClientLifecycle("test", shutdown_grace_seconds=0.01)
        client = object()
        close_started = asyncio.Event()

        async def hanging_close(_client, _owner):
            close_started.set()
            await asyncio.sleep(1)

        await lifecycle.get_or_create(lambda: client, hanging_close)
        started_at = asyncio.get_running_loop().time()
        await lifecycle.shutdown(hanging_close)
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertLess(elapsed, 0.5)
        self.assertTrue(close_started.is_set())
        self.assertIsNone(lifecycle.current_state)
        self.assertEqual(lifecycle.retired_states, ())


class AsyncProviderCrossLoopTests(unittest.TestCase):
    def test_client_is_recreated_and_old_client_closed_when_loop_changes(self):
        first = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock()),
            aclose=AsyncMock(),
        )
        second = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock()),
            aclose=AsyncMock(),
        )
        fake_openai = types.SimpleNamespace(AsyncOpenAI=MagicMock(side_effect=[first, second]))

        async def get_client():
            from app.providers import openai_provider

            return await openai_provider.get_async_client()

        async def close_client():
            from app.providers import openai_provider

            await openai_provider.shutdown_async_client()

        from app.providers import openai_provider

        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(openai_provider, "OPENAI_API_KEY", "test-key"):
            self.assertIs(asyncio.run(get_client()), first)
            self.assertIs(asyncio.run(get_client()), second)
            asyncio.run(close_client())

        first.aclose.assert_awaited_once()
        second.aclose.assert_awaited_once()

    def test_gemini_closes_both_aio_surface_and_owner(self):
        from app.providers import gemini_provider

        aio = SimpleNamespace(aclose=AsyncMock())
        owner = SimpleNamespace(aio=aio, close=AsyncMock())
        fake_genai = types.SimpleNamespace(Client=MagicMock(return_value=owner))
        fake_google = types.ModuleType("google")
        fake_google.genai = fake_genai

        async def exercise():
            self.assertIs(await gemini_provider.get_async_client(), aio)
            await gemini_provider.shutdown_async_client()

        with patch.dict(sys.modules, {"google": fake_google}), \
                patch.object(gemini_provider, "GEMINI_API_KEY", "test-key"):
            asyncio.run(exercise())

        aio.aclose.assert_awaited_once()
        owner.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
