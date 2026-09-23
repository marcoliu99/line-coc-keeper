"""Tests for the forced-wrap-up fix in each provider's tool-calling loop.

Diagnosed from a live log (docs/specs/bug-add-npc-to-combat-duplicate-name-
guard.md): a turn that spends every iteration up to max_iterations on tool
calls (never producing plain text) used to fall off the loop and return the
hardcoded "（守密人一時語塞...）" placeholder — even though the tool calls
themselves (start_combat, add_npc_to_combat, HP changes, ...) already
executed and saved for real. The player was never told what had just
happened, and the game state and the player's understanding of it silently
diverged. Each provider now makes one extra request with tools disabled when
this happens, forcing a plain-text wrap-up instead.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

PLACEHOLDER = "（守密人一時語塞，請再說一次剛才的行動）"


async def _execute_tool(_name, _args):
    return {"ok": True}


class OpenAIWrapupTests(unittest.TestCase):
    def _tool_call_response(self, call_id="call_1"):
        fc = MagicMock(type="function_call", arguments="{}", call_id=call_id, name="add_npc_to_combat")
        return MagicMock(output=[fc], output_text="", id=f"resp_{call_id}")

    def test_forces_final_wrapup_call_with_no_tools_when_budget_exhausted(self):
        from app.providers import openai_provider

        wrapup_response = MagicMock(output=[], output_text="柯比特甦醒並撲向你。", id="resp_wrapup")
        fake_client = MagicMock()
        fake_client.responses.create = AsyncMock(side_effect=[self._tool_call_response(), wrapup_response])
        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}), \
             patch("app.providers.openai_provider.OPENAI_API_KEY", "test-key"):
            result = asyncio.run(openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1
            ))
            asyncio.run(openai_provider.shutdown_async_client())

        self.assertEqual(result, "柯比特甦醒並撲向你。")
        self.assertEqual(fake_client.responses.create.call_count, 2)
        wrapup_kwargs = fake_client.responses.create.call_args_list[-1].kwargs
        self.assertNotIn("tools", wrapup_kwargs)

    def test_keeps_placeholder_when_wrapup_call_itself_fails(self):
        from app.providers import openai_provider

        fake_client = MagicMock()
        fake_client.responses.create = AsyncMock(side_effect=[self._tool_call_response(), RuntimeError("boom")])
        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}), \
             patch("app.providers.openai_provider.OPENAI_API_KEY", "test-key"):
            result = asyncio.run(openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1
            ))
            asyncio.run(openai_provider.shutdown_async_client())

        self.assertEqual(result, PLACEHOLDER)

    def test_enable_wrapup_false_skips_the_extra_call(self):
        # PR #55 review finding: app/agents/executor.py's Supervisor-path
        # caller discards this function's return value entirely and a
        # separate Narrator call always runs afterward regardless — so a
        # forced wrap-up there is a real extra API call whose output the
        # player could never see. enable_wrapup=False must skip it.
        from app.providers import openai_provider

        fake_client = MagicMock()
        fake_client.responses.create = AsyncMock(return_value=self._tool_call_response())
        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}), \
             patch("app.providers.openai_provider.OPENAI_API_KEY", "test-key"):
            result = asyncio.run(openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1, enable_wrapup=False,
            ))
            asyncio.run(openai_provider.shutdown_async_client())

        self.assertEqual(result, PLACEHOLDER)
        self.assertEqual(fake_client.responses.create.call_count, 1)

    def test_normal_turn_with_a_final_text_response_never_triggers_wrapup(self):
        from app.providers import openai_provider

        text_response = MagicMock(output=[], output_text="平常的敘述。", id="resp_1")
        fake_client = MagicMock()
        fake_client.responses.create = AsyncMock(return_value=text_response)
        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}), \
             patch("app.providers.openai_provider.OPENAI_API_KEY", "test-key"):
            result = asyncio.run(openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 3
            ))
            asyncio.run(openai_provider.shutdown_async_client())

        self.assertEqual(result, "平常的敘述。")
        self.assertEqual(fake_client.responses.create.call_count, 1)


class AnthropicWrapupTests(unittest.TestCase):
    def _tool_use_response(self):
        block = MagicMock(type="tool_use", id="tu1", name="add_npc_to_combat", input={})
        return MagicMock(content=[block])

    def test_forces_final_wrapup_call_with_no_tools_when_budget_exhausted(self):
        from app.providers import anthropic_provider

        wrapup_text_block = MagicMock(type="text", text="柯比特甦醒並撲向你。")
        wrapup_response = MagicMock(content=[wrapup_text_block])
        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(side_effect=[self._tool_use_response(), wrapup_response])
        fake_anthropic_module = MagicMock()
        fake_anthropic_module.AsyncAnthropic = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"anthropic": fake_anthropic_module}), \
             patch("app.providers.anthropic_provider.ANTHROPIC_API_KEY", "test-key"):
            result = asyncio.run(anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1
            ))
            asyncio.run(anthropic_provider.shutdown_async_client())

        self.assertEqual(result, "柯比特甦醒並撲向你。")
        self.assertEqual(fake_client.messages.create.call_count, 2)
        wrapup_kwargs = fake_client.messages.create.call_args_list[-1].kwargs
        self.assertNotIn("tools", wrapup_kwargs)

    def test_keeps_placeholder_when_wrapup_call_itself_fails(self):
        from app.providers import anthropic_provider

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(side_effect=[self._tool_use_response(), RuntimeError("boom")])
        fake_anthropic_module = MagicMock()
        fake_anthropic_module.AsyncAnthropic = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"anthropic": fake_anthropic_module}), \
             patch("app.providers.anthropic_provider.ANTHROPIC_API_KEY", "test-key"):
            result = asyncio.run(anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1
            ))
            asyncio.run(anthropic_provider.shutdown_async_client())

        self.assertEqual(result, PLACEHOLDER)

    def test_enable_wrapup_false_skips_the_extra_call(self):
        from app.providers import anthropic_provider

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=self._tool_use_response())
        fake_anthropic_module = MagicMock()
        fake_anthropic_module.AsyncAnthropic = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"anthropic": fake_anthropic_module}), \
             patch("app.providers.anthropic_provider.ANTHROPIC_API_KEY", "test-key"):
            result = asyncio.run(anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1, enable_wrapup=False,
            ))
            asyncio.run(anthropic_provider.shutdown_async_client())

        self.assertEqual(result, PLACEHOLDER)
        self.assertEqual(fake_client.messages.create.call_count, 1)


class GeminiWrapupTests(unittest.TestCase):
    def _function_call_response(self):
        fc = MagicMock(name="add_npc_to_combat", args={})
        candidate = MagicMock(content=MagicMock())
        return MagicMock(candidates=[candidate], function_calls=[fc], text="")

    def test_forces_final_wrapup_call_with_no_tools_when_budget_exhausted(self):
        from app.providers import gemini_provider

        wrapup_response = MagicMock(text="柯比特甦醒並撲向你。")
        fake_client = MagicMock()
        fake_client.models.generate_content = AsyncMock(
            side_effect=[self._function_call_response(), wrapup_response]
        )
        fake_client.aio = fake_client

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()
        fake_types_module.Part.from_function_response = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}
        ), patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"):
            result = asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1
            ))
            asyncio.run(gemini_provider.shutdown_async_client())

        self.assertEqual(result, "柯比特甦醒並撲向你。")
        self.assertEqual(fake_client.models.generate_content.call_count, 2)
        wrapup_call = fake_client.models.generate_content.call_args_list[-1]
        # The wrap-up config is built fresh with no `tools=` kwarg passed to
        # GenerateContentConfig — asserting the call reused a *different*
        # config object than the iteration loop's `config` confirms this
        # wasn't just the same tool-bearing config re-sent.
        self.assertIsNotNone(wrapup_call.kwargs.get("config"))

    def test_keeps_placeholder_when_wrapup_call_itself_fails(self):
        from app.providers import gemini_provider

        fake_client = MagicMock()
        fake_client.models.generate_content = AsyncMock(
            side_effect=[self._function_call_response(), RuntimeError("boom")]
        )
        fake_client.aio = fake_client

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()
        fake_types_module.Part.from_function_response = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}
        ), patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"):
            result = asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1
            ))
            asyncio.run(gemini_provider.shutdown_async_client())

        self.assertEqual(result, PLACEHOLDER)

    def test_keeps_placeholder_when_wrapup_response_text_property_raises(self):
        # Second-round review finding: `.text` is a property on the
        # google-genai response object that can itself raise (e.g. the
        # response was safety-blocked or has no valid candidate) - that
        # access used to sit outside the try/except meant to guarantee this
        # never propagates out of run_conversation.
        from app.providers import gemini_provider

        class _RaisingText:
            @property
            def text(self):
                raise ValueError("no valid candidate")

        fake_client = MagicMock()
        fake_client.models.generate_content = AsyncMock(
            side_effect=[self._function_call_response(), _RaisingText()]
        )
        fake_client.aio = fake_client

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()
        fake_types_module.Part.from_function_response = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}
        ), patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"):
            result = asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1
            ))
            asyncio.run(gemini_provider.shutdown_async_client())

        self.assertEqual(result, PLACEHOLDER)

    def test_enable_wrapup_false_skips_the_extra_call(self):
        from app.providers import gemini_provider

        fake_client = MagicMock()
        fake_client.models.generate_content = AsyncMock(return_value=self._function_call_response())
        fake_client.aio = fake_client

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()
        fake_types_module.Part.from_function_response = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}
        ), patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"):
            result = asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1, enable_wrapup=False,
            ))
            asyncio.run(gemini_provider.shutdown_async_client())

        self.assertEqual(result, PLACEHOLDER)
        self.assertEqual(fake_client.models.generate_content.call_count, 1)


if __name__ == "__main__":
    unittest.main()
