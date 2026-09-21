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
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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


# httpx's real transport exception names (httpx._exceptions) — PR review
# caught that google-genai (and potentially other SDKs) can raise these
# directly, unwrapped, and none of them contained the original marker
# strings (e.g. "ConnectError" has no "connectionerror" substring).
class ConnectError(Exception):
    pass


class ReadError(Exception):
    pass


class RemoteProtocolError(Exception):
    pass


class PoolTimeout(Exception):
    pass


class WrapsHttpxError(Exception):
    """Stand-in for an SDK exception that wraps the real transport failure
    as its __cause__ (`raise SomeSDKError(...) from httpx_error`) without
    the outer class's own name matching anything retryable."""


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
        self.assertTrue(retry.is_retryable(FakeStatusError(429)))

    def test_provider_error_classification_distinguishes_retry_classes(self):
        self.assertEqual(retry.classify_exception(FakeStatusError(429)), retry.ProviderError.RATE_LIMITED)
        self.assertEqual(retry.classify_exception(FakeStatusError(500)), retry.ProviderError.TRANSIENT_SERVER)
        self.assertEqual(retry.classify_exception(FakeStatusError(401)), retry.ProviderError.AUTH_FAILED)
        self.assertEqual(retry.classify_exception(FakeStatusError(404)), retry.ProviderError.MODEL_NOT_FOUND)
        self.assertEqual(retry.classify_exception(FakeStatusError(400)), retry.ProviderError.INVALID_REQUEST)

    def test_httpx_transport_exceptions_are_retryable_by_name(self):
        self.assertTrue(retry.is_retryable(ConnectError()))
        self.assertTrue(retry.is_retryable(ReadError()))
        self.assertTrue(retry.is_retryable(RemoteProtocolError()))
        self.assertTrue(retry.is_retryable(PoolTimeout()))

    def test_wrapped_transport_exception_is_retryable_via_cause_chain(self):
        try:
            try:
                raise ConnectError("connection refused")
            except ConnectError as cause:
                raise WrapsHttpxError("wrapped") from cause
        except WrapsHttpxError as exc:
            self.assertTrue(retry.is_retryable(exc))

    def test_wrapped_unrelated_exception_stays_non_retryable(self):
        try:
            try:
                raise UnrelatedValueError("bad input")
            except UnrelatedValueError as cause:
                raise WrapsHttpxError("wrapped") from cause
        except WrapsHttpxError as exc:
            self.assertFalse(retry.is_retryable(exc))


class CallWithRetryTests(unittest.TestCase):
    def test_succeeds_on_first_try_without_sleeping(self):
        fn = MagicMock(return_value="ok")
        with patch("app.providers.retry.time.sleep") as sleep_mock:
            result = retry.call_with_retry(fn, provider="test", operation="op")
        self.assertEqual(result, "ok")
        fn.assert_called_once()
        sleep_mock.assert_not_called()

    def test_async_retry_uses_async_sleep_and_preserves_cancellation(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RetryableConnectionError()
            return "ok"

        async def run():
            with patch("app.providers.retry.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
                result = await retry.async_call_with_retry(fn, provider="test", operation="op")
            return result, sleep_mock

        result, sleep_mock = asyncio.run(run())
        self.assertEqual(result, "ok")
        sleep_mock.assert_awaited_once()

    def test_async_retry_does_not_swallow_cancellation(self):
        async def fn():
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(retry.async_call_with_retry(fn, provider="test", operation="op"))

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
        with (
            patch("app.providers.retry.time.sleep"),
            patch("app.providers.retry.LLM_MAX_RETRIES", 2),
            self.assertRaises(RetryableConnectionError),
        ):
            retry.call_with_retry(fn, provider="test", operation="op")
        self.assertEqual(fn.call_count, 3)  # initial attempt + 2 retries

    def test_non_retryable_failure_raises_immediately_without_sleeping(self):
        fn = MagicMock(side_effect=UnrelatedValueError("not our problem"))
        with (
            patch("app.providers.retry.time.sleep") as sleep_mock,
            patch("app.providers.retry.LLM_MAX_RETRIES", 3),
            self.assertRaises(UnrelatedValueError),
        ):
            retry.call_with_retry(fn, provider="test", operation="op")
        fn.assert_called_once()
        sleep_mock.assert_not_called()

    def test_zero_max_retries_disables_retrying_entirely(self):
        fn = MagicMock(side_effect=RetryableConnectionError())
        with (
            patch("app.providers.retry.time.sleep") as sleep_mock,
            patch("app.providers.retry.LLM_MAX_RETRIES", 0),
            self.assertRaises(RetryableConnectionError),
        ):
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
        with (
            patch("app.providers.openai_provider.time.sleep"),
            patch("app.providers.openai_provider.LLM_MAX_RETRIES", 2),
            self.assertRaises(RetryableConnectionError),
        ):
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
        with patch("app.providers.openai_provider.time.sleep") as sleep_mock, self.assertRaises(
            UnrelatedValueError
        ):
            openai_provider._create_response(client, model="gpt-test", input=[])
        client.responses.create.assert_called_once()
        sleep_mock.assert_not_called()


class OpenAIClientConstructionTests(unittest.TestCase):
    def test_run_conversation_disables_sdk_default_retries(self):
        from app.providers import openai_provider

        fake_response = MagicMock(output=[], output_text="回覆", id="resp_1")
        fake_client = MagicMock()
        fake_client.responses.create = MagicMock(return_value=fake_response)

        fake_openai_module = MagicMock()
        fake_client.responses.create = AsyncMock(return_value=fake_response)
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}), \
             patch("app.providers.openai_provider.OPENAI_API_KEY", "test-key"):
            async def execute_tool(_name, _args):
                return {}

            result = asyncio.run(openai_provider.run_conversation(
                "static", "dynamic", [], [], "hello", execute_tool, 1
            ))
            asyncio.run(openai_provider.shutdown_async_client())

        self.assertEqual(result, "回覆")
        # See app/providers/anthropic_provider.py's identical fix — the
        # SDK's own default retrying (max_retries=2) must be disabled so
        # retry.call_with_retry's LLM_MAX_RETRIES budget is the only one in
        # effect.
        fake_openai_module.AsyncOpenAI.assert_called_once_with(api_key="test-key", max_retries=0)


