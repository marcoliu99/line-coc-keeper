"""Actual SDK transports cannot retry behind a single durable reservation."""
import copy
import importlib
from types import SimpleNamespace

import httpx
import openai
import pytest

from app import config, markitdown_shim, pdf_image_transcription, pdf_layout_adapters


@pytest.mark.parametrize('failure', ['429', 'timeout'])
@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'gemini'])
def test_sdk_transport_is_single_attempt_per_durable_reservation(monkeypatch, provider, failure):
    import anthropic
    import markitdown
    from google import genai
    from google.genai import types

    transport_sdk = (importlib.import_module(anthropic.DefaultHttpxClient.mro()[1].__module__.split('.')[0])
                     if provider == 'anthropic' else httpx)
    budget = pdf_layout_adapters.new_budget()
    saved = []
    requests = []
    policies = []
    timeout = 0.25
    def checkpoint(current):
        saved.append(copy.deepcopy(current))
    def reserve():
        return pdf_image_transcription.reserve_verification(budget, 1, checkpoint)
    def transport(request):
        assert saved[-1]['consumed_requests'] == 1  # Persisted before actual dispatch.
        requests.append(request)
        if failure == 'timeout':
            raise transport_sdk.ReadTimeout('Private provider body must not be recorded', request=request)
        return transport_sdk.Response(429, request=request, json={'error': {'message': 'Rate limited',
            'type': 'rate_limit_error', 'status': 'RESOURCE_EXHAUSTED'}})
    http_client = transport_sdk.Client(transport=transport_sdk.MockTransport(transport))
    if provider in {'openai', 'anthropic'}:
        sdk = openai if provider == 'openai' else anthropic
        name = 'OpenAI' if provider == 'openai' else 'Anthropic'
        real = getattr(sdk, name)
        def client(**options):
            policies.append(options)
            return real(http_client=http_client, **options)
        monkeypatch.setattr(sdk, name, client)
    else:
        real = genai.Client
        def client(**options):
            opts = options.get('http_options') or types.HttpOptions()
            policies.append(opts)
            opts.httpx_client = http_client
            options['http_options'] = opts
            return real(**options)
        monkeypatch.setattr(genai, 'Client', client)
    monkeypatch.setattr(markitdown_shim, 'ANALYSIS_PROVIDER', provider)
    monkeypatch.setattr(markitdown_shim, provider.upper() + '_API_KEY', 'fake-test-key')
    monkeypatch.setattr(config, 'PDF_LAYOUT_IMAGE_TIMEOUT_SECONDS', timeout)
    def converter(**options):
        def convert(_file):
            return options['llm_client'].chat.completions.create(model='test-model', messages=[{
                'role': 'user', 'content': [{'type': 'text', 'text': 'Neutral test image'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}}]}])
        return SimpleNamespace(convert=convert)
    monkeypatch.setattr(markitdown, 'MarkItDown', converter)
    engine = markitdown_shim.build_markitdown('Neutral test prompt', image_ocr_evidence=[], reserve_image_request=reserve)
    expected_error = (openai.APIError if provider == 'openai' else anthropic.APIError
                      if provider == 'anthropic' else (httpx.ReadTimeout, genai.errors.APIError))
    with pytest.raises(expected_error):
        engine.convert('synthetic-test.png')
    assert budget['consumed_requests'] == 1
    assert len(saved) == len(requests) == 1
    assert budget['remaining_requests'] == budget['configured_max_requests'] - 1
    if provider == 'gemini':
        assert policies[0].timeout == 250
        assert policies[0].retry_options.attempts == 1
    else:
        assert policies[0]['max_retries'] == 0
        assert policies[0]['timeout'] == timeout
    assert requests[0].extensions['timeout']['read'] == timeout
