import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import scenario_rag
from app import scenario_templates as templates
from app.providers import openai_provider, retry

MANIFEST = {'content_hash': 'a' * 64, 'chapters': [
    {'id': 'c1', 'title': 'House', 'kind': 'playable', 'start_page': 1, 'end_page': 2}
]}
SOURCE = '## Stairs\nDEX 50.\n\nSuccess: 1d6 damage.\n\nFailure: 2d6 damage.'


def item():
    return {'type': 'check_rule', 'name': '樓梯', 'aliases': ['stairs'], 'keywords': [],
            'public_text': '樓梯', 'kp_text': '', 'rule_text': 'DEX 50; 成功 1d6; 失敗 2d6',
            'uncertainty': '', 'related_source_ids': [], 'rules': [{
                'check': {'text': 'DEX 50', 'source_quote': 'DEX 50'},
                'success': {'text': '成功 1d6', 'source_quote': 'Success: 1d6'},
                'failure': {'text': '失敗 2d6', 'source_quote': 'Failure: 2d6'},
            }]}


def record():
    return {**item(), 'id': 'c1-u1-r1', 'source_id': 'c1-u1', 'page': 1,
            'chapter_id': 'c1', 'visibility': 'public', 'source_excerpt': SOURCE}


def test_units_preserve_paragraphs_subheadings_and_page_continuations():
    with patch.object(scenario_rag, 'split_pages', return_value=[
        (1, '## Stairs\nTrigger\n\n### Check\nDEX'),
        (2, 'Failure and exceptions\n\n## Library\nBooks'),
    ]):
        units = templates._blocks(MANIFEST, 'unused')
    assert len(units) == 2
    assert units[0]['pages'] == [1, 2]
    assert 'Failure and exceptions' in units[0]['text']
    assert 'DEX' in units[0]['text']
    assert 'Books' not in units[0]['text']


def test_swapped_damage_is_rejected_against_each_field_source():
    with patch.object(templates, '_source', return_value=(MANIFEST, SOURCE)):
        assert templates._validate('scenario', [record()])[2] == []
        invalid = record()
        invalid['rules'][0]['success']['text'] = '成功 2d6'
        invalid['rules'][0]['failure']['text'] = '失敗 1d6'
        with pytest.raises(ValueError, match='數值'):
            templates._validate('scenario', [invalid])
        invalid = record()
        invalid['rules'][0]['success']['source_quote'] = 'invented source'
        with pytest.raises(ValueError, match='引述'):
            templates._validate('scenario', [invalid])


def test_retrieval_one_slot_returns_all_allowed_record_scopes():
    with patch.object(scenario_rag, '_embed_texts', return_value=None):
        index = scenario_rag.get_record_index('template-test-scopes', [record()])
    internal = scenario_rag.search(index, 'stairs', top_k=1)
    assert len(internal) == 1 and '2d6' in internal[0]['text']
    public = scenario_rag.search(index, 'stairs', top_k=1, allowed_visibility={'public'})
    assert len(public) == 1 and '2d6' not in public[0]['text']
    assert '原文片段' not in public[0]['text']


def test_restart_reuses_completed_units_and_retries_only_failure(monkeypatch):
    source = SOURCE + '\n## Room\nNothing here.'
    monkeypatch.setattr(templates, '_source', lambda _: (MANIFEST, source))
    monkeypatch.setattr(templates, 'LLM_PROVIDER', 'openai')
    monkeypatch.setattr(templates, '_save_variant', lambda *a, **k: 'saved')
    no_rule = {**item(), 'name': 'Room', 'rule_text': '', 'rules': [], 'public_text': '空房間'}

    async def exercise():
        provider = AsyncMock(side_effect=[{'records': [item()]}, RuntimeError('offline')])
        with patch.object(openai_provider, 'analyze_text_background', provider), pytest.raises(RuntimeError):
            await templates._generate('resume-test', MANIFEST['content_hash'], templates._chapter_hash(MANIFEST))
        assert provider.await_count == 2
        provider = AsyncMock(return_value={'records': [no_rule]})
        with patch.object(openai_provider, 'analyze_text_background', provider):
            assert await templates._generate('resume-test', MANIFEST['content_hash'], templates._chapter_hash(MANIFEST)) == 'saved'
        assert provider.await_count == 1
        assert 'Nothing here' in provider.call_args.args[0]
    asyncio.run(exercise())


def test_background_openai_uses_shared_async_admission_and_rejects_truncation(monkeypatch):
    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-test-key')
    seen = []

    async def call(**kwargs):
        seen.append((retry.background_request.get(), kwargs))
        return SimpleNamespace(status='incomplete', output=[])

    async def exercise():
        with patch.object(openai_provider, '_create_response_async', side_effect=call), pytest.raises(ValueError, match='Incomplete'):
            await openai_provider.analyze_text_background('text', templates._TOOL, 'prompt')
        assert not retry.background_request.get()
    asyncio.run(exercise())
    assert seen[0][0] is True
    assert seen[0][1]['_input_tokens_estimate'] >= 12000


def test_background_yields_to_foreground_even_with_spare_slots(monkeypatch):
    monkeypatch.setattr(retry, '_admission_semaphores', {})
    monkeypatch.setattr(retry, '_foreground_requests', {})

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        order = []

        async def foreground():
            started.set()
            await release.wait()
            order.append('foreground')

        async def background():
            token = retry.background_request.set(True)
            try:
                async def request():
                    order.append('background')
                await retry.async_call_with_retry(request, provider='openai', operation='test')
            finally:
                retry.background_request.reset(token)

        fg = asyncio.create_task(retry.async_call_with_retry(foreground, provider='openai', operation='test'))
        await started.wait()
        bg = asyncio.create_task(background())
        await asyncio.sleep(.01)
        assert order == []
        release.set()
        await asyncio.gather(fg, bg)
        assert order == ['foreground', 'background']
        assert retry._foreground_requests['openai'] == 0
    asyncio.run(exercise())


def test_link_expansion_keeps_chapter_and_visibility_boundaries():
    first = record()
    first['related_source_ids'] = ['allowed', 'future']
    linked = {**record(), 'id': 'linked', 'source_id': 'allowed', 'public_text': 'safe-link',
              'kp_text': 'secret-link', 'related_source_ids': ['c1-u1']}
    with patch.object(scenario_rag, '_embed_texts', return_value=None):
        index = scenario_rag.get_record_index('links', [first, linked])
    public = scenario_rag.search(index, 'stairs', 1, allowed_visibility={'public'})[0]['text']
    assert 'safe-link' in public and 'secret-link' not in public
    private = scenario_rag.search(index, 'stairs', 1)[0]['text']
    assert 'secret-link' in private
    assert 'future' not in private