class AnthropicProviderRetryWiringTests(unittest.TestCase):
    def test_run_conversation_retries_transient_failure_then_succeeds(self):
        from app.providers import anthropic_provider

        text_block = MagicMock(type="text", text="回覆")
        fake_response = MagicMock(content=[text_block])
        fake_client = MagicMock()
        fake_client.messages.create = MagicMock(side_effect=[RetryableConnectionError(), fake_response])

        fake_anthropic_module = MagicMock()
        fake_client.messages.create = AsyncMock(side_effect=[RetryableConnectionError(), fake_response])
        fake_anthropic_module.AsyncAnthropic = MagicMock(return_value=fake_client)

        with patch.dict("sys.modules", {"anthropic": fake_anthropic_module}), \
             patch("app.providers.anthropic_provider.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.providers.retry.asyncio.sleep", new_callable=AsyncMock) as sleep_mock, \
             patch("app.providers.retry.LLM_MAX_RETRIES", 3):
            async def execute_tool(_name, _args):
                return {}

            result = asyncio.run(anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", execute_tool, 1
            ))
            asyncio.run(anthropic_provider.shutdown_async_client())
        self.assertEqual(result, "回覆")
        self.assertEqual(fake_client.messages.create.call_count, 2)
        sleep_mock.assert_awaited_once()
        # PR review finding: the SDK's own default retrying (max_retries=2)
        # must be disabled so our retry.call_with_retry budget is the only
        # one in effect, instead of stacking on top of the SDK's.
        fake_anthropic_module.AsyncAnthropic.assert_called_once_with(api_key="test-key", max_retries=0)


class GeminiProviderRetryWiringTests(unittest.TestCase):
    def test_run_conversation_retries_transient_failure_then_succeeds(self):
        from app.providers import gemini_provider

        fake_candidate = MagicMock(content=MagicMock())
        fake_response = MagicMock(candidates=[fake_candidate], function_calls=[], text="回覆")
        fake_client = MagicMock()
        fake_client.models.generate_content = AsyncMock(side_effect=[RetryableConnectionError(), fake_response])
        fake_client.aio = fake_client

        fake_genai_module = MagicMock()
        fake_genai_module.Client = MagicMock(return_value=fake_client)
        fake_types_module = MagicMock()

        with patch.dict("sys.modules", {"google.genai": fake_genai_module, "google.genai.types": fake_types_module}), \
             patch("app.providers.gemini_provider.GEMINI_API_KEY", "test-key"), \
             patch("app.providers.retry.asyncio.sleep", new_callable=AsyncMock) as sleep_mock, \
             patch("app.providers.retry.LLM_MAX_RETRIES", 3):
            async def execute_tool(_name, _args):
                return {}

            result = asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", execute_tool, 1
            ))
            asyncio.run(gemini_provider.shutdown_async_client())
        self.assertEqual(result, "回覆")
        self.assertEqual(fake_client.models.generate_content.call_count, 2)
        sleep_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
