"""Offline coverage for cross-turn prompt cache routing on the OpenAI path."""
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import observability
from app.providers import openai_provider


def _client(responses=4):
    final = [SimpleNamespace(output=[], output_text="done", id=f"response-{i}") for i in range(responses)]
    return SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=final)))


async def _run(client, **context):
    # The client is cached per event loop, so a second run in one test would
    # otherwise reuse the first client and record nothing on this one.
    await openai_provider.shutdown_async_client()
    fake_openai = types.SimpleNamespace(DefaultAsyncHttpxClient=MagicMock(),
                                        AsyncOpenAI=MagicMock(return_value=client))

    async def execute_tool(name, _args):
        return {"ok": True, "name": name}

    with patch.dict(sys.modules, {"openai": fake_openai}), \
            patch.object(openai_provider, "OPENAI_API_KEY", "test-key"), \
            patch.object(openai_provider, "_unsupported_params", set()):
        if context:
            with observability.context(**context):
                return await openai_provider.run_conversation(
                    "static", "dynamic", [], [], "hello", execute_tool, 1)
        return await openai_provider.run_conversation(
            "static", "dynamic", [], [], "hello", execute_tool, 1)


def _keys(client):
    return [call.kwargs.get("prompt_cache_key") for call in client.responses.create.await_args_list]


def _sent(client):
    return ["prompt_cache_key" in call.kwargs for call in client.responses.create.await_args_list]


class PromptCacheKeyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await openai_provider.shutdown_async_client()

    async def asyncTearDown(self):
        await openai_provider.shutdown_async_client()

    async def test_bound_conversation_is_sent_as_the_cache_key(self):
        client = _client()
        await _run(client, conversation_id="1a7b1e88e38a")
        self.assertEqual(_keys(client), ["1a7b1e88e38a"])

    async def test_separate_turns_of_one_conversation_share_a_key(self):
        first, second = _client(), _client()
        await _run(first, conversation_id="1a7b1e88e38a", turn_id="turn-1", request_id="req-1")
        await _run(second, conversation_id="1a7b1e88e38a", turn_id="turn-2", request_id="req-2")
        # The prefix is identical across turns, so the routing key must be too.
        self.assertEqual(set(_keys(first)), {"1a7b1e88e38a"})
        self.assertEqual(set(_keys(second)), {"1a7b1e88e38a"})

    async def test_separate_conversations_do_not_share_a_key(self):
        first, second = _client(), _client()
        await _run(first, conversation_id="1a7b1e88e38a")
        await _run(second, conversation_id="0341532cdeef")
        self.assertNotEqual(_keys(first), _keys(second))

    async def test_turn_and_request_ids_never_reach_the_key(self):
        client = _client()
        await _run(client, conversation_id="1a7b1e88e38a", turn_id="turn-1", request_id="req-1")
        key = _keys(client)[0]
        self.assertNotIn("turn-1", key)
        self.assertNotIn("req-1", key)

    async def test_unbound_conversation_omits_the_parameter_entirely(self):
        client = _client()
        await _run(client)
        # An empty or shared constant key would pool unrelated games together.
        self.assertEqual(_sent(client), [False])


if __name__ == "__main__":
    unittest.main()
