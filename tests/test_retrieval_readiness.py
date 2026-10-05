"""Offline coverage for missing tokenization and hidden gate rejections."""
import asyncio
import json
import sys
from unittest.mock import Mock, patch

import pytest

from app import scenario_retrieval
from app.agents.tool_gateway import make_tool_executor
from app.models import GroupState
from app.services import input_budget


def test_unavailable_tokenizer_is_conservative_and_diagnostic(monkeypatch):
    input_budget.reset_encoding_cache()
    monkeypatch.setitem(sys.modules, 'tiktoken', None)
    try:
        with patch.object(input_budget.observability, 'event') as event:
            assert input_budget.estimate('中文', 'test') == 6
            assert input_budget.tokenizer_method('test') == 'utf8_bytes_fallback'
        assert event.call_count == 1
        assert event.call_args.args == ('llm.tokenizer.unavailable',)
        assert event.call_args.kwargs['error_type'] == 'ModuleNotFoundError'
        assert '中文' not in str(event.call_args)
    finally:
        input_budget.reset_encoding_cache()


class _FakeEncoding:
    name = 'o200k_base'

    def encode(self, text, disallowed_special=()):
        return [0] * len(text)


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def test_transient_tokenizer_failure_is_retried_instead_of_latched(monkeypatch):
    """One cold-cache failure must not pin the process to the byte fallback."""
    input_budget.reset_encoding_cache()
    clock = _FakeClock()
    monkeypatch.setattr(input_budget, 'time', clock)
    attempts = []

    def loader(model):
        attempts.append(model)
        if len(attempts) == 1:
            raise OSError('cold BPE cache download failed')
        return _FakeEncoding()

    monkeypatch.setattr(input_budget, '_load_encoding', loader)
    try:
        with patch.object(input_budget.observability, 'event') as event:
            assert input_budget.estimate('中文', 'gpt-test') == 6
            assert event.call_count == 1
            assert event.call_args.args == ('llm.tokenizer.unavailable',)
            assert event.call_args.kwargs['failed_attempts'] == 1
            assert event.call_args.kwargs['retry_after_seconds'] == input_budget.ENCODING_RETRY_SECONDS

            # Inside the retry window the fallback is reused without reloading.
            clock.now += input_budget.ENCODING_RETRY_SECONDS - 1
            assert input_budget.tokenizer_method('gpt-test') == 'utf8_bytes_fallback'
            assert attempts == ['gpt-test']
            assert event.call_count == 1

            # Once the window elapses the tokenizer is retried and recovers.
            clock.now += 2
            assert input_budget.estimate('中文', 'gpt-test') == 2
            assert input_budget.tokenizer_method('gpt-test') == 'tokenizer_estimate'
            assert attempts == ['gpt-test', 'gpt-test']
            assert event.call_args.args == ('llm.tokenizer.recovered',)
            assert event.call_args.kwargs['failed_attempts'] == 1
    finally:
        input_budget.reset_encoding_cache()


def test_resolved_tokenizer_is_cached_without_reloading(monkeypatch):
    input_budget.reset_encoding_cache()
    attempts = []

    def loader(model):
        attempts.append(model)
        return _FakeEncoding()

    monkeypatch.setattr(input_budget, '_load_encoding', loader)
    try:
        with patch.object(input_budget.observability, 'event') as event:
            assert input_budget.estimate('中文', 'gpt-test') == 2
            assert input_budget.estimate('中文', 'gpt-test') == 2
            assert input_budget.tokenizer_method('gpt-test') == 'tokenizer_estimate'
        assert attempts == ['gpt-test']
        # A first success is not an incident; it must stay off the warning path.
        assert event.call_count == 0
    finally:
        input_budget.reset_encoding_cache()


def test_named_model_byte_fallback_reports_zero_budget_honestly(monkeypatch):
    monkeypatch.setattr(input_budget, '_encoding', lambda _: None)
    monkeypatch.setattr(scenario_retrieval.config, 'SCENARIO_CONTEXT_TOKEN_CEILING', 32000)
    token = scenario_retrieval.MODEL.set('named-model-without-encoder')
    budget = scenario_retrieval.BUDGET.set(0)
    try:
        with patch.object(scenario_retrieval.observability, 'event') as event:
            assert scenario_retrieval.remaining_budget('中' * 22000, 'named-model-without-encoder') == 0
        assert event.call_args.kwargs['context_tokens_estimate'] == 66000
        assert event.call_args.kwargs['token_estimate_method'] == 'utf8_bytes_fallback'
        row = scenario_retrieval.project({'intro': {
            'page': 1, 'name': 'Intro', 'visibility': 'kp_only', 'type': 'scene',
            'kp_text': '房東提供鑰匙。', 'public_text': '', 'related_record_ids': [],
        }}, ['intro'], '鑰匙')[0]
        assert not row['complete_for_action']
        assert row['token_estimate_method'] == 'utf8_bytes_fallback'
        assert row['missing_required_ids'] == ['intro#kp_only']
    finally:
        scenario_retrieval.BUDGET.reset(budget)
        scenario_retrieval.MODEL.reset(token)


