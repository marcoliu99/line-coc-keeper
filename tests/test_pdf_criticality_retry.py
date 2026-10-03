"""Operator retry consumes another receipt; ordinary cache replay never retries."""
import json
from unittest.mock import Mock

from app import config
from app import pdf_page_criticality as criticality
from app.providers import registry


def test_explicit_retry_keeps_failed_attempt_and_replays_success(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_PAGE_CRITICALITY_MAX_REQUESTS', 2)
    output = {'page_role': 'empty', 'contains_gameplay_source': False,
              'contains_mechanics': False, 'contains_required_clue': False, 'asset_only': True,
              'all_source_fragments_accounted_for': True, 'source_fragments': [],
              'optional_source_quote': '', 'optional_source_page': 0}
    provider = Mock(analyze_image=Mock(side_effect=[None, output]), analysis_model_identity=lambda: 'test')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    kwargs = {'page': 1, 'pdf_sha256': 'book', 'native': '', 'safe': {}}
    assert criticality.classify(b'image', **kwargs)['source_critical'] is None
    assert criticality.classify(b'image', **kwargs)['source_critical'] is None
    assert provider.analyze_image.call_count == 1
    assert criticality.classify(b'image', **kwargs, retry_failed=True)['page_role'] == 'EMPTY_NON_SOURCE'
    assert criticality.classify(b'image', **kwargs, retry_failed=True)['page_role'] == 'EMPTY_NON_SOURCE'
    assert provider.analyze_image.call_count == 2
    ledger = json.loads(next((tmp_path / '.page-criticality').glob('*/ledger.json')).read_text())
    assert ledger['consumed_requests'] == 2
    assert [a['status'] for a in ledger['attempts'].values()] == ['failed', 'completed']
    assert all(call.kwargs['max_retries'] == 0 for call in provider.analyze_image.call_args_list)


def test_explicit_retry_cannot_exceed_existing_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_PAGE_CRITICALITY_MAX_REQUESTS', 1)
    provider = Mock(analyze_image=Mock(return_value=None), analysis_model_identity=lambda: 'test')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    kwargs = {'page': 1, 'pdf_sha256': 'book', 'native': '', 'safe': {}}
    criticality.classify(b'image', **kwargs)
    assert criticality.classify(b'image', **kwargs, retry_failed=True)['reason'] == 'classification_budget_exhausted'
    assert provider.analyze_image.call_count == 1
    ledger = json.loads(next((tmp_path / '.page-criticality').glob('*/ledger.json')).read_text())
    assert ledger['consumed_requests'] == 1
    assert len(ledger['attempts']) == 1


def test_audit_cache_uses_exact_window_and_explicit_failed_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_PAGE_CRITICALITY_MAX_REQUESTS', 4)
    observed = {'page_role': 'mixed', 'contains_gameplay_source': True,
                'contains_mechanics': False, 'contains_required_clue': False, 'asset_only': False,
                'all_source_fragments_accounted_for': True,
                'source_fragments': ['Character gear.', 'First Aid heals 1 HP.'],
                'optional_source_quote': '', 'optional_source_page': 0}
    provider = Mock(analyze_image=Mock(return_value=observed),
                    analyze_text=Mock(side_effect=[None, {'pages': []}, {'pages': []}]),
                    analysis_model_identity=lambda: 'test')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    safe = {2: 'Each player creates an investigator.', 3: 'First Aid heals 1 HP.'}
    result = criticality.classify(b'image', page=1, pdf_sha256='book', native='', safe=safe)
    kwargs = {'pdf_sha256': 'book', 'safe': safe,
              'assets': {1: {'kind': 'pregen', 'start_page': 1, 'end_page': 1, 'title_sha256': 'title'}},
              'current': {1: result}}
    criticality.triage({1: b'image'}, **kwargs)
    criticality.triage({1: b'image'}, **kwargs)
    assert provider.analyze_text.call_count == 1
    criticality.triage({1: b'image'}, **kwargs, retry_failed=True)
    criticality.triage({1: b'image'}, **kwargs, retry_failed=True)
    assert provider.analyze_text.call_count == 2
    kwargs['safe'] = {2: safe[2], 3: 'First Aid heals 2 HP.'}
    criticality.triage({1: b'image'}, **kwargs)
    assert provider.analyze_text.call_count == 3
    ledger = json.loads(next((tmp_path / '.page-criticality').glob('*/ledger.json')).read_text())
    assert ledger['consumed_requests'] == 4
    assert [a['status'] for a in ledger['attempts'].values()] == ['completed', 'failed', 'completed', 'completed']
    assert all(call.kwargs['max_retries'] == 0 for call in provider.analyze_text.call_args_list)
