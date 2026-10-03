"""Map stage deadlines and actual SDK dispatch obey the durable request ledger."""
import copy
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app import config, pdf_image_transcription, pdf_layout_adapters, pdf_map_analysis
from app.providers import openai_provider, registry


def location():
    return {'label': 'Entrance', 'floor_or_section': 'ground', 'id_hint': 'entrance',
            'visible': True, 'kind': 'location', 'evidence': 'Visible entrance label'}


def configure(monkeypatch):
    monkeypatch.setattr(config, 'PDF_MAP_INVENTORY_TIMEOUT_SECONDS', 30)
    monkeypatch.setattr(config, 'PDF_MAP_CONNECTIVITY_TIMEOUT_SECONDS', 60)
    monkeypatch.setattr(config, 'PDF_MAP_REPAIR_TIMEOUT_SECONDS', 60)
    monkeypatch.setattr(config, 'PDF_MAP_AUDIT_TIMEOUT_SECONDS', 60)
    monkeypatch.setattr(config, 'PDF_LAYOUT_MAX_REQUESTS', 8)
    monkeypatch.setattr(config, 'PDF_LAYOUT_MAX_PAGES', 4)


def phase_response(name, payload):
    if name == 'inventory_map_locations':
        return {'page_type': 'map', 'locations': [location()]}
    if name == 'audit_map_inventory':
        return {'complete': True, 'uncertainties': [], 'confirmed_ids': [r['id'] for r in payload['inventory']],
                'missing_locations': []}
    if name == 'extract_map_connectivity':
        return {'entry': {'status': 'resolved', 'room_id': payload['inventory'][0]['id'],
                          'evidence': 'Visible entrance'}, 'edges': [], 'missing_locations': []}
    graph = payload['graph']
    return {'complete': True, 'uncertainties': [],
        'visible_locations': [{'label': room['visible_label'], 'room_id': room['id']} for room in graph['rooms']],
        'rooms': [{'room_id': room['id'], 'verdict': 'supported', 'evidence': 'Original label'} for room in graph['rooms']],
        'edges': [], 'entry': {'room_id': graph['entry_room_id'], 'verdict': 'supported', 'evidence': 'Visible entrance'}}


def test_phase2_over_thirty_seconds_succeeds_under_stage_deadline_without_sleep(monkeypatch):
    configure(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(pdf_map_analysis.time, 'perf_counter', lambda: clock[0])
    durations = {'inventory_map_locations': 12, 'audit_map_inventory': 15,
                 'extract_map_connectivity': 35, 'audit_scene_map_image': 50}
    calls = []
    saved = []
    budget = pdf_layout_adapters.new_budget()
    def analyze(_png, tool, prompt, **options):
        name = tool['name']
        calls.append(name)
        assert saved[-1]['consumed_requests'] == len(calls)
        assert options['max_retries'] == 0
        expected = 30 if name in {'inventory_map_locations', 'audit_map_inventory'} else 60
        assert options['timeout'] == expected
        assert durations[name] < options['timeout']
        clock[0] += durations[name]
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        return phase_response(name, payload)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=analyze))
    def reserve():
        return pdf_image_transcription.reserve_verification(budget, 7, lambda ledger: saved.append(copy.deepcopy(ledger)))
    result = pdf_map_analysis.analyze(b'neutral-image', reserve=reserve, candidate=True)
    assert result.analysis['status'] == 'MAP_GRAPH_VERIFIED'
    assert [a['elapsed_seconds'] for a in result.analysis['attempts']] == [12, 15, 35, 50]
    assert budget['consumed_requests'] == len(saved) == len(calls) == 4
    assert budget['visited_pages'] == [7]
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'neutral-image')


@pytest.mark.parametrize(('failure_kind', 'error_type', 'status', 'timeout'), [
    ('timeout', 'APITimeoutError', None, True), ('429', 'RateLimitError', 429, False),
    ('connection', 'APIConnectionError', None, False)])
def test_phase2_actual_sdk_failure_consumes_one_transport_and_preserves_failure(monkeypatch, failure_kind, error_type, status, timeout):
    configure(monkeypatch)
    clock = [0.0]
    actual_counter = pdf_map_analysis.time.perf_counter
    monkeypatch.setattr(pdf_map_analysis.time, 'perf_counter', lambda: actual_counter() + clock[0])
    requests = []
    saved = []
    budget = pdf_layout_adapters.new_budget()
    def transport(request):
        body = json.loads(request.content)
        name = body['tools'][0]['name']
        requests.append(name)
        assert saved[-1]['consumed_requests'] == len(requests)
        assert request.extensions['timeout']['read'] == (60 if name == 'extract_map_connectivity' else 30)
        if name == 'extract_map_connectivity':
            if failure_kind == 'timeout':
                clock[0] += 40.245
                raise httpx.ReadTimeout('PRIVATE BODY NEVER SAVED', request=request)
            if failure_kind == 'connection':
                raise httpx.ConnectError('PRIVATE BODY NEVER SAVED', request=request)
            return httpx.Response(429, request=request, json={'error': {
                'message': 'PRIVATE BODY NEVER SAVED', 'type': 'rate_limit_error'}})
        prompt = body['input'][0]['content'][0]['text']
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        return httpx.Response(200, request=request, json={'id': 'resp_test', 'created_at': 0,
            'model': 'test-model', 'object': 'response', 'output': [{'type': 'function_call',
            'id': 'fc_test', 'call_id': 'call_test', 'name': name,
            'arguments': json.dumps(phase_response(name, payload))}]})
    real_client = openai.OpenAI
    http_client = httpx.Client(transport=httpx.MockTransport(transport))
    def client(**options):
        assert options['max_retries'] == 0
        return real_client(http_client=http_client, **options)
    monkeypatch.setattr(openai, 'OpenAI', client)
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-test-key')
    monkeypatch.setattr(openai_provider, 'OPENAI_MODEL', 'test-model')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, openai_provider)
    def reserve():
        return pdf_image_transcription.reserve_verification(budget, 16, lambda ledger: saved.append(copy.deepcopy(ledger)))
    result = pdf_map_analysis.analyze(b'neutral-test-image', reserve=reserve, candidate=True)
    assert result.graph is None
    assert result.analysis['status'] == 'MAP_ANALYSIS_FAILED'
    assert requests == ['inventory_map_locations', 'audit_map_inventory', 'extract_map_connectivity']
    assert budget['consumed_requests'] == len(saved) == len(result.analysis['attempts']) == 3
    assert budget['remaining_requests'] == 5
    attempt = result.analysis['attempts'][-1]
    assert attempt['stage'] == 'phase2_generation'
    if timeout:
        assert attempt['elapsed_seconds'] >= 40.245
    failure = attempt['failure']
    assert {key: value for key, value in failure.items() if key != 'elapsed_seconds'} == {
        'provider': 'openai', 'stage': 'phase2_generation', 'error_type': error_type,
        'status_code': status, 'timeout': timeout}
    assert (40.245 if timeout else 0) <= failure['elapsed_seconds'] <= attempt['elapsed_seconds']
    assert 'PRIVATE BODY' not in json.dumps(attempt)
    assert result.analysis['repair_attempts'] == 0