def test_evidence_rejection_keeps_facts_and_diagnostics_without_execution():
    async def run():
        facts, status = [], {}
        execute = make_tool_executor(GroupState(group_id='g'), [], [], 'player', facts, status,
                                     evidence_incomplete=True, required_evidence_ids={'intro'})
        with patch('app.agents.tool_gateway.tool_dispatch.execute_tool') as tool, \
                patch('app.agents.tool_gateway.observability.event') as event:
            result = await execute('add_carried_item', {'investigator': 'private-name', 'item': 'private-item'})
        tool.assert_not_called()
        assert result['error'] == 'required_scenario_evidence_missing'
        assert status['scenario_evidence_blocked'] is True
        assert facts == ['add_carried_item 失敗：required_scenario_evidence_missing']
        assert event.call_args.args == ('llm.tool.rejected',)
        assert 'private-' not in str(event.call_args)
    asyncio.run(run())


def test_correction_rejection_is_also_recorded():
    async def run():
        facts = []
        execute = make_tool_executor(GroupState(group_id='g'), [], [], 'player', facts)
        with patch('app.services.narrative_corrections.blocking_reply', return_value='pending correction'), \
                patch('app.agents.tool_gateway.tool_dispatch.execute_tool', new=Mock()) as tool:
            result = await execute('add_carried_item', {})
        tool.assert_not_called()
        assert result['error'] == 'narrative_correction_hold'
        assert facts == ['add_carried_item 失敗：narrative_correction_hold']
    asyncio.run(run())


def test_reuse_does_not_hide_unseen_dependencies_or_leak_across_turns():
    def record(name, related=()):
        return {'page': 1, 'name': name, 'visibility': 'kp_only', 'type': 'scene',
                'kp_text': name * 500, 'public_text': '', 'related_record_ids': list(related)}
    records = {'intro': record('intro', ['rule']), 'rule': record('rule')}
    token = scenario_retrieval.DELIVERED_FRAGMENTS.set(frozenset({'intro#kp_only'}))
    budget = scenario_retrieval.BUDGET.set(1000)
    try:
        row = scenario_retrieval.project(records, ['intro'], 'keys')[0]
        assert not row['complete_for_action']
        assert row['missing_required_ids'] == ['rule#kp_only']
        assert row['reused_fragment_ids'] == ['intro#kp_only']
        assert row['_next_offset'] == 1
    finally:
        scenario_retrieval.DELIVERED_FRAGMENTS.reset(token)
        scenario_retrieval.BUDGET.reset(budget)
    assert not scenario_retrieval.DELIVERED_FRAGMENTS.get()
    assert not scenario_retrieval.project(records, ['intro'], 'keys')[0]['reused_fragment_ids']


def test_continuation_never_skips_unseen_middle_fragment():
    records = {name: {'page': 1, 'name': name, 'visibility': 'kp_only', 'type': 'scene',
                     'kp_text': name * 500, 'public_text': '', 'related_record_ids': []}
               for name in ('first', 'middle', 'last')}
    token = scenario_retrieval.DELIVERED_FRAGMENTS.set(frozenset({'first#kp_only', 'last#kp_only'}))
    budget = scenario_retrieval.BUDGET.set(1000)
    try:
        row = scenario_retrieval.project(records, list(records), 'keys')[0]
        assert row['missing_required_ids'] == ['middle#kp_only']
        assert row['_next_offset'] == 1
    finally:
        scenario_retrieval.DELIVERED_FRAGMENTS.reset(token)
        scenario_retrieval.BUDGET.reset(budget)


def test_ranked_alternatives_do_not_become_required_dependencies():
    records = {name: {'page': i+1, 'name': name, 'visibility': 'kp_only', 'type': 'source_unit',
                     'kp_text': name * size, 'public_text': '', 'related_record_ids': []}
               for i, (name, size) in enumerate((('intro', 10), ('unrelated', 3000)))}
    token = scenario_retrieval.BUDGET.set(3000)
    try:
        row = scenario_retrieval.project_ranked(records, ['intro', 'unrelated'], 'keys')[0]
        assert row['complete_for_action']
        assert row['root_record_ids'] == ['intro']
        assert not row['missing_required_ids']
        assert row['deferred_candidates'][0]['record_id'] == 'unrelated'
        assert row['projection_tokens_estimate'] <= 3000
        # Making the same record an explicit dependency must block this action.
        records['intro']['related_record_ids'] = ['unrelated']
        row = scenario_retrieval.project_ranked(records, ['intro', 'unrelated'], 'keys')[0]
        assert not row['complete_for_action']
        assert 'unrelated#kp_only' in row['missing_required_ids']
        # Never skip an oversized highest-ranked result in favour of easy hits.
        row = scenario_retrieval.project_ranked(records, ['unrelated', 'intro'], 'keys')[0]
        assert not row['complete_for_action']
        assert row['root_record_ids'] == ['unrelated']
    finally:
        scenario_retrieval.BUDGET.reset(token)


