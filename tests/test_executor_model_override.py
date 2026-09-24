"""Tests for docs/specs/enhancement-executor-model-tiering-and-tool-
scoping.md's model_override/reasoning_effort_override plumbing: each
provider's run_conversation must send the override (when given) instead of
its plain *_MODEL/KEEPER_REASONING_EFFORT config, and must fall back to the
usual config when no override is given (None) — the call shape every other
caller (app/keeper.py's legacy run_turn, app/agents/narrator.py,
app/agents/guard.py) already relies on, unaffected by this feature.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


async def _execute_tool(_name, _args):
    return {"ok": True}


class OpenAIModelOverrideTests(unittest.TestCase):
    def _plain_text_response(self):
        return MagicMock(output=[], output_text="好的。", id="resp_1")

    def test_model_override_and_reasoning_effort_override_are_sent(self):
        from app.providers import openai_provider

        fake_client = MagicMock()
        fake_client.responses.create = AsyncMock(return_value=self._plain_text_response())
        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}), \
             patch("app.providers.openai_provider.OPENAI_API_KEY", "test-key"), \
             patch("app.providers.openai_provider.OPENAI_MODEL", "gpt-5.6-luna"), \
             patch("app.providers.openai_provider.KEEPER_REASONING_EFFORT", "medium"):
            asyncio.run(openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1,
                model_override="gpt-6-luna", reasoning_effort_override="none",
            ))
            asyncio.run(openai_provider.shutdown_async_client())

        sent_kwargs = fake_client.responses.create.call_args.kwargs
        self.assertEqual(sent_kwargs["model"], "gpt-6-luna")
        self.assertEqual(sent_kwargs["reasoning"], {"effort": "none"})

    def test_no_override_falls_back_to_plain_config(self):
        from app.providers import openai_provider

        fake_client = MagicMock()
        fake_client.responses.create = AsyncMock(return_value=self._plain_text_response())
        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}), \
             patch("app.providers.openai_provider.OPENAI_API_KEY", "test-key"), \
             patch("app.providers.openai_provider.OPENAI_MODEL", "gpt-5.6-luna"), \
             patch("app.providers.openai_provider.KEEPER_REASONING_EFFORT", "medium"):
            asyncio.run(openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1,
            ))
            asyncio.run(openai_provider.shutdown_async_client())

        sent_kwargs = fake_client.responses.create.call_args.kwargs
        self.assertEqual(sent_kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(sent_kwargs["reasoning"], {"effort": "medium"})


class AnthropicModelOverrideTests(unittest.TestCase):
    def _plain_text_response(self):
        block = MagicMock(type="text", text="好的。")
        return MagicMock(content=[block])

    def test_model_override_is_sent(self):
        from app.providers import anthropic_provider

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=self._plain_text_response())
        fake_anthropic_module = MagicMock()
        fake_anthropic_module.AsyncAnthropic = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"anthropic": fake_anthropic_module}), \
             patch("app.providers.anthropic_provider.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.providers.anthropic_provider.ANTHROPIC_MODEL", "claude-sonnet-5"):
            asyncio.run(anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1,
                model_override="claude-haiku-4-5-20251001",
            ))
            asyncio.run(anthropic_provider.shutdown_async_client())

        sent_kwargs = fake_client.messages.create.call_args.kwargs
        self.assertEqual(sent_kwargs["model"], "claude-haiku-4-5-20251001")

    def test_no_override_falls_back_to_plain_config(self):
        from app.providers import anthropic_provider

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=self._plain_text_response())
        fake_anthropic_module = MagicMock()
        fake_anthropic_module.AsyncAnthropic = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"anthropic": fake_anthropic_module}), \
             patch("app.providers.anthropic_provider.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.providers.anthropic_provider.ANTHROPIC_MODEL", "claude-sonnet-5"):
            asyncio.run(anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1,
            ))
            asyncio.run(anthropic_provider.shutdown_async_client())

        sent_kwargs = fake_client.messages.create.call_args.kwargs
        self.assertEqual(sent_kwargs["model"], "claude-sonnet-5")


class GeminiModelOverrideTests(unittest.TestCase):
    def _plain_text_response(self):
        return MagicMock(text="好的。", function_calls=[])

    def test_model_override_is_sent(self):
        from app.providers import gemini_provider

        fake_client = MagicMock()
        fake_client.models.generate_content = AsyncMock(return_value=self._plain_text_response())
        fake_client.aio = fake_client

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()
        fake_types_module.Part.from_function_response = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}
        ), patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"), \
                patch("app.providers.gemini_provider.GEMINI_MODEL", "gemini-flash-latest"):
            asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1,
                model_override="gemini-pro-latest",
            ))
            asyncio.run(gemini_provider.shutdown_async_client())

        sent_kwargs = fake_client.models.generate_content.call_args.kwargs
        self.assertEqual(sent_kwargs["model"], "gemini-pro-latest")

    def test_no_override_falls_back_to_plain_config(self):
        from app.providers import gemini_provider

        fake_client = MagicMock()
        fake_client.models.generate_content = AsyncMock(return_value=self._plain_text_response())
        fake_client.aio = fake_client

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()
        fake_types_module.Part.from_function_response = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}
        ), patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"), \
                patch("app.providers.gemini_provider.GEMINI_MODEL", "gemini-flash-latest"):
            asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", _execute_tool, 1,
            ))
            asyncio.run(gemini_provider.shutdown_async_client())

        sent_kwargs = fake_client.models.generate_content.call_args.kwargs
        self.assertEqual(sent_kwargs["model"], "gemini-flash-latest")


if __name__ == "__main__":
    unittest.main()
