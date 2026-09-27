"""Real authoring round trips and source/permission boundaries, without live APIs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from copy import deepcopy
from itertools import pairwise
from types import SimpleNamespace

import pytest

from app import scenario_authoring as authoring
from app import scenario_library, scenario_rag, scenario_retrieval
from app import scenario_templates as templates


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, 'IMPORT_DIR', tmp_path / 'imports')
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(scenario_rag, '_embed_texts', lambda *a, **k: None)
    scenario_rag._index_cache.clear()
    templates._selection_cache.clear()
    def create(text='Armor 2.\n\nAttack 1d6.\n\nNo second attack.'):
        root = scenario_library._path('sample')
        root.mkdir(parents=True, exist_ok=True)
        manifest = {'content_hash': hashlib.sha256(text.encode()).hexdigest(), 'chapters': [
            {'id': 'c1', 'title': 'House', 'kind': 'playable', 'start_page': 1, 'end_page': 999}]}
        (root / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'scenario.txt').write_text(text)
        path = result_copy(templates.export_template('sample'))
        return path, authoring.parse_markdown(path.read_text())
    return create


def result_copy(source):
    target = source.parent.parent / 'results' / source.name
    shutil.copy2(source, target)
    return target


def relative(path):
    return path.relative_to(templates.IMPORT_DIR).as_posix()


def write(path, payload):
    path.write_text('```json\n'+json.dumps(payload, ensure_ascii=False)+'\n```')


def fill(payload, text='護甲 2。攻擊 1d6。不能再次攻擊。'):
    for record in [r for b in payload['batches'] for r in b['records']]:
        record.update(kp_text=text, uncertainty='')
    return payload


def test_workbook_prompt_complete_example_source_mapping_and_round_trip(library):
    path, payload = library()
    assert payload['authoring_version'] == 2 and 'schema_version' not in payload
    assert authoring.PROMPT in path.read_text()
    assert path.stat().st_mode & 0o077 == 0
    message = templates.export_message('sample', path)
    assert authoring.PROMPT in message and path.name in message and 'imports' in message
    assert 'source_spans' not in payload['batches'][0]['records'][0]
    fill(payload)
    payload['batches'][0]['records'][0]['rules'] = [{'check': {'text': '護甲 2', 'evidence': [
        {'unit_id': 'u1', 'source_quote': 'Armor 2'}]}}]
    write(path, payload)
    variant_id = templates.import_markdown('sample', relative(path))
    variant, records = templates._read_variant('sample', variant_id)
    assert variant['schema_version'] == 4
    assert records[0]['source_spans'] == [[0, len('Armor 2.\n\nAttack 1d6.\n\nNo second attack.')]]
    assert records[0]['rules'][0]['check']['source_quote'] == 'Armor 2'
    assert records[0]['source_pages'] == [1]
    templates.approve('sample', variant_id, reviewer_id='kp')
    assert templates.import_markdown('sample', relative(path)) == variant_id
    state = SimpleNamespace(group_id='g', timeline_id='t1', scenario_library_id='sample',
                            scenario_variant_id=variant_id, context_chapter_ids=['c1'], scenario_text='source')
    index, rows = templates.search_for_state(state, '護甲')
    assert index.record_store and rows and '護甲 2' in rows[0]['text']
    assert rows[0]['complete_for_action'] is True
    assert 'Armor 2' not in rows[0]['text']
    scenario_rag._index_cache.clear()
    templates._selection_cache.clear()
    cold, cold_rows = templates.search_for_state(state, '護甲')
    assert cold.index_cache == 'disk' and cold_rows == rows


def test_multiple_independent_diagnostics_and_no_variant_on_failure(library):
    path, payload = library()
    row = payload['batches'][0]['records'][0]
    row.pop('type')
    row['visibility'] = 'all'
    row.pop('name')
    write(path, payload)
    with pytest.raises(authoring.Diagnostics) as caught:
        templates.import_markdown('sample', relative(path))
    codes = {v['code'] for v in caught.value.issues}
    assert {'MISSING_FIELD', 'INVALID_VISIBILITY', 'EMPTY_TRANSLATION'} <= codes
    report = caught.value.report_path
    assert report and report.exists() and report.stat().st_mode & 0o077 == 0
    assert 'actual' in report.read_text()
    assert templates.status('sample')['variants'] == []


def test_unheaded_long_source_batches_preserve_all_unicode_ranges(library):
    source = ('A room. 😀\n\n' * 3000)
    _path, payload = library(source)
    directory = templates._root() / 'sample' / 'exports' / payload['export_id']
    registry = authoring.read_json(directory / 'registry.json')
    units = registry['units']
    assert len(units) > 3 and len(registry['batches']) > 1
    assert ''.join(u['text'] for u in units) == source.strip()
    assert all(u['hash'] == authoring.digest(u['text']) for u in units)
    assert units[0]['span'][0] == 0
    assert all(a['span'][1] == b['span'][0] for a, b in pairwise(units))
    files = authoring.read_json(directory / 'files.json')
    variant = ''
    for filename in files:
        file = result_copy(templates.IMPORT_DIR / filename)
        batch = authoring.parse_markdown(file.read_text())
        fill(batch, '一間房。' * 2500)  # intentionally over 6000 characters per record
        write(file, batch)
        variant = templates.import_markdown('sample', relative(file))
        if filename != files[-1]:
            assert variant.startswith('draft:')
            assert templates.status('sample')['variants'] == []
    assert not variant.startswith('draft:')
    manifest, records = templates._read_variant('sample', variant)
    assert manifest['schema_version'] == 4
    assert all(len(r['kp_text']) > 6000 for r in records)


def test_partial_replay_and_explicit_replacement(library):
    path, payload = library('First room.\n\n' * 1400)
    fill(payload, '房間。')
    write(path, payload)
    first = templates.import_markdown('sample', relative(path))
    assert first.startswith('draft:')
    assert templates.import_markdown('sample', relative(path)) == first
    payload['batches'][0]['records'][0]['kp_text'] = '另一個翻譯。'
    write(path, payload)
    with pytest.raises(authoring.Diagnostics) as caught:
        templates.import_markdown('sample', relative(path))
    assert caught.value.issues[0]['code'] == 'RECORD_CONFLICT'
    payload['batches'][0]['replace_record_ids'] = [payload['batches'][0]['records'][0]['id']]
    write(path, payload)
    assert templates.import_markdown('sample', relative(path)).startswith('draft:')


@pytest.mark.parametrize('damage', ['hash', 'missing', 'quote', 'number', 'privacy', 'json'])
def test_stale_exports_and_bad_rules_fail_closed(library, damage):
    path, payload = library('Armor 2. Armor 2.')
    fill(payload, '護甲 2。')
    record = payload['batches'][0]['records'][0]
    if damage in {'quote', 'number'}:
        record['rules'] = [{'check': {'text': '護甲 9' if damage == 'number' else '護甲 2',
                                     'evidence': [{'unit_id': 'u1', 'source_quote': 'Armor 2' if damage == 'quote' else 'Armor 2. Armor 2.'}]}}]
    if damage == 'privacy':
        record['public_text'] = '秘密'
    directory = templates._root() / 'sample' / 'exports' / payload['export_id']
    if damage == 'hash':
        registry = authoring.read_json(directory / 'registry.json')
        registry['units'][0]['page'] = 99
        authoring.atomic_json(directory / 'registry.json', registry)
    if damage == 'missing':
        (directory / 'registry.json').unlink()
    write(path, payload)
    if damage == 'json':
        path.write_text('```json\n{"authoring_version": }\n```')
    with pytest.raises(authoring.Diagnostics):
        templates.import_markdown('sample', relative(path))
    assert templates.status('sample')['variants'] == []


def v4_record(rid='npc', **overrides):
    return {'id': rid, 'schema_version': 4, 'name': '敵人', 'page': 1, 'chapter_id': 'c1',
            'type': 'npc', 'aliases': [], 'keywords': [], 'visibility': 'kp_only', 'public_text': '',
            'kp_text': '外貌描述。'*3000, 'rules': [{'check': {'text': '護甲 2；每輪攻擊 1 次。', 'source_quote': 'not indexed'}}],
            'related_record_ids': [], **overrides}


def test_large_records_are_stored_and_required_rules_win_over_optional_prose(monkeypatch):
    monkeypatch.setattr(scenario_retrieval.config, 'SCENARIO_RETRIEVAL_TOKEN_BUDGET', 6000)
    row = scenario_retrieval.project({'npc': v4_record()}, ['npc'], '攻擊')[0]
    assert row['complete_for_action'] and '護甲 2' in row['text']
    assert row['deferred_optional_ids']
    assert len(row['text']) < len(v4_record()['kp_text'])
    assert 'not indexed' not in row['text']


def test_cycles_cross_chapter_privacy_and_unknown_conditions():
    a = v4_record('a', related_record_ids=['b'])
    b = v4_record('b', related_record_ids=['a', 'secret-future'])
    rows = scenario_retrieval.project({'a': a, 'b': b}, ['a'], '敵人')
    assert rows[0]['blocked_dependency_count'] == 1 and not rows[0]['complete_for_action']
    assert 'secret-future' not in json.dumps(rows)
    public = scenario_retrieval.project({'a': a, 'b': b}, ['a'], '敵人', {'public'})
    assert '護甲' not in public[0]['text'] and not public[0]['record_ids']


def test_continuation_is_query_authorization_and_version_bound():
    records = {str(i): v4_record(str(i), rules=[], kp_text='完整規則。'*150) for i in range(4)}
    row = scenario_retrieval.project(records, list(records), '攻擊')[0]
    assert not row['complete_for_action'] and row['missing_required_ids']
    binding = ['group', 'timeline', 'role', 'variant', 'chapters', 'hash', 'query']
    scenario_retrieval.bind_continuation([row], binding)
    token = row['continuation_token']
    assert token and scenario_retrieval.continuation_offset(token, binding) > 0
    for i in range(len(binding)):
        changed = deepcopy(binding)
        changed[i] += '-changed'
        with pytest.raises(ValueError, match='失效'):
            scenario_retrieval.continuation_offset(token, changed)


def test_budget_accounts_for_full_context_and_reservations(monkeypatch):
    monkeypatch.setattr(scenario_retrieval.input_budget, 'estimate', lambda value, model: int(value))
    monkeypatch.setattr(scenario_retrieval.config, 'SCENARIO_CONTEXT_TOKEN_CEILING', 10000)
    monkeypatch.setattr(scenario_retrieval.config, 'SCENARIO_OUTPUT_TOKEN_RESERVE', 1000)
    monkeypatch.setattr(scenario_retrieval.config, 'SCENARIO_CONTEXT_SAFETY_TOKENS', 1000)
    assert scenario_retrieval.remaining_budget(7500, 'test') == 500
    assert scenario_retrieval.remaining_budget(9500, 'test') == 0


def test_export_command_and_help_route_show_prompt_privately(library, monkeypatch):
    async def exercise():
        from unittest.mock import AsyncMock

        from app import help_actions
        from app.commands.handlers import system
        from app.models import GroupState
        path, _ = library()
        monkeypatch.setattr(system, 'load_state', lambda _: GroupState(group_id='g'))
        monkeypatch.setattr(templates, 'export_template', lambda _: path)
        command = help_actions.build_command(help_actions.BY_KEY['template_export'], 'sample')
        for parts in (command.split(), ['/coc', 'scenario', 'template', 'export', 'sample']):
            reply, dm = AsyncMock(), AsyncMock()
            await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(), parts, is_keeper=True)
            assert authoring.PROMPT in dm.call_args.args[1]
            assert path.name in dm.call_args.args[1]
            assert str(path) not in reply.call_args.args[0]
        reply, dm = AsyncMock(), AsyncMock()
        await system.handle_system_command('g', 'player', reply, dm, AsyncMock(), AsyncMock(), command.split())
        dm.assert_not_called()
        assert '只有 KP' in reply.call_args.args[0]
    asyncio.run(exercise())

def test_export_failure_never_claims_success(library, monkeypatch):
    async def exercise():
        from unittest.mock import AsyncMock

        from app.commands.handlers import system
        from app.models import GroupState
        monkeypatch.setattr(system, 'load_state', lambda _: GroupState(group_id='g'))
        def fail(_):
            raise ValueError('source invalid')
        monkeypatch.setattr(templates, 'export_template', fail)
        reply, dm = AsyncMock(), AsyncMock()
        await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(),
                                           ['/coc', 'scenario', 'template', 'export', 'sample'], is_keeper=True)
        dm.assert_not_called()
        assert '無法處理' in reply.call_args.args[0]
    asyncio.run(exercise())

def test_unrelated_complete_search_cannot_release_mechanical_hold(monkeypatch):
    async def exercise():
        from app import keeper
        from app.agents.tool_gateway import make_tool_executor
        from app.models import GroupState
        calls = []
        def tool(state, name, data, *args):
            calls.append(name)
            return {'ok': True, **data}
        monkeypatch.setattr(keeper, '_execute_tool', tool)
        execute = make_tool_executor(GroupState(group_id='g'), [], [], 'player', [],
                                     evidence_incomplete=True, required_evidence_ids={'npc'})
        result = await execute('search_scenario', {'complete_for_action': True, 'evidence_record_ids': ['unrelated']})
        assert result['ok']
        assert (await execute('add_npc_to_combat', {}))['error'] == 'required_scenario_evidence_missing'
        assert 'add_npc_to_combat' not in calls
        assert (await execute('roll_dice', {}))['error'] == 'required_scenario_evidence_missing'
        await execute('search_scenario', {'complete_for_action': True, 'evidence_record_ids': ['npc']})
        assert (await execute('add_npc_to_combat', {}))['ok']
    asyncio.run(exercise())


def test_atomic_save_failure_does_not_publish_candidate(library, monkeypatch):
    path, payload = library()
    fill(payload)
    write(path, payload)
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(templates, '_save_variant', fail)
    with pytest.raises(OSError):
        templates.import_markdown('sample', relative(path))
    directory = templates._root() / 'sample' / 'exports' / payload['export_id']
    assert not (directory / 'draft.json').exists()
    assert templates.status('sample')['variants'] == []


def test_v4_empty_search_keeps_original_fallback(library, monkeypatch):
    path, payload = library('Unknown prose.')
    fill(payload, '室內陳設。')
    write(path, payload)
    variant = templates.import_markdown('sample', relative(path))
    templates.approve('sample', variant, reviewer_id='kp')
    state = SimpleNamespace(group_id='empty-fallback', timeline_id='t1', scenario_library_id='sample',
                            scenario_variant_id=variant, context_chapter_ids=['c1'], scenario_text='a unique-library clue')
    metrics = {}
    _, rows = templates.search_for_state(state, 'unique-library', metrics=metrics)
    assert rows and rows[0]['retrieval_source'] == 'original_fallback'
    assert metrics['query_fallback'] == 'chinese_no_match'


def test_report_caps_are_visible():
    report = authoring.Diagnostics([authoring.issue('X', str(i), 'field', 'expected', 'actual') for i in range(120)])
    assert len(report.issues) == 100 and report.total == 120


def test_many_chapters_and_large_dependency_groups_do_not_reject_storage(library, monkeypatch):
    # Synthetic 300-page campaign; unit/page mapping must not use printed labels.
    text = '\n'.join(f'Page {i} text.' for i in range(1,301))
    monkeypatch.setattr(scenario_rag, 'split_pages', lambda _: [(i, f'Page {i} text.') for i in range(1,301)])
    _path, payload = library(text)
    registry = authoring.read_json(templates._root() / 'sample' / 'exports' / payload['export_id'] / 'registry.json')
    assert registry['units'][0]['source_pages'] == list(range(1,301))
    a = v4_record('a', related_record_ids=['b'])
    b = v4_record('b', related_record_ids=['a'])
    assert len(a['kp_text']) + len(b['kp_text']) > 12000
    row = scenario_retrieval.project({'a':a, 'b':b}, ['a'], '護甲')[0]
    assert row['complete_for_action'] and set(row['record_ids']) == {'a','b'}


def test_exported_example_is_valid_authoring_and_source_json_is_not_payload(library):
    import re
    path, payload = library('Source example:\n```json\n{"not": "a workbook"}\n```\nArmor 2.')
    assert payload['batches'][0]['records']
    sample = json.loads(re.search(r'```text\n(.*?)\n```', path.read_text(), re.DOTALL).group(1))
    registry = {'units': [{'id': 'example-unit', 'source_id': 'c1-u1', 'span': [0, 8],
                           'page': 1, 'source_pages': [1], 'chapter_id': 'c1', 'text': 'Armor 2.'}]}
    compiled = authoring.compile_records([sample], registry, {'example-unit'}, complete=True)
    assert compiled[0]['rules'][0]['check']['source_quote'] == 'Armor 2'


def test_traversal_limit_does_not_claim_complete_or_expand_every_root():
    records = {str(i): v4_record(str(i), kp_text='', related_record_ids=[str(i+1)] if i < 139 else []) for i in range(140)}
    row = scenario_retrieval.project(records, ['0'], '護甲')[0]
    assert row['traversal_limited'] and not row['complete_for_action']
    assert row['record_count'] == scenario_retrieval.MAX_NODES


def test_legacy_error_report_identifies_expected_source_metadata(library):
    library()
    record = {'id': 'bad', 'source_id': 'c1-u1', 'page': 40, 'source_pages': [40],
              'chapter_id': 'wrong', 'visibility': 'all', 'rules': [{'check': 'wrong shape'}]}
    with pytest.raises(authoring.Diagnostics) as caught:
        templates._diagnose_records('sample', [record], version=3)
    assert len(caught.value.issues) >= 7
    pages = next(e for e in caught.value.issues if e['field'] == 'source_pages')
    assert pages['expected'] == '[1]' and pages['actual'] == '[40]'


def test_overlapping_quotation_is_ambiguous():
    registry = {'units': [{'id':'u1', 'source_id':'c1-u1', 'span':[0,3], 'page':1,
                           'source_pages':[1], 'chapter_id':'c1', 'text':'aaa'}]}
    record = authoring.blank_record('u1', 1)
    record.update(kp_text='描述', uncertainty='', rules=[{'check': {
        'text':'描述', 'evidence':[{'unit_id':'u1','source_quote':'aa'}]}}])
    with pytest.raises(authoring.Diagnostics) as caught:
        authoring.compile_records([record], registry, {'u1'}, complete=True)
    assert any(e['code'] == 'AMBIGUOUS_QUOTE' for e in caught.value.issues)


def test_crash_after_variant_save_remains_idempotent(library, monkeypatch):
    path, payload = library()
    fill(payload)
    write(path, payload)
    real_write = authoring.atomic_json
    def interrupt_draft(path, value):
        if path.name == 'draft.json':
            raise OSError('interrupted after variant commit')
        return real_write(path, value)
    monkeypatch.setattr(authoring, 'atomic_json', interrupt_draft)
    with pytest.raises(OSError):
        templates.import_markdown('sample', relative(path))
    created = templates.status('sample')['variants']
    assert len(created) == 1 and created[0]['review_status'] == 'review_required'
    monkeypatch.setattr(authoring, 'atomic_json', real_write)
    replay = templates.import_markdown('sample', relative(path))
    assert replay == created[0]['variant_id']
    assert len(templates.status('sample')['variants']) == 1


def test_long_history_budget_matches_provider_selection(monkeypatch):
    from app.services import input_budget
    monkeypatch.setattr(input_budget, '_encoding', lambda _: None)
    monkeypatch.setattr(scenario_retrieval.config, 'OPENAI_HISTORY_TOKEN_BUDGET', 1000)
    monkeypatch.setattr(scenario_retrieval.config, 'OPENAI_HISTORY_MIN_TURNS', 2)
    history = [{'role': 'user', 'content': 'old' * 2000} for _ in range(20)]
    history += [{'role': 'user', 'content': 'recent action'} for _ in range(2)]
    original = deepcopy(history)
    selected = input_budget.provider_history(history, 'unknown', 'openai')
    assert len(selected) == 2
    assert scenario_retrieval.remaining_budget([history], 'unknown') == 0
    assert scenario_retrieval.request_budget(['prompt'], history, 'unknown', 'openai') == scenario_retrieval.remaining_budget(['prompt', selected], 'unknown') > 0
    assert scenario_retrieval.request_budget(['prompt'], history, 'unknown', 'anthropic') == 0
    assert history == original


def test_continuation_accumulates_evidence_and_releases_tool_gate(monkeypatch):
    from app import keeper
    from app.agents.tool_gateway import make_tool_executor
    from app.models import GroupState
    records = {str(i): v4_record(str(i), rules=[], kp_text=str(i) * 3000) for i in range(4)}
    binding = ['same-turn']
    delivered = []
    async def exercise():
        monkeypatch.setattr(keeper, '_execute_tool', lambda state, name, data, *args: {'ok': True, **data})
        execute = make_tool_executor(GroupState(group_id='g'), [], [], 'player', [],
                                     evidence_incomplete=True, required_evidence_ids=set(records))
        offset = 0
        for page in range(4):
            row = scenario_retrieval.project(records, list(records), 'action', offset=offset)[0]
            delivered.extend(row['included_fragment_ids'])
            scenario_retrieval.bind_continuation([row], binding, offset)
            await execute('search_scenario', {'complete_for_action': row['complete_for_action'],
                                             'evidence_record_ids': row['root_record_ids']})
            if page < 3:
                assert not row['complete_for_action']
                assert (await execute('roll_dice', {}))['error'] == 'required_scenario_evidence_missing'
                token = row['continuation_token']
                assert scenario_retrieval.continuation_roots(token, binding) == list(records)
                offset = scenario_retrieval.continuation_offset(token, binding)
            else:
                assert row['complete_for_action'] and not row['continuation_token']
                assert row['missing_required_count'] == 0
                assert (await execute('roll_dice', {}))['ok']
    asyncio.run(exercise())
    assert delivered == [rid + '#kp_only' for rid in records]


def test_oversized_middle_fragment_cannot_be_skipped_by_cursor():
    records = {str(i): v4_record(str(i), rules=[], kp_text='x' * size)
               for i, size in enumerate([2000, 9000, 100])}
    row = scenario_retrieval.project(records, list(records), 'action')[0]
    scenario_retrieval.bind_continuation([row], ['binding'])
    offset = scenario_retrieval.continuation_offset(row['continuation_token'], ['binding'])
    assert offset == 1
    row = scenario_retrieval.project(records, list(records), 'action', offset=offset)[0]
    scenario_retrieval.bind_continuation([row], ['binding'], offset)
    assert not row['complete_for_action'] and not row['continuation_token']
    assert row['missing_required_ids'] == ['1#kp_only', '2#kp_only']


def test_search_continuation_keeps_roots_without_reranking_and_rejects_new_history(monkeypatch):
    records = {str(i): v4_record(str(i), rules=[], kp_text=str(i) * 3000) for i in range(2)}
    index = SimpleNamespace(record_store=records, text_hash='fixed-version')
    state = SimpleNamespace(group_id='g', timeline_id='t', scenario_library_id='s',
                            scenario_variant_id='v', context_chapter_ids=['c1'], log=[])
    monkeypatch.setattr(templates, 'index_for_state', lambda *args: index)
    calls = []
    def search(*args, **kwargs):
        calls.append(True)
        return scenario_retrieval.project(records, list(records), 'action')
    monkeypatch.setattr(templates.scenario_rag, 'search', search)
    _, first = templates.search_for_state(state, 'action', principal='player:p')
    token = first[0]['continuation_token']
    _, last = templates.search_for_state(state, 'action', continuation=token, principal='player:p')
    assert last[0]['complete_for_action']
    assert len(calls) == 1
    state.log.append({'role': 'user', 'content': 'next action'})
    with pytest.raises(ValueError, match='失效'):
        templates.search_for_state(state, 'action', continuation=token, principal='player:p')


@pytest.mark.parametrize('count', [1, 2, 3, 10, 201])
def test_packages_preserve_full_source_at_all_scales(tmp_path, count):
    source = ('門😀A' * 2666 + 'XY') * count  # exactly 8000 characters per logical batch
    block = {'id': 'c1-u1', 'text': source, 'page': 1, 'pages': [1], 'chapter_id': 'c1'}
    imports, root = tmp_path / 'imports', tmp_path / 'exports'
    path = authoring.export(root, imports, 'source', 'chapters', [block], title='燈塔')
    payload = authoring.parse_markdown(path.read_text())
    directory = root / payload['export_id']
    registry = authoring.read_json(directory / 'registry.json')
    files = authoring.read_json(directory / 'files.json')
    assert len(registry['batches']) == count
    assert len(files) == min(3, count)
    assert ''.join(u['text'] for u in registry['units']) == source
    assigned = []
    for index, filename in enumerate(files, 1):
        file = imports / filename
        assert file.name == f'燈塔_{index:02d}.md'
        package = authoring.parse_markdown(file.read_text())
        assert registry['packages'][package['package_id']] == [b['batch_id'] for b in package['batches']]
        assert '可下載的 UTF-8 .md' in file.read_text()
        assigned.extend(u for b in package['batches'] for r in b['records'] for u in r['unit_ids'])
    assert assigned == [u['id'] for u in registry['units']]
    assert len(set(assigned)) == len(assigned)


def test_filenames_use_real_title_safely_and_exports_do_not_collide(library):
    library()
    manifest_path = scenario_library._path('sample') / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['title'] = '另一個劇本 / The: Beacon\\夜\n'
    manifest_path.write_text(json.dumps(manifest))
    first, second = templates.export_template('sample'), templates.export_template('sample')
    assert first.name == second.name == '另一個劇本_The_Beacon_夜_01.md'
    assert first.parent != second.parent and first.exists()
    assert '另一個劇本_The_Beacon_夜_02.md' in templates.export_message('sample', first)
    assert authoring.filename_prefix('.../\\\n') == 'scenario'
    assert len(authoring.filename_prefix('測' * 200).encode()) <= 120
    assert authoring.result_filename('夜', 100) == '夜_100.md'


def test_partial_units_append_and_progress_continues_beyond_99(library):
    path, full = library('A' * 8000)
    fill(full)
    assert len(full['batches'][0]['records']) == 2
    payload = deepcopy(full)
    payload['batches'][0]['records'] = full['batches'][0]['records'][1:]
    write(path, payload)
    assert templates.import_markdown('sample', relative(path)).endswith(':1/2')
    payload['batches'][0]['records'] = full['batches'][0]['records'][:1]
    next_path = path.with_name('sample_100.md')
    write(next_path, payload)
    variant = templates.import_markdown('sample', relative(next_path))
    assert not variant.startswith('draft:')
    _, records = templates._read_variant('sample', variant)
    assert [r['id'] for r in records] == ['r1', 'r2']
    progress = templates.import_progress('sample', relative(next_path))
    assert '2/2' in progress and 'sample_101.md' in progress
    templates.approve('sample', variant, reviewer_id='kp')
    assert templates.import_markdown('sample', relative(next_path)) == variant
    assert templates.status('sample')['variants'][0]['review_status'] == 'approved'


@pytest.mark.parametrize('damage', ['cross_package', 'cross_unit', 'duplicate_batch', 'duplicate_id', 'overlap', 'replacement', 'invalid_later'])
def test_v2_invalid_submission_never_changes_saved_draft(library, damage):
    path, full = library('A' * 80000)
    fill(full, '描述')
    assert len(full['batches']) >= 2
    payload = deepcopy(full)
    payload['batches'] = [deepcopy(full['batches'][0])]
    write(path, payload)
    templates.import_markdown('sample', relative(path))
    directory = templates._root() / 'sample' / 'exports' / payload['export_id']
    before = (directory / 'draft.json').read_bytes()
    registry = authoring.read_json(directory / 'registry.json')
    payload = deepcopy(full)
    if damage == 'cross_package':
        payload['batches'][1]['batch_id'] = registry['packages']['p2'][0]
    elif damage == 'cross_unit':
        payload['batches'][1]['records'][0]['unit_ids'] = full['batches'][0]['records'][0]['unit_ids']
    elif damage == 'duplicate_batch':
        payload['batches'][1]['batch_id'] = payload['batches'][0]['batch_id']
    elif damage == 'duplicate_id':
        payload['batches'][1]['records'][0]['id'] = payload['batches'][0]['records'][0]['id']
    elif damage == 'overlap':
        record = deepcopy(payload['batches'][0]['records'][0])
        record['id'] = 'overlap'
        payload['batches'][0]['records'] = [record]
    elif damage == 'replacement':
        payload['batches'][0]['replace_record_ids'] = ['unknown']
    else:
        payload['batches'][1]['records'][0]['kp_text'] = ''
    write(path, payload)
    with pytest.raises(authoring.Diagnostics):
        templates.import_markdown('sample', relative(path))
    assert (directory / 'draft.json').read_bytes() == before
    assert not templates.status('sample')['variants']


def test_candidate_identity_is_independent_of_arrival_order(library):
    path, _ = library('A' * 80000)
    first = authoring.parse_markdown(path.read_text())
    directory = templates._root() / 'sample' / 'exports' / first['export_id']
    files = authoring.read_json(directory / 'files.json')
    outputs = []
    for filename in files:
        file = result_copy(templates.IMPORT_DIR / filename)
        payload = fill(authoring.parse_markdown(file.read_text()), '描述')
        outputs.append((file, payload))
    # Forward dependency across packages remains legal in a draft.
    outputs[0][1]['batches'][0]['records'][0]['related_record_ids'] = [outputs[-1][1]['batches'][-1]['records'][-1]['id']]
    for file, payload in outputs:
        write(file, payload)
        expected = templates.import_markdown('sample', relative(file))
    before = (directory / 'draft.json').read_bytes()
    (directory / 'draft.json').unlink()  # Simulate the same export started in reverse order.
    for file, payload in reversed(outputs):
        payload['batches'].reverse()
        for batch in payload['batches']:
            batch['records'].reverse()
        write(file, payload)
        actual = templates.import_markdown('sample', relative(file))
    assert actual == expected
    assert (directory / 'draft.json').read_bytes() == before
    assert len(templates.status('sample')['variants']) == 1


def test_legacy_authoring_v1_registry_and_batch_replacement_still_import(library):
    path, package = library('A' * 16000)
    directory = templates._root() / 'sample' / 'exports' / package['export_id']
    registry = authoring.read_json(directory / 'registry.json')
    for key in ('authoring_version', 'packaging_version', 'packages', 'filename_prefix'):
        registry.pop(key)
    authoring.atomic_json(directory / 'registry.json', registry)
    authoring.atomic_json(directory / 'registry.sha256.json', authoring.digest(registry))
    path = templates.IMPORT_DIR / 'old-authoring.md'
    payload = {'authoring_version': 1, 'export_id': package['export_id'], **package['batches'][0]}
    for record in payload['records']:
        record.update(kp_text='描述', uncertainty='')
    write(path, payload)
    assert templates.import_markdown('sample', path.name).startswith('draft:')
    payload['records'][0]['kp_text'] = '更新'
    write(path, payload)
    with pytest.raises(authoring.Diagnostics, match='校對'):
        templates.import_markdown('sample', path.name)
    payload['replace_batch'] = True
    write(path, payload)
    assert templates.import_markdown('sample', path.name).startswith('draft:')
    for bid, units in list(registry['batches'].items())[1:]:
        payload['batch_id'] = bid
        payload['records'] = [authoring.blank_record(u, i + 100) for i, u in enumerate(units)]
        for record in payload['records']:
            record.update(kp_text='描述', uncertainty='')
        write(path, payload)
        result = templates.import_markdown('sample', path.name)
    assert not result.startswith('draft:')


def test_help_lists_results_and_legacy_only_with_safe_export_paths(library, monkeypatch):
    from app import help_actions
    path, payload = library()
    monkeypatch.setattr(scenario_library, 'list_scenarios', lambda: [{'id': 'sample', 'title': '測試'}])
    fill(payload)
    write(path, payload)
    options = help_actions._template_options('template_import')
    assert [value for _, value in options] == ['sample ' + relative(path)]
    source = path.parent.parent / 'source' / path.name
    for invalid in (relative(source), '../outside.md', str(path), relative(path).replace('/results/', '/results/../results/')):
        with pytest.raises(ValueError):
            templates.import_markdown('sample', invalid)
    link = path.parent / 'link.md'
    link.symlink_to(path)
    with pytest.raises(ValueError):
        templates.import_markdown('sample', relative(link))
    original_results = path.parent.rename(path.parent.with_name('original_results'))
    path.parent.symlink_to(original_results, target_is_directory=True)
    assert help_actions._template_options('template_import') == []
    with pytest.raises(ValueError):
        templates.import_markdown('sample', relative(path))


def test_export_preflight_and_interruption_leave_no_ready_files(tmp_path, monkeypatch):
    imports, root = tmp_path / 'imports', tmp_path / 'exports'
    block = {'id': 'c1-u1', 'text': 'A' * 8000, 'page': 1, 'pages': [1], 'chapter_id': 'c1'}
    monkeypatch.setattr(authoring, 'MAX_FILE_BYTES', 100)
    with pytest.raises(ValueError, match='RESOURCE_LIMIT'):
        authoring.export(root, imports, 's', 'c', [block])
    assert not root.exists() and not imports.exists()
    monkeypatch.setattr(authoring, 'MAX_FILE_BYTES', 20_000_000)
    real_write = authoring.atomic_json
    def fail(path, value):
        if path.name == 'files.json':
            raise OSError('disk failed')
        real_write(path, value)
    monkeypatch.setattr(authoring, 'atomic_json', fail)
    with pytest.raises(OSError):
        authoring.export(root, imports, 's', 'c', [block])
    assert list(root.iterdir()) == list(imports.iterdir()) == []


def test_partial_import_command_sends_private_progress(library, monkeypatch):
    from unittest.mock import AsyncMock

    from app import help_actions
    from app.commands.handlers import system
    from app.models import GroupState
    path, payload = library('A' * 16000)
    fill(payload)
    write(path, payload)
    monkeypatch.setattr(system, 'load_state', lambda _: GroupState(group_id='g'))
    command = help_actions.build_command(help_actions.BY_KEY['template_import'], 'sample ' + relative(path))
    async def exercise():
        reply, dm = AsyncMock(), AsyncMock()
        await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(), command.split(), is_keeper=True)
        messages = '\n'.join(call.args[1] for call in dm.call_args_list)
        assert '翻譯進度：2/4' in messages and '尚未完成：b2' in messages
        assert 'sample_02.md' in messages
        assert 'u3' not in reply.call_args.args[0] and str(path.parent) not in reply.call_args.args[0]
    asyncio.run(exercise())


@pytest.mark.parametrize('failure', ['render_size', 'unit_limit', 'publish_registry', 'second_staging'])
def test_export_resource_and_publication_boundaries(tmp_path, monkeypatch, failure):
    from pathlib import Path

    imports, root = tmp_path / 'imports', tmp_path / 'exports'
    block = {'id': 'c1-u1', 'text': 'A' * 8000, 'page': 1, 'pages': [1], 'chapter_id': 'c1'}
    if failure == 'render_size':
        block['text'] = 'hello'
        monkeypatch.setattr(authoring, 'MAX_FILE_BYTES', 1000)
    elif failure == 'unit_limit':
        monkeypatch.setattr(authoring, 'MAX_RECORDS', 1)
    elif failure == 'publish_registry':
        rename = Path.rename
        def fail_rename(self, target):
            if self.parent == root and self.name.startswith('.building-'):
                raise OSError('registry publication failed')
            return rename(self, target)
        monkeypatch.setattr(Path, 'rename', fail_rename)
    else:
        mkdtemp = authoring.tempfile.mkdtemp
        def fail_staging(*args, **kwargs):
            if kwargs.get('dir') == imports:
                raise OSError('staging creation failed')
            return mkdtemp(*args, **kwargs)
        monkeypatch.setattr(authoring.tempfile, 'mkdtemp', fail_staging)
    with pytest.raises((ValueError, OSError)):
        authoring.export(root, imports, 's', 'c', [block])
    assert not root.exists() or list(root.iterdir()) == []
    assert not imports.exists() or list(imports.iterdir()) == []


def test_full_coverage_still_validates_dependencies_and_requires_review(library):
    path, payload = library()
    fill(payload)
    record = payload['batches'][0]['records'][0]
    record['related_record_ids'] = ['missing']
    write(path, payload)
    with pytest.raises(authoring.Diagnostics) as caught:
        templates.import_markdown('sample', relative(path))
    assert any(e['code'] == 'UNKNOWN_DEPENDENCY' for e in caught.value.issues)
    assert not templates.status('sample')['variants']
    record['related_record_ids'] = []
    record['uncertainty'] = '仍須核對原文'
    write(path, payload)
    variant = templates.import_markdown('sample', relative(path))
    assert templates.status('sample')['variants'][0]['review_status'] == 'review_required'
    with pytest.raises(ValueError):
        templates.approve('sample', variant, reviewer_id='kp')