@pytest.mark.parametrize('capacity', [1500, 1800, 3000])
@pytest.mark.parametrize('model', ['unknown', 'named-fallback', 'named-tokenizer'])
def test_ranked_candidate_metadata_fits_final_budget(monkeypatch, capacity, model):
    monkeypatch.setattr(input_budget, '_encoding',
                        lambda _: _FakeEncoding() if model == 'named-tokenizer' else None)
    records = {str(i): {
        'page': i + 1, 'name': '房屋地下室與閣樓中的詳細線索資訊' * 3 if i else 'intro',
        'visibility': 'kp_only', 'type': 'source_unit',
        'kp_text': 'key' if i == 0 else 'hidden' * 3000,
        'public_text': '', 'related_record_ids': [],
    } for i in range(9)}
    budget = scenario_retrieval.BUDGET.set(capacity)
    model_token = scenario_retrieval.MODEL.set(model)
    try:
        row = scenario_retrieval.project_ranked(records, list(records), 'keys')[0]
        scenario_retrieval.bind_continuation([row], ['test'])
        assert row['complete_for_action']
        assert row['root_record_ids'] == ['0']
        assert '[0｜PDF 1] intro\nkey' in row['text']
        assert row['deferred_candidate_count'] == 8
        assert row['completeness_scope'] == 'selected_records_and_required_dependencies'
        actual_cost = scenario_retrieval._cost(json.dumps(row, ensure_ascii=False))
        assert actual_cost <= row['projection_tokens_estimate'] <= capacity
        if model != 'named-tokenizer' and capacity == 1500:
            assert len(row['deferred_candidates']) < 8
    finally:
        scenario_retrieval.BUDGET.reset(budget)
        scenario_retrieval.MODEL.reset(model_token)


@pytest.mark.parametrize('capacity', [0, 100, 1000])
def test_ranked_control_envelope_overflow_is_explicit(capacity):
    records = {'intro': {
        'page': 1, 'name': 'Intro', 'visibility': 'kp_only', 'type': 'scene',
        'kp_text': 'key', 'public_text': '', 'related_record_ids': [],
    }}
    budget = scenario_retrieval.BUDGET.set(capacity)
    model = scenario_retrieval.MODEL.set('unknown')
    try:
        row = scenario_retrieval.project_ranked(records, ['intro'], 'key')[0]
        assert not row['complete_for_action']
        assert row['budget_exceeded']
        assert row['projection_reason'] == 'retrieval_budget_exceeded'
        assert row['projection_tokens_estimate'] > row['budget_tokens'] == capacity
        assert '【依據尚未完整】' in row['text']
    finally:
        scenario_retrieval.BUDGET.reset(budget)
        scenario_retrieval.MODEL.reset(model)


def test_evidence_status_tracks_remaining_roots_and_narrator_reason():
    from app.domain.models import MechanicResult, StateDelta, TurnResolution
    from app.services.prompt_config import enforce_mechanic_check_consistency

    async def run():
        status, calls = {}, []
        execute = make_tool_executor(GroupState(group_id='g'), [], [], 'player', [], status)

        def tool(state, name, data, *args):
            calls.append(name)
            return {'ok': True, **data}

        with patch('app.agents.tool_gateway.tool_dispatch.execute_tool', side_effect=tool):
            await execute('search_scenario', {'complete_for_action': False, 'evidence_record_ids': ['intro', 'rules']})
            for receipt in (
                {'complete_for_action': True, 'evidence_record_ids': ['unrelated']},
                {'complete_for_action': True, 'evidence_record_ids': ['intro']},
                {'complete_for_action': None, 'evidence_record_ids': ['rules']},
                {'ok': False, 'complete_for_action': True, 'evidence_record_ids': ['rules']},
            ):
                await execute('search_scenario', receipt)
                assert status['scenario_evidence_blocked'] is True
                assert (await execute('add_carried_item', {}))['error'] == 'required_scenario_evidence_missing'
            assert 'add_carried_item' not in calls
            await execute('search_scenario', {'complete_for_action': True, 'evidence_record_ids': ['rules']})
            assert status['scenario_evidence_blocked'] is False
            # Retrieval recovered, but a later mutation fails for a different
            # reason. Narrator must not resurrect the old evidence hold.
            assert not (await execute('adjust_character', {'ok': False, 'error': 'invalid_adjustment'}))['ok']
            for disposition in ('blocked', 'incomplete'):
                result = MechanicResult(success=False, action_type='mutation', narrative_facts=[],
                                        state_delta=StateDelta(), check_status=status,
                                        turn_resolution=TurnResolution(disposition=disposition))
                text = enforce_mechanic_check_consistency('mutation failed', result)
                assert '劇本依據' not in text
            assert (await execute('add_carried_item', {}))['ok']
            # A later new incomplete root must close the gate again.
            await execute('search_scenario', {'complete_for_action': False, 'evidence_record_ids': ['door']})
            assert status['scenario_evidence_blocked'] is True
            assert not (await execute('add_carried_item', {}))['ok']
    asyncio.run(run())
