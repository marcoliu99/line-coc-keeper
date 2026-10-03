"""Opening helpers must reach Codex's bounded structured-analysis adapter."""
import json
from unittest.mock import AsyncMock

import pytest

from app import config, scenario_intro
from app.providers import codex_provider


@pytest.fixture
def opening_transport(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'codex')
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    transport = AsyncMock()
    transport.request.return_value = json.dumps({'found': True, 'text': 'A quiet street.',
                                                 'page': 1, 'opening_check': None})
    monkeypatch.setattr(codex_provider, 'ExecTransport', lambda: transport)
    return transport


def test_opening_helper_reaches_codex_adapter(opening_transport, tmp_path):
    result = scenario_intro.extract_opening_narration('Read aloud: A quiet street.')
    assert result == {'found': True, 'text': 'A quiet street.', 'page': 1, 'opening_check': None}
    assert opening_transport.request.await_count == 1
    records = [json.loads(p.read_text()) for p in (tmp_path / '.source-analysis').glob('*.json')]
    assert len(records) == 1 and records[0]['status'] == 'completed'
    assert 'TypeError' not in json.dumps(records)


def test_old_failed_version_does_not_poison_corrected_opening(opening_transport, monkeypatch, tmp_path):
    from app import source_analysis
    real_analyze = codex_provider.analyze_text
    def broken(*_args, **_kwargs):
        raise TypeError('old provider contract')
    monkeypatch.setattr(source_analysis, 'SOURCE_ANALYSIS_VERSION', 1, raising=False)
    monkeypatch.setattr(codex_provider, 'analyze_text', broken)
    assert not scenario_intro.extract_opening_narration('Read aloud: A quiet street.')['found']
    monkeypatch.setattr(source_analysis, 'SOURCE_ANALYSIS_VERSION', 2)
    monkeypatch.setattr(codex_provider, 'analyze_text', real_analyze)
    assert scenario_intro.extract_opening_narration('Read aloud: A quiet street.')['found']
    assert scenario_intro.extract_opening_narration('Read aloud: A quiet street.')['found']
    assert opening_transport.request.await_count == 1
    records = [json.loads(p.read_text()) for p in (tmp_path / '.source-analysis').glob('*.json')]
    assert sorted(r['status'] for r in records) == ['completed', 'failed']
    assert sum(r['consumed_requests'] for r in records) == 2


@pytest.mark.parametrize('output', [json.dumps({'found': False, 'text': '', 'page': None,
                                               'opening_check': None}), 'bad json'])
def test_absent_or_failed_opening_remains_graceful(opening_transport, output):
    opening_transport.request.return_value = output
    assert scenario_intro.extract_opening_narration('Read aloud: A quiet street.')['found'] is False
    assert opening_transport.request.await_count == 1


@pytest.mark.parametrize(('caller_timeout', 'owner_timeout', 'bound'), [(10, 30, 10), (60, 30, 30), (None, 30, 30)])
def test_caller_and_owner_share_shortest_deadline(opening_transport, monkeypatch, caller_timeout, owner_timeout, bound):
    from app.providers import codex_request_owner
    monkeypatch.setattr(config, 'CODEX_TIMEOUT', owner_timeout)
    acquire = AsyncMock(wraps=codex_request_owner.OWNER.acquire)
    monkeypatch.setattr(codex_request_owner.OWNER, 'acquire', acquire)
    import time
    before = time.monotonic()
    tool = {'name': 'report', 'input_schema': {'type': 'object'}}
    assert codex_provider.analyze_text('source', tool, 'extract', timeout=caller_timeout, max_retries=0)
    assert before + bound <= acquire.call_args.args[0] <= time.monotonic() + bound
    assert opening_transport.request.await_count == 1


def test_codex_failure_has_one_attempt_and_no_retry(opening_transport):
    opening_transport.request.side_effect = TimeoutError('synthetic failure')
    assert not scenario_intro.extract_opening_narration('Read aloud: A quiet street.')['found']
    assert opening_transport.request.await_count == 1
    assert opening_transport.close.await_count == 1


def test_codex_rejects_nonzero_retries_without_dispatch(opening_transport):
    with pytest.raises(ValueError, match='does not support retries'):
        codex_provider.analyze_text('source', {'name': 'report'}, 'extract', max_retries=1)
    assert opening_transport.request.await_count == 0


def test_all_general_analysis_adapters_accept_bounded_contract():
    import inspect

    from app.providers.registry import CONVERSATION_PROVIDERS
    for provider in CONVERSATION_PROVIDERS.values():
        signature = inspect.signature(provider.analyze_text)
        signature.bind('source', {'name': 'report'}, 'extract', timeout=10, max_retries=0)
        assert signature.parameters['max_retries'].default == 0


def test_bounded_codex_analysis_disables_cli_http_and_stream_retries(monkeypatch):
    import asyncio

    from app.providers.codex_transport import CodexError, ExecTransport, Process
    start = AsyncMock(side_effect=CodexError('synthetic connection failure'))
    monkeypatch.setattr(Process, 'start', start)
    with pytest.raises(CodexError, match='synthetic connection failure'):
        asyncio.run(ExecTransport().request('synthetic source', {'type': 'object'}, max_retries=0))
    assert start.await_count == 1
    arguments = start.call_args.args[0]
    assert 'model_provider="openai-bounded"' in arguments
    assert 'model_providers.openai-bounded.requires_openai_auth=true' in arguments
    assert 'model_providers.openai-bounded.request_max_retries=0' in arguments
    assert 'model_providers.openai-bounded.stream_max_retries=0' in arguments
