"""Real Responses shape, invented text: runtime diagnostics never publish prose."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app.providers import openai_provider


def response(output, *, status='completed', identity='fixture-response'):
    return {'id': identity, 'object': 'response', 'created_at': 0, 'model': 'fixture-model',
            'status': status, 'output': output, 'error': None, 'incomplete_details': None,
            'parallel_tool_calls': False, 'tool_choice': 'auto', 'tools': []}


def message(text):
    return {'type': 'message', 'id': 'fixture-message', 'status': 'completed', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}


@pytest.fixture
def sdk(monkeypatch):
    original = openai.AsyncOpenAI
    queue = []
    sent = []

    async def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=queue.pop(0), request=request)

    def client(**options):
        return original(api_key='fixture-key', max_retries=0,
                        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))

    monkeypatch.setattr(openai, 'AsyncOpenAI', client)
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fixture-key')
    monkeypatch.setattr(openai_provider.config, 'OPENAI_OMIT_TEMPERATURE', True)
    yield queue, sent


def test_completed_real_message_is_preserved_and_traced_without_prose(sdk, monkeypatch):
    asyncio.run(completed_message(sdk, monkeypatch))


async def completed_message(sdk, monkeypatch):
    queue, sent = sdk
    queue.append(response([{'type': 'reasoning', 'id': 'fixture-reasoning', 'summary': []}, message('虛構的安全回覆。')]))
    events = []
    monkeypatch.setattr(openai_provider.observability, 'event', lambda name, **fields: events.append((name, fields)))
    text = await openai_provider.run_conversation('fixture system', 'fixture state', [], [], 'look', AsyncMock(), 2, enable_wrapup=False)
    await openai_provider.shutdown_async_client()
    assert text == '虛構的安全回覆。'
    assert len(sent) == 1
    shape = next(fields for name, fields in events if name == 'llm.response.shape')
    assert shape['output_types'] == ['reasoning', 'message']
    assert shape['assistant_text_present'] is True
    assert '虛構' not in str(events)
    assert next(fields for name, fields in events if name == 'llm.turn.stop')['reason'] == 'assistant_output'


def call():
    return {'type': 'function_call', 'id': 'fixture-call', 'call_id': 'call-1',
            'name': 'inspect_fixture', 'arguments': '{}', 'status': 'completed'}


async def run(sdk, outputs, *, iterations=3, tools=None):
    queue, sent = sdk
    queue.extend(outputs)
    execute = AsyncMock(return_value={'ok': True})
    try:
        text = await openai_provider.run_conversation('fixture system', 'fixture state', tools or [], [], 'look', execute, iterations, enable_wrapup=False)
        return text, execute, sent
    finally:
        await openai_provider.shutdown_async_client()


@pytest.mark.parametrize('mixed', [False, True])
def test_tool_result_continuation_reaches_final_text(sdk, mixed):
    initial = [call()] + ([message('Intermediate fixture text.')] if mixed else [])
    text, execute, sent = asyncio.run(run(sdk, [response(initial), response([message('Final fixture text.')])]))
    assert text == 'Final fixture text.'
    execute.assert_awaited_once_with('inspect_fixture', {})
    assert sent[1]['input'][0]['type'] == 'function_call_output'
    assert json.loads(sent[1]['input'][0]['output']) == {'ok': True}
    assert len(sent) == 2


def test_multiple_message_items_keep_unicode_text(sdk):
    text, execute, sent = asyncio.run(run(sdk, [response([message('一段。'), message('二段。')])]))
    assert text == '一段。二段。'
    execute.assert_not_awaited()
    assert len(sent) == 1


def test_empty_output_retains_incomplete_placeholder_and_reason(sdk, monkeypatch):
    events = []
    monkeypatch.setattr(openai_provider.observability, 'event', lambda name, **fields: events.append((name, fields)))
    text, execute, _ = asyncio.run(run(sdk, [response([message('  ')])]))
    assert '無法完成' in text
    execute.assert_not_awaited()
    assert next(f for n, f in events if n == 'llm.turn.stop')['reason'] == 'NO_ASSISTANT_OUTPUT'


def test_tool_iteration_limit_is_not_success(sdk, monkeypatch):
    events = []
    monkeypatch.setattr(openai_provider.observability, 'event', lambda name, **fields: events.append((name, fields)))
    text, execute, sent = asyncio.run(run(sdk, [response([call()]), response([call()])], iterations=2))
    assert '無法完成' in text
    assert execute.await_count == len(sent) == 2
    assert next(f for n, f in events if n == 'llm.turn.stop')['reason'] == 'MAX_ITERATIONS'


@pytest.mark.parametrize('status', ['incomplete', 'failed', 'cancelled'])
def test_nonfinal_provider_response_does_not_fake_success(sdk, status):
    with pytest.raises(openai_provider.IncompleteResponseError):
        asyncio.run(run(sdk, [response([message('Not authoritative.')], status=status)]))


def test_malformed_output_fails_without_publishing_text(sdk):
    with pytest.raises((AttributeError, TypeError)):
        asyncio.run(run(sdk, [response(None)]))


@pytest.mark.parametrize('disposition,category', [
    ('no_mechanics', 'COMPLETED'), ('incomplete', 'MODEL_INCOMPLETE'),
    ('await_check', 'PENDING_ACTION'),
])
def test_real_completed_response_is_not_completed_gameplay_by_itself(sdk, disposition, category):
    from app.models import Character, GroupState
    from app.services import turn_resolution
    state = GroupState(group_id='fixture-state', timeline_id='fixture-timeline')
    state.characters['player'] = Character(name='Fixture Investigator', owner_id='player', character_id='fixture-actor')
    if disposition == 'await_check':
        state.pending_checks['player'] = {'check_id': 'fixture-check', 'timeline_id': 'fixture-timeline'}
    decision = {'disposition': disposition, 'actor_character_id': 'fixture-actor',
                'waiting_for': 'fixture-actor' if disposition == 'await_check' else '',
                'check_id': 'fixture-check' if disposition == 'await_check' else '',
                'reason': 'Invented fixture reason.', 'evidence_refs': ['state']}
    text, _, _ = asyncio.run(run(sdk, [response([{'type': 'reasoning', 'id': 'fixture-reasoning', 'summary': []}, message(json.dumps(decision))])]))
    result = turn_resolution.validate_resolution(text, state=state, user_id='player', before_pending={}, before_luck={}, tool_events=[], has_scenario=False, before_actor={})
    assert result.disposition == disposition
    assert turn_resolution.completion_category(result) == category
    assert bool(state.pending_checks) is (disposition == 'await_check')


def test_narrative_is_not_an_executor_mechanics_receipt(sdk):
    from app.models import Character, GroupState
    from app.services import turn_resolution
    state = GroupState(group_id='fixture-state')
    state.characters['player'] = Character(name='Fixture Investigator', owner_id='player')
    text, _, _ = asyncio.run(run(sdk, [response([message('Plain invented narration, not a mechanics decision.')])]))
    result = turn_resolution.validate_resolution(text, state=state, user_id='player', before_pending={}, before_luck={}, tool_events=[], has_scenario=False, before_actor={})
    assert result.disposition == 'incomplete'
    assert result.validation_code == 'invalid_json'
    assert turn_resolution.completion_category(result) == 'PARSER_INVALID_OUTPUT'


def test_runtime_wire_preserves_optional_opposed_contract(sdk):
    from app import keeper
    from app.services import opposed_checks
    tool = next(t for t in keeper.TOOLS if t['name'] == 'skill_check')
    # Real rejected call supplied the opposed field set but failed its required value checks.
    invalid_opposed = {'opponent_skill': '', 'opponent_value': 0, 'tie_winner': 'neither',
                       'source': '', 'on_win': '', 'on_loss': ''}
    with pytest.raises(ValueError, match='缺少有效'):
        opposed_checks.contract(invalid_opposed)
    _, _, sent = asyncio.run(run(sdk, [response([message('Fixture final text.')])], tools=[tool]))
    wire = sent[0]['tools'][0]
    assert 'opposed' not in wire['parameters']['required']
    assert wire.get('strict') is False  # Omission must not imply server strict normalization.
    assert wire['parameters']['properties']['opposed'] == opposed_checks.SCHEMA
    assert opposed_checks.contract(None) is None
