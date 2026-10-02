import httpx
import openai
import pytest

from app.providers import openai_provider


@pytest.mark.parametrize('failure', ['429', 'timeout', 'connection', 'unsupported_parameter'])
def test_zero_retry_text_has_exactly_one_sdk_transport(monkeypatch, failure):
    calls = []
    def transport(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('private body', request=request)
        if failure == 'connection':
            raise httpx.ConnectError('private body', request=request)
        status = 400 if failure == 'unsupported_parameter' else 429
        return httpx.Response(status, json={'error': {'message': 'unsupported parameter: temperature',
            'param': 'temperature', 'code': 'unsupported_parameter'}}, request=request)
    real = openai.OpenAI
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: real(http_client=httpx.Client(
        transport=httpx.MockTransport(transport)), **kw))
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-key')
    assert openai_provider.analyze_text('bounded evidence', {'name': 'test', 'description': 'test',
        'input_schema': {'type': 'object'}}, 'extract', timeout=.2, max_retries=0) is None
    assert len(calls) == 1
    assert calls[0].extensions['timeout']['read'] == .2

@pytest.mark.parametrize('failure', ['429', 'timeout', 'connection'])
def test_region_repair_reserves_before_transport_and_never_refunds(monkeypatch, failure):
    import copy

    import pymupdf

    from app import config, pdf_ai_repair, pdf_quality
    from app.providers import registry
    ledger = {}
    saved = []
    calls = []
    def transport(request):
        assert saved[-1]['consumed_requests'] == 1
        assert saved[-1]['attempts']
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('private body', request=request)
        if failure == 'connection':
            raise httpx.ConnectError('private body', request=request)
        return httpx.Response(429, request=request, json={'error': {'message': 'limited'}})
    real = openai.OpenAI
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: real(http_client=httpx.Client(
        transport=httpx.MockTransport(transport)), **kw))
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-key')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, openai_provider)
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 100), 'STR')
        evidence = pdf_quality.block_evidence(page)
        row = {'numeric_pairs': pdf_quality.numeric_pairs(evidence), 'evidence': evidence, 'local_repairs': []}
        checkpoint = lambda value: saved.append(copy.deepcopy(value))
        text, _ = pdf_ai_repair.repair_page(page, row, 'STR', [1], ledger=ledger, checkpoint=checkpoint)
        pdf_ai_repair.repair_page(page, row, 'STR', [1], ledger=ledger, checkpoint=checkpoint)
    assert text == 'STR'
    assert len(calls) == ledger['consumed_requests'] == 1
    assert calls[0].extensions['timeout']['read'] == config.PDF_LAYOUT_IMAGE_TIMEOUT_SECONDS


def test_bounded_source_reservation_and_failed_dispatch_reused(monkeypatch, tmp_path):
    import json

    from app import config, source_analysis
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    source = '\n'.join(f'--- 第 {i} 頁 ---\nPage evidence {i}' for i in range(1, 11))
    calls = []
    def transport(request):
        records = list((tmp_path / '.source-analysis').glob('*.json'))
        assert len(records) == 1 and json.loads(records[0].read_text())['status'] == 'reserved'
        calls.append(request)
        payload = json.loads(request.content)
        sent = payload['input'][0]['content']
        assert source not in sent and 'Page evidence 10' not in sent
        return httpx.Response(429, request=request, json={'error': {'message': 'limited'}})
    real = openai.OpenAI
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: real(http_client=httpx.Client(
        transport=httpx.MockTransport(transport)), **kw))
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-key')
    tool = {'name': 'report_opening_narration', 'description': 'test', 'input_schema': {'type': 'object'}}
    assert source_analysis.analyze(openai_provider, source, tool, 'extract') is None
    assert source_analysis.analyze(openai_provider, source, tool, 'extract') is None
    assert len(calls) == 1


def test_optional_analysis_storage_failure_never_dispatches(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from app import config, source_analysis
    invalid = tmp_path / 'file'
    invalid.write_text('not a directory')
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', invalid)
    provider = Mock()
    assert source_analysis.analyze(provider, 'bounded source', {'name': 'test'}, 'extract') is None
    provider.analyze_text.assert_not_called()


def test_optional_analysis_corrupt_reservation_never_redispatches(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from app import config, source_analysis
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    provider = Mock()
    provider.analyze_text.return_value = None
    tool = {'name': 'test'}
    assert source_analysis.analyze(provider, 'bounded source', tool, 'extract') is None
    next((tmp_path / '.source-analysis').glob('*.json')).write_text('broken JSON')
    assert source_analysis.analyze(provider, 'bounded source', tool, 'extract') is None
    assert provider.analyze_text.call_count == 1


def test_zero_retry_text_success_has_one_transport(monkeypatch):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, request=request, json={
            'id': 'resp_test', 'object': 'response', 'created_at': 1, 'model': 'test',
            'output': [{'type': 'function_call', 'id': 'call_test', 'call_id': 'call_test',
                        'name': 'test', 'arguments': '{"safe":true}'}]})
    real = openai.OpenAI
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: real(http_client=httpx.Client(
        transport=httpx.MockTransport(transport)), **kw))
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-key')
    assert openai_provider.analyze_text('bounded', {'name': 'test', 'description': 'test',
        'input_schema': {'type': 'object'}}, 'extract', timeout=.2, max_retries=0) == {'safe': True}
    assert len(calls) == 1
