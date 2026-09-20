"""Tests for app/providers/retry.py and its wiring into the three provider
adapters — see app/config.py's LLM_MAX_RETRIES/LLM_RETRY_BASE_DELAY_SECONDS.

None of the three providers retried a transient connection/server failure
before this: client.messages.create/generate_content/responses.create each
ran with no try/except at all (openai_provider.py's existing loop only
handled a model rejecting an unsupported parameter, a completely different
failure), so a single network blip raised straight out of run_conversation
and lost the player's whole turn. These tests cover the classifier, the
shared retry helper, and each provider's actual wiring.
"""
import time
import unittest
from unittest.mock import MagicMock, patch

from app.providers import retry


class RetryableConnectionError(Exception):
    """Stand-in for anthropic/openai's APIConnectionError — retry.py
    classifies by class name, not by importing each SDK's real hierarchy
    (see its own docstring), so a same-named fake is a faithful test double
    without fighting real SDK constructors that expect httpx Request/
    Response objects."""


class RetryableTimeoutError(Exception):
    pass


class UnrelatedValueError(Exception):
    pass


class FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class IsRetryableTests(unittest.TestCase):
    def test_connection_and_timeout_errors_are_retryable_by_name(self):
        self.assertTrue(retry.is_retryable(RetryableConnectionError()))
        self.assertTrue(retry.is_retryable(RetryableTimeoutError()))

    def test_unrelated_exception_is_not_retryable(self):
        self.assertFalse(retry.is_retryable(UnrelatedValueError()))

    def test_5xx_status_code_is_retryable_regardless_of_class_name(self):
        self.assertTrue(retry.is_retryable(FakeStatusError(500)))
        self.assertTrue(retry.is_retryable(FakeStatusError(503)))

    def test_4xx_status_code_is_not_retryable(self):
        self.assertFalse(retry.is_retryable(FakeStatusError(400)))
        self.assertFalse(retry.is_retryable(FakeStatusError(429)))


class CallWithRetryTests(unittest.TestCase):
    def test_succeeds_on_first_try_without_sleeping(self):
        fn = MagicMock(return_value="ok")
        with patch("app.providers.retry.time.sleep") as sleep_mock:
            result = retry.call_with_retry(fn, provider="test", operation="op")
        self.assertEqual(result, "ok")
        fn.assert_called_once()
        sleep_mock.assert_not_called()

    def test_retries_transient_failure_then_succeeds(self):
        fn = MagicMock(side_effect=[RetryableConnectionError(), RetryableConnectionError(), "ok"])
        with patch("app.providers.retry.time.sleep") as sleep_mock, \
             patch("app.providers.retry.LLM_MAX_RETRIES", 3), \
             patch("app.providers.retry.LLM_RETRY_BASE_DELAY_SECONDS", 1.0):
            result = retry.call_with_retry(fn, provider="test", operation="op")
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 3)
        # Exponential backoff: 1s then 2s.
        sleep_mock.assert_has_calls([unittest.mock.call(1.0), unittest.mock.call(2.0)])

    def test_exhausts_retries_and_reraises(self):
        fn = MagicMock(side_effect=RetryableConnectionError("still broken"))
        with patch("app.providers.retry.time.sleep"), \
             patch("app.providers.retry.LLM_MAX_RETRIES", 2):
            with self.assertRaises(RetryableConnectionError):
                retry.call_with_retry(fn, provider="test", operation="op")
        self.assertEqual(fn.call_count, 3)  # initial attempt + 2 retries

    def test_non_retryable_failure_raises_immediately_without_sleeping(self):
        fn = MagicMock(side_effect=UnrelatedValueError("not our problem"))
        with patch("app.providers.retry.time.sleep") as sleep_mock, \
             patch("app.providers.retry.LLM_MAX_RETRIES", 3):
            with self.assertRaises(UnrelatedValueError):
                retry.call_with_retry(fn, provider="test", operation="op")
        fn.assert_called_once()
        sleep_mock.assert_not_called()

    def test_zero_max_retries_disables_retrying_entirely(self):
        fn = MagicMock(side_effect=RetryableConnectionError())
        with patch("app.providers.retry.time.sleep") as sleep_mock, \
             patch("app.providers.retry.LLM_MAX_RETRIES", 0):
            with self.assertRaises(RetryableConnectionError):
                retry.call_with_retry(fn, provider="test", operation="op")
        fn.assert_called_once()
        sleep_mock.assert_not_called()


