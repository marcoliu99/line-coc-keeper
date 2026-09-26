import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers import openai_provider, retry


class StatusError(Exception):
    def __init__(self, status, message="error", headers=None, body=None):
        super().__init__(message)
        self.status_code = status
        self.response = SimpleNamespace(headers=headers or {})
        self.body = body


def test_only_numeric_allowlisted_rate_limit_headers_are_logged():
    exc = StatusError(429, headers={
        "X-RateLimit-Limit-Tokens": "9000", "x-ratelimit-remaining-tokens": "0",
        "x-ratelimit-reset-tokens": "1m2.5s", "x-ratelimit-limit-requests": "SECRET",
        "Authorization": "SECRET", "x-ratelimit-reset-requests": "SECRET",
    })
    fields = retry._extract_safe_error_fields(exc)
    assert fields['rate_limit_limit_tokens'] == 9000
    assert fields['rate_limit_remaining_tokens'] == 0
    assert fields['rate_limit_reset_tokens'] == '1m2.5s'
    assert 'SECRET' not in str(fields) and 'Authorization' not in fields


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', 'bad'])
def test_invalid_retry_after_is_not_scheduled(value):
    assert retry._extract_retry_after_seconds(StatusError(429, headers={'Retry-After': value})) is None


def test_retry_event_distinguishes_server_wait_from_jitter():
    fn = AsyncMock(side_effect=[StatusError(429, headers={'retry-after': '2'}), 'ok'])
    with patch.object(retry.observability, 'event') as event, patch.object(retry.asyncio, 'sleep', AsyncMock()) as sleep:
        assert asyncio.run(retry.async_call_with_retry(fn, provider='test', operation='generate')) == 'ok'
    data = next(c.kwargs for c in event.call_args_list if c.args[0] == 'llm.request.retry')
    assert data['delay_source'] == 'retry_after' and data['retry_after_s'] == 2
    sleep.assert_awaited_once_with(2)


@pytest.mark.parametrize('status', [401, 429, 500])
def test_unrelated_error_cannot_strip_reasoning_or_temperature(status):
    exc = StatusError(status, "Unsupported parameter: 'temperature'", body={'param': 'reasoning', 'code': 'unsupported_parameter'})
    client = SimpleNamespace(responses=SimpleNamespace(create=MagicMock(side_effect=exc)))
    with patch.object(openai_provider, '_unsupported_params', set()), patch.object(openai_provider, 'LLM_MAX_RETRIES', 0):
        with pytest.raises(StatusError):
            openai_provider._create_response(client, model='model', temperature=.6, reasoning={'effort': 'medium'})
        assert openai_provider._unsupported_params == set()
        assert client.responses.create.call_count == 1


def test_specific_parameter_400_is_negotiated_without_extra_review():
    exc = StatusError(400, body={'param': 'temperature', 'code': 'unsupported_parameter'})
    client = SimpleNamespace(responses=SimpleNamespace(create=MagicMock(side_effect=[exc, 'ok'])))
    with patch.object(openai_provider, '_unsupported_params', set()):
        assert openai_provider._create_response(client, model='model', temperature=.6) == 'ok'
        assert client.responses.create.call_count == 2
        assert 'temperature' not in client.responses.create.call_args.kwargs


def test_explicit_temperature_omit_prevents_first_invalid_request():
    client = SimpleNamespace(responses=SimpleNamespace(create=MagicMock(return_value='ok')))
    with patch.object(openai_provider.config, 'OPENAI_OMIT_TEMPERATURE', True):
        assert openai_provider._create_response(client, model='model', temperature=.6) == 'ok'
    assert client.responses.create.call_count == 1
    assert 'temperature' not in client.responses.create.call_args.kwargs
