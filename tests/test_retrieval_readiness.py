"""Offline coverage for missing tokenization and hidden gate rejections."""
import asyncio
import sys
from unittest.mock import Mock, patch

from app import scenario_retrieval
from app.agents.tool_gateway import make_tool_executor
from app.models import GroupState
from app.services import input_budget


def test_unavailable_tokenizer_is_conservative_and_diagnostic(monkeypatch):
    input_budget._encoding.cache_clear()
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
        input_budget._encoding.cache_clear()


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
        with patch('app.agents.tool_gateway.keeper._execute_tool') as tool, \
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
                patch('app.agents.tool_gateway.keeper._execute_tool', new=Mock()) as tool:
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