class OpenAICreateResponseRetryTests(unittest.TestCase):
    """openai_provider._create_response merges connection-retry into its
    existing unsupported-parameter retry loop — see that function's
    docstring for why it doesn't just delegate to call_with_retry."""

    def _fake_client(self, side_effect):
        client = MagicMock()
        client.responses.create = MagicMock(side_effect=side_effect)
        return client

    def test_retries_connection_error_then_succeeds(self):
        from app.providers import openai_provider

        fake_response = MagicMock()
        client = self._fake_client([RetryableConnectionError(), fake_response])
        with patch("app.providers.openai_provider.time.sleep") as sleep_mock, \
             patch("app.providers.openai_provider.LLM_MAX_RETRIES", 3), \
             patch("app.providers.openai_provider.LLM_RETRY_BASE_DELAY_SECONDS", 1.0):
            result = openai_provider._create_response(client, model="gpt-test", input=[])
        self.assertIs(result, fake_response)
        self.assertEqual(client.responses.create.call_count, 2)
        sleep_mock.assert_called_once_with(1.0)

    def test_exhausts_connection_retries_and_reraises(self):
        from app.providers import openai_provider

        client = self._fake_client(RetryableConnectionError("down"))
        with patch("app.providers.openai_provider.time.sleep"), \
             patch("app.providers.openai_provider.LLM_MAX_RETRIES", 2):
            with self.assertRaises(RetryableConnectionError):
                openai_provider._create_response(client, model="gpt-test", input=[])
        self.assertEqual(client.responses.create.call_count, 3)

    def test_unsupported_parameter_path_is_unaffected_by_connection_retry(self):
        """Regression: the pre-existing temperature/reasoning-stripping
        retry must keep working exactly as before, and must not consume the
        connection-retry budget."""
        from app.providers import openai_provider

        openai_provider._unsupported_params.clear()
        fake_response = MagicMock()
        client = self._fake_client([Exception("Unsupported parameter: 'temperature'"), fake_response])
        with patch("app.providers.openai_provider.time.sleep") as sleep_mock:
            result = openai_provider._create_response(client, model="gpt-test", input=[], temperature=0.6)
        self.assertIs(result, fake_response)
        self.assertNotIn("temperature", client.responses.create.call_args.kwargs)
        sleep_mock.assert_not_called()  # param-strip retry has no backoff delay
        openai_provider._unsupported_params.discard("temperature")

    def test_non_retryable_error_raises_immediately(self):
        from app.providers import openai_provider

        client = self._fake_client(UnrelatedValueError("bad request"))
        with patch("app.providers.openai_provider.time.sleep") as sleep_mock:
            with self.assertRaises(UnrelatedValueError):
                openai_provider._create_response(client, model="gpt-test", input=[])
        client.responses.create.assert_called_once()
        sleep_mock.assert_not_called()


class AnthropicProviderRetryWiringTests(unittest.TestCase):
    def test_run_conversation_retries_transient_failure_then_succeeds(self):
        from app.providers import anthropic_provider

        text_block = MagicMock(type="text", text="回覆")
        fake_response = MagicMock(content=[text_block])
        fake_client = MagicMock()
        fake_client.messages.create = MagicMock(side_effect=[RetryableConnectionError(), fake_response])

        fake_anthropic_module = MagicMock()
        fake_anthropic_module.Anthropic = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"anthropic": fake_anthropic_module}), \
             patch("app.providers.anthropic_provider.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.providers.retry.time.sleep") as sleep_mock, \
             patch("app.providers.retry.LLM_MAX_RETRIES", 3):
            result = anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", lambda name, args: {}, 1
            )
        self.assertEqual(result, "回覆")
        self.assertEqual(fake_client.messages.create.call_count, 2)
        sleep_mock.assert_called_once()


class GeminiProviderRetryWiringTests(unittest.TestCase):
    def test_run_conversation_retries_transient_failure_then_succeeds(self):
        from app.providers import gemini_provider

        fake_candidate = MagicMock(content=MagicMock())
        fake_response = MagicMock(candidates=[fake_candidate], function_calls=[], text="回覆")
        fake_client = MagicMock()
        fake_client.models.generate_content = MagicMock(side_effect=[RetryableConnectionError(), fake_response])

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()

        with patch.dict("sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}), \
             patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"), \
             patch("app.providers.retry.time.sleep") as sleep_mock, \
             patch("app.providers.retry.LLM_MAX_RETRIES", 3):
            result = gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", lambda name, args: {}, 1
            )
        self.assertEqual(result, "回覆")
        self.assertEqual(fake_client.models.generate_content.call_count, 2)
        sleep_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
