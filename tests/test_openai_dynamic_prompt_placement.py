"""Coverage for WP2: the per-turn dynamic prompt rides after the player's
message so the static prompt and tool schema stay a cacheable prefix, and it
still reaches the model in both input shapes.

Measured against the real API before implementing (see
scripts/experiments/ab_prompt_cache_boundary.py): 12.5% cached with the block
inside instructions, 0.0% with it at the front of the input list, 82.5% with
it after the player's message."""
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers import openai_provider


def _client(responses=3):
    outputs = [SimpleNamespace(output=[], output_text="done", id=f"r{i}") for i in range(responses)]
    return SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=outputs)))


async def _run(client, *, after_input: bool, previous_response_id: str = "", history=None):
    fake_openai = types.SimpleNamespace(DefaultAsyncHttpxClient=MagicMock(),
                                        AsyncOpenAI=MagicMock(return_value=client))

    async def execute_tool(name, _args):
        return {"ok": True, "name": name}

    await openai_provider.shutdown_async_client()
    with patch.dict(sys.modules, {"openai": fake_openai}), \
            patch.object(openai_provider, "OPENAI_API_KEY", "test-key"), \
            patch.object(openai_provider, "_unsupported_params", set()), \
            patch.object(openai_provider.config, "OPENAI_DYNAMIC_PROMPT_AFTER_INPUT", after_input):
        return await openai_provider.run_conversation(
            "STATIC-PROMPT", "DYNAMIC-HP-8/10", [], history or [], "我推開門",
            execute_tool, 1, previous_response_id=previous_response_id,
        )


def _sent(client):
    return client.responses.create.await_args_list[0].kwargs


class DynamicPromptPlacementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await openai_provider.shutdown_async_client()

    async def test_instructions_hold_only_the_static_prompt(self):
        client = _client()
        await _run(client, after_input=True)
        self.assertEqual(_sent(client)["instructions"], "STATIC-PROMPT")

    async def test_dynamic_block_follows_the_player_message(self):
        client = _client()
        await _run(client, after_input=True)
        items = _sent(client)["input"]
        roles = [item["role"] for item in items]
        self.assertEqual(roles[-2:], ["user", "developer"])
        self.assertEqual(items[-1]["content"], "DYNAMIC-HP-8/10")
        self.assertEqual(items[-2]["content"], "我推開門")

    async def test_chained_shape_still_delivers_current_state(self):
        """The chained branch relied on instructions to carry current state, so
        omitting the block there would leave the model reading a stale sheet."""
        client = _client()
        await _run(client, after_input=True, previous_response_id="resp-from-last-turn")
        sent = _sent(client)
        self.assertEqual(sent["previous_response_id"], "resp-from-last-turn")
        self.assertIn("DYNAMIC-HP-8/10", [item["content"] for item in sent["input"]])
        self.assertEqual(sent["instructions"], "STATIC-PROMPT")

    async def test_history_stays_ahead_of_the_player_message(self):
        client = _client()
        await _run(client, after_input=True,
                   history=[{"role": "user", "content": "先前"}, {"role": "assistant", "content": "回覆"}])
        contents = [item["content"] for item in _sent(client)["input"]]
        self.assertEqual(contents, ["先前", "回覆", "我推開門", "DYNAMIC-HP-8/10"])

    async def test_flag_off_restores_the_previous_composition(self):
        client = _client()
        await _run(client, after_input=False)
        sent = _sent(client)
        self.assertEqual(sent["instructions"], "STATIC-PROMPT\n\nDYNAMIC-HP-8/10")
        self.assertEqual([item["role"] for item in sent["input"]], ["user"])

    async def test_invalid_chain_retry_rebuilds_the_current_dynamic_block(self):
        for after_input in (True, False):
            with self.subTest(after_input=after_input):
                client = _client()
                client.responses.create.side_effect = [
                    ValueError("previous_response_id resp-from-last-turn expired"),
                    SimpleNamespace(output=[], output_text="done", id="retry"),
                ]
                await _run(client, after_input=after_input,
                           previous_response_id="resp-from-last-turn",
                           history=[{"role": "assistant", "content": "先前回覆"}])
                initial, fallback = [call.kwargs for call in client.responses.create.await_args_list]
                self.assertEqual(initial["previous_response_id"], "resp-from-last-turn")
                self.assertNotIn("previous_response_id", fallback)
                self.assertEqual([item["content"] for item in fallback["input"]],
                                 ["先前回覆", "我推開門"] + (["DYNAMIC-HP-8/10"] if after_input else []))
                self.assertEqual(fallback["instructions"],
                                 "STATIC-PROMPT" if after_input else "STATIC-PROMPT\n\nDYNAMIC-HP-8/10")

    async def test_composition_event_does_not_count_the_block_twice(self):
        client = _client()
        with patch.object(openai_provider.observability, "event") as event:
            await _run(client, after_input=True)
        composition = next(call for call in event.call_args_list
                           if call.args[0] == "llm.input.composition").kwargs
        # new_input_tokens already includes the block once it rides in input.
        self.assertEqual(
            composition["input_tokens_estimate"],
            composition["static_tokens_estimate"] + composition["tools_tokens_estimate"]
            + composition["new_input_tokens_estimate"])


if __name__ == "__main__":
    unittest.main()
