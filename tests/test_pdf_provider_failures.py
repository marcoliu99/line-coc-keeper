"""Private provider inputs must never appear in map failure diagnostics."""
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app import config, pdf_map_analysis
from app.providers import openai_provider, registry


@pytest.mark.parametrize(('failure', 'error_type', 'status', 'timed_out'), [
    ('timeout', 'APITimeoutError', None, True), ('401', 'AuthenticationError', 401, False),
    ('429', 'RateLimitError', 429, False), ('json', 'JSONDecodeError', None, False),
    ('missing_tool', 'MissingToolCall', None, False),
    ('connection', 'APIConnectionError', None, False),
    ('invalid_arguments', 'InvalidToolArguments', None, False)])
def test_map_records_sanitized_swallowed_provider_failure(monkeypatch, failure, error_type, status, timed_out):
    secret = 'PRIVATE-PROMPT-IMAGE-KEY-BODY'
    request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
    def create(**_options):
        if failure == 'timeout':
            raise openai.APITimeoutError(request=request)
        if failure == 'connection':
            raise openai.APIConnectionError(message=secret, request=request)
        if failure == 'invalid_arguments':
            return SimpleNamespace(output=[SimpleNamespace(type='function_call', name='inventory_map_locations', arguments='[]')])
        if failure in {'401', '429'}:
            response = httpx.Response(int(failure), request=request, json={'private': secret})
            cls = openai.AuthenticationError if failure == '401' else openai.RateLimitError
            raise cls(secret, response=response, body={'private': secret})
        if failure == 'json':
            return SimpleNamespace(output=[SimpleNamespace(type='function_call', name='inventory_map_locations', arguments=secret)])
        return SimpleNamespace(output=[])
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', secret)
    monkeypatch.setattr(openai, 'OpenAI', lambda **_options: SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, openai_provider)
    reservations = []
    def reserve():
        reservations.append(1)
        return True
    result = pdf_map_analysis.analyze(secret.encode(), reserve=reserve, candidate=True)
    assert reservations == [1]
    assert result.analysis['status'] == 'MAP_ANALYSIS_FAILED'
    diagnostic = result.analysis['attempts'][0]['failure']
    assert diagnostic['provider'] == 'openai'
    assert diagnostic['stage'] == 'phase1_generation'
    assert diagnostic['error_type'] == error_type
    assert diagnostic['status_code'] == status
    assert diagnostic['timeout'] is timed_out
    assert diagnostic['elapsed_seconds'] >= 0
    serialized = json.dumps(diagnostic)
    assert secret not in serialized
    assert 'api.openai.com' not in serialized
    assert set(diagnostic) == {'provider', 'stage', 'error_type', 'status_code', 'timeout', 'elapsed_seconds'}


@pytest.mark.parametrize('provider', ['anthropic', 'gemini'])
@pytest.mark.parametrize('failure', ['401', '429', 'timeout', 'missing_tool'])
def test_other_adapters_keep_safe_status_and_timeout_metadata(monkeypatch, provider, failure):
    import importlib

    import anthropic
    from google import genai

    from app.providers import anthropic_provider, gemini_provider, image_diagnostics

    adapter = anthropic_provider if provider == 'anthropic' else gemini_provider
    secret = 'PRIVATE-REQUEST-AND-ERROR-BODY'
    def create(**_options):
        if failure == 'missing_tool':
            return SimpleNamespace(content=[], function_calls=[])
        if provider == 'gemini':
            if failure == 'timeout':
                raise httpx.ReadTimeout(secret)
            raise genai.errors.ClientError(int(failure), {'error': {'message': secret, 'status': 'PRIVATE'}})
        native_http = importlib.import_module(anthropic.DefaultHttpxClient.mro()[1].__module__.split('.')[0])
        request = native_http.Request('POST', 'https://example.test/v1/messages')
        if failure == 'timeout':
            raise anthropic.APITimeoutError(request=request)
        response = native_http.Response(int(failure), request=request)
        cls = anthropic.AuthenticationError if failure == '401' else anthropic.RateLimitError
        raise cls(secret, response=response, body={'private': secret})
    monkeypatch.setattr(adapter, provider.upper() + '_API_KEY', secret)
    if provider == 'anthropic':
        monkeypatch.setattr(anthropic, 'Anthropic', lambda **_options: SimpleNamespace(messages=SimpleNamespace(create=create)))
    else:
        monkeypatch.setattr(genai, 'Client', lambda **_options: SimpleNamespace(models=SimpleNamespace(generate_content=create)))
    tool = {'name': 'test_image', 'description': 'Neutral', 'input_schema': {'type': 'object', 'properties': {}}}
    with image_diagnostics.capture('image_audit') as failures:
        assert adapter.analyze_image(secret.encode(), tool, secret, timeout=.25, max_retries=0) is None
    assert len(failures) == 1
    diagnostic = failures[0]
    assert diagnostic['provider'] == provider
    assert diagnostic['stage'] == 'image_audit'
    assert diagnostic['status_code'] == (int(failure) if failure in {'401', '429'} else None)
    assert diagnostic['timeout'] is (failure == 'timeout')
    assert secret not in json.dumps(diagnostic)
