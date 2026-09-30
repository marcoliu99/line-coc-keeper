"""Challengers cannot rewrite source or escape import budgets."""
import subprocess
import sys
from types import ModuleType

import pymupdf
import pytest

from app import config
from app import pdf_layout_adapters as adapters


@pytest.fixture
def source(monkeypatch):
    # T1 is independently implemented; isolate the adapter's permutation contract.
    module = ModuleType('app.pdf_layout')
    module.apply_order = lambda d, ids: '\n\n'.join(next(b['text'] for b in d['blocks'] if b['id'] == i) for i in ids)
    monkeypatch.setitem(sys.modules, 'app.pdf_layout', module)
    monkeypatch.setattr(config, 'PDF_LAYOUT_DOCLING_ENABLED', True)
    document = pymupdf.open()
    page = document.new_page()
    decision = {'status': 'needs_review', 'selected_text': 'original', 'ordered_ids': [],
                'diagnostics': ['ambiguous'], 'blocks': [
                    {'id': 'l', 'text': 'Never roll 2d6', 'bbox': [10, 10, 100, 40], 'column': 0},
                    {'id': 'r', 'text': 'Cost 20', 'bbox': [200, 10, 300, 40], 'column': 1}]}
    yield page, decision
    document.close()


def response(ids=None, unresolved=None):
    return {'ordered_ids': ids if ids is not None else ['l', 'r'],
            'unresolved_ids': unresolved if unresolved is not None else []}


def test_docling_first_no_paid_request(source, monkeypatch):
    page, decision = source
    monkeypatch.setattr(adapters, '_docling', lambda *args: 'Never roll 2d6\nCost 20')
    monkeypatch.setattr(adapters, '_provider', lambda *args: pytest.fail('unnecessary API call'))
    budget = adapters.new_budget()
    result = adapters.resolve_page(page, decision, budget)
    assert result['selected_text'] == 'Never roll 2d6\n\nCost 20'
    assert result['selected_candidate'] == 'docling'
    assert budget['remaining_requests'] == config.PDF_LAYOUT_MAX_REQUESTS
    assert decision['selected_text'] == 'original'
    assert decision['diagnostics'] == ['ambiguous']


@pytest.mark.parametrize('bad', [
    response(['l', 'unknown']), response(['l', 'l']), response(['l']),
    response(['l', 'r'], ['r']), response(['l', 'r'], ['unknown']),
    {'ordered_ids': ['l', 'r'], 'unresolved_ids': [], 'text': 'invented 200'},
    None, response([1, 'r']),
])
def test_reject_invalid_provider_preserves_source(source, monkeypatch, bad):
    page, decision = source
    monkeypatch.setattr(adapters, '_docling', lambda *args: 'missing source')
    monkeypatch.setattr(adapters, '_provider', lambda *args: bad)
    budget = {'remaining_requests': 2, 'remaining_pages': 1, 'retries': 1}
    result = adapters.resolve_page(page, decision, budget)
    assert result['status'] == 'needs_review'
    assert result['selected_text'] == 'original'
    assert result['blocks'] == decision['blocks']
    assert budget['metrics']['image_calls'] == 2
    assert budget['remaining_requests'] == 0


def test_timeout_then_retry_validated(source, monkeypatch):
    page, decision = source
    monkeypatch.setattr(adapters, '_docling', lambda *args: 'missing source')
    calls = []

    def provider(*args):
        calls.append(True)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired('worker', 0.1)
        return response(['r', 'l'])

    monkeypatch.setattr(adapters, '_provider', provider)
    budget = {'remaining_requests': 2, 'remaining_pages': 1, 'retries': 1}
    result = adapters.resolve_page(page, decision, budget)
    assert result['status'] == 'accepted'
    assert result['selected_text'] == 'Cost 20\n\nNever roll 2d6'
    assert budget['metrics']['image_failures'] == 1
    assert len(calls) == 2


@pytest.mark.parametrize('requests,pages', [(0, 1), (2, 0)])
def test_exhausted_budget_no_provider(source, monkeypatch, requests, pages):
    page, decision = source
    monkeypatch.setattr(config, 'PDF_LAYOUT_DOCLING_ENABLED', False)
    monkeypatch.setattr(adapters, '_provider', lambda *args: pytest.fail('exhausted budget'))
    result = adapters.resolve_page(page, decision, {'remaining_requests': requests, 'remaining_pages': pages})
    assert result['status'] == 'needs_review'
    assert any('budget exhausted' in d for d in result['diagnostics'])


def test_known_same_column_order_cannot_reverse(source):
    _, decision = source
    decision['blocks'][1].update(column=0, bbox=[10, 60, 100, 90])
    with pytest.raises(ValueError, match='geometrically'):
        adapters._validate(response(['r', 'l']), decision)


def test_repeated_docling_text_not_aligned(source):
    _, decision = source
    with pytest.raises(ValueError, match='alignment'):
        adapters._docling_order('Never roll 2d6 Never roll 2d6 Cost 20', decision['blocks'])


def test_verified_resume_page_never_reprocessed(source, monkeypatch):
    page, decision = source
    decision['status'] = 'accepted'
    monkeypatch.setattr(adapters, '_docling', lambda *args: pytest.fail('already accepted'))
    assert adapters.resolve_page(page, decision, {}) is decision


def test_worker_hard_deadline(monkeypatch):
    recorded = {}

    def run(command, **kwargs):
        recorded.update(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired):
        adapters._run_worker('provider', {}, 0.01)
    assert recorded['timeout'] == 0.01
    assert recorded['check']


def test_bounded_openai_image_call_disables_both_retry_layers(monkeypatch):
    from types import SimpleNamespace

    import openai

    from app.providers import openai_provider

    options = {}
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        raise ConnectionError('offline')

    def client(**kwargs):
        options.update(kwargs)
        return SimpleNamespace(responses=SimpleNamespace(create=create))

    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-test-key')
    monkeypatch.setattr(openai, 'OpenAI', client)
    monkeypatch.setattr(openai_provider, '_create_response', lambda *args, **kwargs: pytest.fail('hidden retry'))
    assert openai_provider.analyze_image(b'png', adapters._TOOL, 'order', timeout=0.5, max_retries=0) is None
    assert options['max_retries'] == 0
    assert options['timeout'] == 0.5
    assert len(requests) == 1


def test_budget_shared_across_pages(source, monkeypatch):
    page, decision = source
    monkeypatch.setattr(config, 'PDF_LAYOUT_DOCLING_ENABLED', False)
    calls = []

    def provider(*args):
        calls.append(True)
        return response()

    monkeypatch.setattr(adapters, '_provider', provider)
    budget = {'remaining_requests': 1, 'remaining_pages': 1}
    assert adapters.resolve_page(page, decision, budget)['status'] == 'accepted'
    assert adapters.resolve_page(page, decision, budget)['status'] == 'needs_review'
    assert len(calls) == 1
