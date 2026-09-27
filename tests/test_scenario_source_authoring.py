"""External source round trips use real PDFs, registries and library publication."""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import wraps
from unittest.mock import AsyncMock

import pymupdf
import pytest

from app import help_actions, scenario_numbers
from app import scenario_authoring as authoring
from app import scenario_library as library
from app import scenario_source_authoring as source
from app import scenario_source_review as review
from app import scenario_templates as templates
from app.models import GroupState


def _sync(test):
    @wraps(test)
    def call(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return call


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(templates, 'IMPORT_DIR', tmp_path / 'imports')
    def make(texts=('Armor 2.\n17', 'Damage 1D40.\n18'), *, markers=True, raster_pages=()):
        with pymupdf.open() as pdf:
            for index, text in enumerate(texts):
                p = pdf.new_page(width=300, height=400)
                if index in raster_pages:
                    with pymupdf.open() as raster:
                        image_page = raster.new_page(width=300, height=400)
                        image_page.insert_text((20, 30), text)
                        p.insert_image(p.rect, stream=image_page.get_pixmap().tobytes('png'))
                elif text:
                    p.insert_text((20, 30), text)
            content = pdf.tobytes()
        raw = '\n\n'.join(f'--- 第 {n} 頁 ---\n{t}' for n, t in enumerate(('' if i in raster_pages else t for i, t in enumerate(texts)), 1)) if markers else 'Unlocated old OCR'
        sid = library.save_scenario(content, title='Real Scenario', filename='original.pdf', preview='test', text=raw,
                                    indexes={'stale': True}, pregens=[{'name': 'stale'}],
                                    page_maps={'1': {'stale': True}}, page_images={})
        path = source.export_source(sid)
        payload = authoring.parse_markdown(path.read_text())
        result = path.parent.parent / 'results' / path.name
        return sid, path, result, payload
    return make


def write(path, payload):
    path.write_text(review._markdown(payload))
    return str(path.relative_to(templates.IMPORT_DIR))


def finish(payload, texts):
    for row, text in zip(payload['pages'], texts, strict=True):
        row.update(status='complete' if text else 'visual_only', text=text, unresolved=[])


def run(sid, path, payload, user='keeper'):
    return source.import_source(sid, write(path, payload), imported_by=user)


def test_single_workbook_every_page_native_fallback_and_prompt(prepared):
    sid, path, _, payload = prepared(tuple(f'Age {20+n}. Skill Custom {40+n}. Luck: blank.' for n in range(27)), markers=False)
    assert path.name == 'Real_Scenario_01.md'
    assert list(path.parent.glob('*.md')) == [path]
    assert len(payload['pages']) == 27
    assert payload['pages'][-1]['pdf_page'] == 27
    registry = source._load(sid, payload['export_id'])
    assert registry['fallback_native'] and registry['source_text'] == 'Unlocated old OCR'
    assert all(p['original'] == '' and p['native'] for p in registry['pages'])
    assert source.PROMPT in path.read_text()
    assert 'Blank Luck stays blank' in path.read_text()
    assert str(library._path(sid) / 'source.pdf') in source.export_message(sid, path)
    assert path.stat().st_mode & 0o077 == 0
    assert not list(path.parent.glob('*.png'))


def test_partial_replace_publish_numeric_correction_chinese_roundtrip(prepared):
    sid, _, path, payload = prepared()
    original = {p.name: p.read_bytes() for p in library._path(sid).iterdir() if p.is_file()}
    partial = {**payload, 'pages': [deepcopy(payload['pages'][0])]}
    finish(partial, ['Armor 3.'])
    first = run(sid, path, partial)
    assert first['pending'] == ['page-0002'] and not first['published_id']
    finish(partial, ['Armor 2.'])
    run(sid, path, partial)
    assert len(list(source._root(sid, payload['export_id']).glob('revision-*.json'))) == 2
    run(sid, path, partial)
    assert len(list(source._root(sid, payload['export_id']).glob('revision-*.json'))) == 2
    rest = {**payload, 'pages': [deepcopy(payload['pages'][1])]}
    finish(rest, ['Damage +1D4.'])
    new_id = run(sid, path, rest)['published_id']
    assert new_id and new_id != sid
    assert run(sid, path, rest, user='other_keeper')['published_id'] == new_id
    assert len(library.list_scenarios()) == 2
    assert original == {p.name: p.read_bytes() for p in library._path(sid).iterdir() if p.is_file()}
    manifest, text = templates._source(new_id)
    assert text == '--- 第 1 頁 ---\nArmor 2.\n\n--- 第 2 頁 ---\nDamage +1D4.'
    new_root = library._path(new_id)
    for name, expected in [('indexes', {}), ('pregens', []), ('scene_maps', {})]:
        assert json.loads((new_root / f'{name}.json').read_text()) == expected
    audit = json.loads((new_root / 'source_review.json').read_text())
    assert audit['origin'] == 'external_ai' and audit['imported_by'] == 'keeper' and 'reviewer' not in audit
    assert audit['changes'][1]['removed_counts'] == dict(scenario_numbers.counts('1D40 18'))
    assert audit['changes'][1]['added_counts'] == dict(scenario_numbers.counts('+1D4'))
    assert library.load_context(new_id)['text'] == text
    assert not templates.status(new_id)['variants']
    assert all(a['visibility'] == 'kp_only' for a in manifest['image_assets'])
    assert [b['pages'] for b in templates._blocks(manifest, text)] == [[1], [2]]
    chinese = templates.export_template(new_id)
    translated = authoring.parse_markdown(chinese.read_text())
    for batch in translated['batches']:
        for record in batch['records']:
            record.update(kp_text='護甲 2。傷害 +1D4。', uncertainty='')
    result = chinese.parent.parent / 'results' / chinese.name
    variant = templates.import_markdown(new_id, write(result, translated))
    templates.approve(new_id, variant, reviewer_id='keeper')
    assert templates.status(new_id)['variants'][0]['review_status'] == 'approved'
    # A sealed export can be retried, but never revised in place.
    finish(rest, ['Damage +1D6.'])
    with pytest.raises(ValueError, match='already published'):
        run(sid, path, rest)
    assert len(library.list_scenarios()) == 2


def test_visual_page_can_remove_ocr_garbage_and_preserves_image(prepared):
    sid, _, path, payload = prepared(('099 17',))
    finish(payload, [''])
    new_id = run(sid, path, payload)['published_id']
    root = library._path(new_id)
    manifest, text = templates._source(new_id)
    assert '[SOURCE_IMAGE page_1.png:' in text and (root / 'images/page_1.png').is_file()
    quality = json.loads((root / 'parse_quality.json').read_text())
    assert quality['pages'][0]['selected_sha256'] == review._sha(text.split('\n', 1)[1].encode())
    assert manifest['content_hash'] == review._sha((root / 'scenario.txt').read_bytes())


@pytest.mark.parametrize('text', ['  Armor 2.\n\t', '\r\nArmor 2.\r\n', '\u3000Armor 2.\n\n'])
def test_published_exact_serialization_and_newline_normalization(prepared, text):
    sid, _, path, payload = prepared(('old',))
    finish(payload, [text])
    new_id = run(sid, path, payload)['published_id']
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    root = library._path(new_id)
    assert (root / 'scenario.txt').read_bytes() == ('--- 第 1 頁 ---\n' + normalized).encode()
    assert source.import_source(sid, str(path.relative_to(templates.IMPORT_DIR)), imported_by='keeper')['published_id'] == new_id
    audit = json.loads((root / 'source_review.json').read_text())
    assert audit['changes'][0]['published_text'] == normalized


@pytest.mark.parametrize('case', ['duplicate', 'unknown', 'shifted', 'empty', 'bad_status', 'visual_text', 'complete_issues',
                                  'unresolved_empty', 'extra_field', 'package', 'boolean_version', 'boolean_page',
                                  'change', 'marker', 'image_marker', 'bad_text', 'bad_changes'])
def test_invalid_submission_is_atomic(prepared, case):
    sid, _, path, payload = prepared()
    root = source._root(sid, payload['export_id'])
    before = (root / 'draft.json').read_bytes()
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    p = payload['pages'][1]
    if case == 'duplicate':
        payload['pages'][1] = deepcopy(payload['pages'][0])
    elif case == 'unknown':
        p['page_id'] = 'page-9999'
    elif case == 'shifted':
        p['pdf_page'] = 1
    elif case == 'empty':
        p['text'] = ''
    elif case == 'bad_status':
        p['status'] = 'approved'
    elif case == 'visual_text':
        p['status'] = 'visual_only'
    elif case == 'complete_issues':
        p['unresolved'] = ['Unknown']
    elif case == 'unresolved_empty':
        p['status'] = 'unresolved'
    elif case == 'extra_field':
        payload['approved'] = True
    elif case == 'package':
        payload['package_id'] = 'p2'
    elif case == 'boolean_version':
        payload['source_authoring_version'] = True
    elif case == 'boolean_page':
        payload['pages'][0]['pdf_page'] = True
    elif case == 'change':
        p['changes'] = [{'kind': 'ocr_numeric', 'before': 'x', 'after': 'y', 'reason': ''}]
    elif case == 'marker':
        p['text'] = '--- 第 99 頁 ---\nother'
    elif case == 'image_marker':
        p['text'] = '[SOURCE_IMAGE ../../secret.png]'
    elif case == 'bad_text':
        p['text'] = 2
    else:
        p['changes'] = 'not a list'
    with pytest.raises(ValueError):
        run(sid, path, payload)
    assert (root / 'draft.json').read_bytes() == before
    assert not list(root.glob('revision-*'))
    assert len(library.list_scenarios()) == 1


def test_unresolved_and_omitted_pages_stay_pending(prepared):
    sid, _, path, payload = prepared(('Age 32. Luck:', 'Custom skill 42.', ''))
    payload['pages'] = payload['pages'][:2]
    payload['pages'][0].update(text='Age 32. Luck: blank.', status='complete', unresolved=[])
    payload['pages'][1].update(text='Custom skill', unresolved=['PDF value unreadable'])
    info = run(sid, path, payload)
    assert info['completed'] == ['page-0001'] and info['unresolved'] == ['page-0002']
    assert info['pending'] == ['page-0003'] and not info['published_id']
    assert source.status(sid, payload['export_id']) == [info]


@pytest.mark.parametrize('change', ['source', 'pdf', 'chapters', 'registry'])
def test_stale_source_identity_rejected(prepared, change):
    sid, _, path, payload = prepared()
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    root = library._path(sid)
    if change in ('source', 'pdf'):
        (root / ('scenario.txt' if change == 'source' else 'source.pdf')).write_text('Changed')
    else:
        target = root / 'manifest.json' if change == 'chapters' else source._root(sid, payload['export_id']) / 'registry.json'
        data = json.loads(target.read_text())
        if change == 'chapters':
            data['chapters'][0]['title'] = 'Changed'
        else:
            data['pages'][0]['native'] = 'Changed'
        target.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        run(sid, path, payload)
    assert len(library.list_scenarios()) == 1


def test_paths_picker_and_file_change(prepared, tmp_path):
    sid, exported, path, payload = prepared()
    filename = write(path, payload)
    state = GroupState(group_id='g')
    options = help_actions.options_for('source_import', state, 'keeper')
    assert len(options) == 1 and filename in options[0][1]
    assert not any(filename in value for _, value in help_actions.options_for('template_import', state, 'keeper'))
    expected = options[0][1].split()[-1]
    payload['pages'][0]['text'] = 'Changed after picker'
    write(path, payload)
    with pytest.raises(ValueError, match='changed'):
        source.import_source(sid, filename, imported_by='keeper', expected_sha256=expected)
    root_result = templates.IMPORT_DIR / 'AI result.md'
    write(root_result, payload)
    assert len(help_actions.options_for('source_import', state, 'keeper')) == 2
    symlink = templates.IMPORT_DIR / 'link.md'
    symlink.symlink_to(root_result)
    for bad in ('../AI result.md', str(path), str(exported.relative_to(templates.IMPORT_DIR)), 'link.md'):
        with pytest.raises(ValueError):
            source.import_path(bad)
    directory = templates.IMPORT_DIR / ('source-export-' + 'a' * 32)
    directory.symlink_to(path.parent.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='Symlinks'):
        source.import_path(str(directory.name + '/results/' + path.name))
    assert help_actions.options_for('source_status', state, 'keeper')[0][1] == f'{sid} {payload["export_id"]}'


def test_concurrent_final_import_publishes_once(prepared):
    sid, _, path, payload = prepared()
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    filename = write(path, payload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: source.import_source(sid, filename, imported_by='keeper'), range(4)))
    assert len({r['published_id'] for r in outcomes}) == 1 and outcomes[0]['published_id']
    assert len(library.list_scenarios()) == 2


def test_crash_after_publish_before_receipt_recovers_and_seals(prepared, monkeypatch):
    sid, _, path, payload = prepared()
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    atomic = authoring.atomic_json
    def fail_receipt(path, value):
        if path.name == 'draft.json' and value.get('published_id'):
            raise OSError('receipt failed')
        atomic(path, value)
    monkeypatch.setattr(authoring, 'atomic_json', fail_receipt)
    with pytest.raises(OSError, match='receipt failed'):
        run(sid, path, payload)
    assert len(library.list_scenarios()) == 2
    monkeypatch.setattr(authoring, 'atomic_json', atomic)
    altered = deepcopy(payload)
    altered['pages'][0]['text'] = 'Different 42.'
    with pytest.raises(ValueError, match='already published'):
        run(sid, path, altered)
    assert run(sid, path, payload)['published_id']
    assert len(library.list_scenarios()) == 2


def test_failed_publication_is_retryable_without_partial_library(prepared, monkeypatch):
    sid, _, path, payload = prepared()
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    render = pymupdf.Page.get_pixmap
    def fail(*args, **kwargs):
        raise RuntimeError('render failed')
    monkeypatch.setattr(pymupdf.Page, 'get_pixmap', fail)
    with pytest.raises(RuntimeError, match='render failed'):
        run(sid, path, payload)
    assert len(library.list_scenarios()) == 1
    assert not list(library.SCENARIO_LIBRARY_DIR.glob('.source-ai-*'))
    monkeypatch.setattr(pymupdf.Page, 'get_pixmap', render)
    assert run(sid, path, payload)['published_id']


def test_size_limit_never_truncates_or_emits_extra_files(prepared, monkeypatch):
    sid, _, _, _ = prepared()
    before = set(templates.IMPORT_DIR.glob('source-export-*'))
    monkeypatch.setattr(authoring, 'MAX_FILE_BYTES', 100)
    with pytest.raises(ValueError, match='exceeds 20 MB'):
        source.export_source(sid)
    assert set(templates.IMPORT_DIR.glob('source-export-*')) == before


@_sync
@pytest.mark.parametrize('keeper,assistant', [(True, ''), (False, 'kp')])
async def test_commands_allow_trusted_keeper_or_assistant_and_preserve_state(prepared, monkeypatch, keeper, assistant):
    from app.commands.handlers import system
    sid, _, _, _ = prepared()
    state = GroupState(group_id='g', kp_assistant_user_id=assistant)
    original = deepcopy(state)
    monkeypatch.setattr(system, 'load_state', lambda _: state)
    reply, dm = AsyncMock(), AsyncMock()
    cmd = help_actions.build_command(help_actions.BY_KEY['source_export'], sid)
    await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(), cmd.split(), is_keeper=keeper)
    assert source.PROMPT in dm.call_args.args[1]
    assert source.PROMPT not in reply.call_args.args[0]
    assert state == original


@_sync
async def test_commands_deny_player_and_keep_diagnostics_private(prepared, monkeypatch):
    from app.commands.handlers import system
    sid, _, path, payload = prepared()
    state = GroupState(group_id='g')
    monkeypatch.setattr(system, 'load_state', lambda _: state)
    reply, dm = AsyncMock(), AsyncMock()
    parts = f'/coc scenario source import {sid} missing.md'.split()
    await system.handle_system_command('g', 'player', reply, dm, AsyncMock(), AsyncMock(), parts)
    dm.assert_not_called()
    assert '只有' in reply.call_args.args[0]
    await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(), parts, is_keeper=True)
    assert 'missing' in dm.call_args.args[1] or 'missing' in dm.call_args.args[1].lower()
    assert 'missing' not in reply.call_args.args[0]
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    parts = f'/coc scenario source import {sid} {write(path, payload)}'.split()
    await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(), parts, is_keeper=True)
    result = reply.call_args.args[0]
    assert isinstance(result, source.SourceReadyMessage) and result.scenario_id != sid and result.owner_id == 'kp'


def test_source_changed_during_rendering_aborts_without_partial_version(prepared, monkeypatch):
    sid, _, path, payload = prepared()
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    render = pymupdf.Page.get_pixmap
    def change(page, *args, **kwargs):
        result = render(page, *args, **kwargs)
        (library._path(sid) / 'scenario.txt').write_text('Concurrent update')
        return result
    monkeypatch.setattr(pymupdf.Page, 'get_pixmap', change)
    with pytest.raises(ValueError):
        run(sid, path, payload)
    assert len(library.list_scenarios()) == 1
    assert not list(library.SCENARIO_LIBRARY_DIR.glob('.source-ai-*'))


def test_preparation_capacity_preserves_draft_on_failure(prepared, monkeypatch):
    sid, _, path, payload = prepared()
    root = source._root(sid, payload['export_id'])
    before = (root / 'draft.json').read_bytes()
    monkeypatch.setattr(source, 'MAX_STORAGE_BYTES', (root / 'registry.json').stat().st_size + 1)
    with pytest.raises(ValueError, match='200 MB'):
        run(sid, path, payload)
    assert (root / 'draft.json').read_bytes() == before
    assert not list(root.glob('revision-*'))


def test_english_flow_calls_no_provider(prepared, monkeypatch):
    from app.providers import anthropic_provider, gemini_provider, openai_provider
    def forbidden(*args, **kwargs):
        pytest.fail('English preparation must not call a model')
    for provider in (anthropic_provider, gemini_provider, openai_provider):
        for name in ('run_conversation', 'analyze_text', 'analyze_image', '_create_client', 'get_async_client'):
            monkeypatch.setattr(provider, name, forbidden)
    sid, _, path, payload = prepared(('Age 32. Luck: blank. Ancient Languages 46.',))
    finish(payload, ['Age 32. Luck: blank. Ancient Languages 46.'])
    assert run(sid, path, payload)['published_id']


def test_picker_reports_invalid_source_files_without_weakening_import(prepared):
    sid, _, path, payload = prepared()
    payload['pages'][0]['pdf_page'] = 77
    write(path, payload)
    options, problems = source.inspect_results(sid)
    assert not options and 'shifted' in problems[0] and path.name in problems[0]
    with pytest.raises(ValueError, match='shifted'):
        run(sid, path, payload)


@_sync
async def test_private_delivery_failure_does_not_publish_diagnostics(prepared, monkeypatch):
    from app.commands.handlers import system
    sid, _, _, _ = prepared()
    monkeypatch.setattr(system, 'load_state', lambda _: GroupState(group_id='g'))
    reply, dm = AsyncMock(), AsyncMock(side_effect=RuntimeError('secret PDF content'))
    await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(),
                                      f'/coc scenario source status {sid}'.split(), is_keeper=True)
    assert all('secret' not in call.args[0] for call in reply.call_args_list)
    assert '私訊' in reply.call_args.args[0]
    dm.reset_mock()
    await system.handle_system_command('g', 'kp', reply, dm, AsyncMock(), AsyncMock(),
                                      f'/coc scenario source import {sid} missing.md'.split(), is_keeper=True)
    assert all('secret' not in call.args[0] and 'missing' not in call.args[0] for call in reply.call_args_list)


@_sync
async def test_discord_ready_controls_bind_new_version_owner_and_permission(monkeypatch):
    from types import SimpleNamespace

    from app import discord_bot as bot
    state = GroupState(group_id='discord-channel-123')
    monkeypatch.setattr(bot, 'load_group_state', lambda _: state)
    result = source.SourceReadyMessage('new-version', '42')
    view = bot.SourceReadyView('discord-channel-123', result)
    assert [button.key for button in view.children] == ['template_export', 'source_use']
    assert help_actions.BY_KEY['source_use'].confirm
    finish_action = AsyncMock()
    monkeypatch.setattr(bot, '_finish_help_action', finish_action)
    interaction = SimpleNamespace(channel=SimpleNamespace(id=123), user=SimpleNamespace(id=42, roles=[]),
                                  response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False))
    await view.children[0].callback(interaction)
    finish_action.assert_not_called()
    interaction.user.roles = [SimpleNamespace(name='Keeper')]
    await view.children[0].callback(interaction)
    assert finish_action.call_args.kwargs['selected'] == 'new-version'
    finish_action.reset_mock()
    interaction.user.id = 99
    await view.children[1].callback(interaction)
    finish_action.assert_not_called()
    interaction.user.id = 42
    interaction.channel.id = 999
    await view.children[1].callback(interaction)
    finish_action.assert_not_called()


@_sync
async def test_discord_both_reply_adapters_attach_ready_buttons(monkeypatch):
    from types import SimpleNamespace

    from app import discord_bot as bot
    monkeypatch.setattr(bot.config, 'LOG_ENABLED', False)
    result = source.SourceReadyMessage('new-source', '42')
    channel = SimpleNamespace(id=123, send=AsyncMock())
    await bot._make_reply(channel)(result)
    assert isinstance(channel.send.call_args.kwargs['view'], bot.SourceReadyView)
    interaction = SimpleNamespace(channel=channel, followup=SimpleNamespace(send=AsyncMock()))
    await bot._make_interaction_reply(interaction)(result)
    assert interaction.followup.send.call_args.kwargs['ephemeral']
    assert isinstance(interaction.followup.send.call_args.kwargs['view'], bot.SourceReadyView)


def test_separate_processes_publish_one_version(prepared):
    import subprocess
    import sys
    sid, _, path, payload = prepared()
    finish(payload, ['Armor 2.', 'Damage +1D4.'])
    filename = write(path, payload)
    program = '''import sys, json
from pathlib import Path
from app import scenario_library as library, scenario_templates as templates, scenario_source_authoring as source
library.SCENARIO_LIBRARY_DIR = Path(sys.argv[1])
templates.IMPORT_DIR = Path(sys.argv[2])
print(json.dumps(source.import_source(sys.argv[3], sys.argv[4], imported_by='keeper')))
'''
    args = [sys.executable, '-c', program, str(library.SCENARIO_LIBRARY_DIR), str(templates.IMPORT_DIR), sid, filename]
    workers = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        outcomes = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=20)
            assert worker.returncode == 0, stderr
            outcomes.append(json.loads(stdout))
        assert outcomes[0]['published_id'] == outcomes[1]['published_id']
        assert len(library.list_scenarios()) == 2
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()


@pytest.mark.parametrize('asset', ['images/page_1.png', 'scenario.txt', 'source.pdf'])
def test_published_retry_detects_changed_artifacts(prepared, asset):
    sid, _, path, payload = prepared(('Armor 2.',))
    finish(payload, ['Armor 2.'])
    target = run(sid, path, payload)['published_id']
    (library._path(target) / asset).write_bytes(b'changed')
    with pytest.raises(ValueError):
        run(sid, path, payload)
    assert len(library.list_scenarios()) == 2


def test_raster_cards_blank_pages_and_reference_backs_all_publish(prepared):
    texts = [f'Physical page {i+1}.' for i in range(27)]
    texts[10] = 'Age 32. Luck: blank. Ancient Languages 46. Background: researcher.'
    texts[11] = 'Armor 2. Damage +1D4. Ability: once per combat after a hit.'
    texts[15] = ''
    texts[22] = 'Map label: Library. Stairs lead to the entrance.'
    sid, _, path, payload = prepared(tuple(texts), raster_pages=(10, 11))
    registry = source._load(sid, payload['export_id'])
    assert registry['pages'][10]['native'] == '' and registry['pages'][10]['original'] == ''
    assert len(payload['pages']) == 27
    finish(payload, texts)
    target = run(sid, path, payload)['published_id']
    text = (library._path(target) / 'scenario.txt').read_text()
    assert 'Age 32. Luck: blank. Ancient Languages 46.' in text
    assert 'once per combat after a hit' in text and 'Map label: Library' in text
    assert '[SOURCE_IMAGE page_16.png:' in text
    assert len(list((library._path(target) / 'images').glob('*.png'))) == 27


@_sync
async def test_keeper_can_select_corrected_english_explicitly_without_enrollment(prepared, monkeypatch):
    from app.commands.handlers import system
    sid, _, path, payload = prepared(('Armor 1D40.',))
    finish(payload, ['Armor +1D4.'])
    target = run(sid, path, payload)['published_id']
    state = GroupState(group_id='g', scenario_text='Original active game', scenario_library_id=sid)
    before = deepcopy(state)
    monkeypatch.setattr(system, 'load_state', lambda _: state)
    monkeypatch.setattr(system, 'save_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(system, 'clear_page_images', lambda *args: None)
    monkeypatch.setattr(system, 'save_page_image', lambda *args: None)
    monkeypatch.setattr(templates, 'schedule_index_prewarm', lambda *args: None)
    monkeypatch.setattr(templates, 'preferred_variant', lambda *args: 'stale-zh-preference')
    selected_variants = []
    monkeypatch.setattr(templates, 'select_variant', lambda *args: selected_variants.append(args))
    monkeypatch.setattr(templates, 'preference_notice', lambda *args: '')
    options = help_actions.options_for('source_use', state, '42')
    assert options[0][1] == f'{target} original' and sid in options[0][0]
    command = help_actions.build_command(help_actions.BY_KEY['source_use'], options[0][1])
    reply = AsyncMock()
    await system.handle_system_command('g', '42', reply, AsyncMock(), AsyncMock(), AsyncMock(), command.split())
    assert state == before  # Ordinary player is denied even after a valid selection.
    await system.handle_system_command('g', '42', reply, AsyncMock(), AsyncMock(), AsyncMock(), command.split(), is_keeper=True)
    assert state.scenario_library_id == target and state.scenario_variant_id == 'original'
    assert state.scenario_text.endswith('Armor +1D4.') and state.kp_assistant_user_id == ''
    assert selected_variants == [('g', target, 'original')]


def test_export_prompt_prefers_full_delivery_and_omits_backlog_placeholders(prepared):
    sid, path, _, _ = prepared()
    for content in (path.read_text(), source.export_message(sid, path)):
        assert '預設一次完成全部實體頁' in content
        assert '每批 5 頁' in content
        assert '待處理清單寫在 JSON 外' in content
        assert '不能用 unresolved 佔位' in content
        assert '不要將先前完成頁面重送為 unresolved' in content
    assert 'not attempted is not unresolved' in path.read_text()
    assert 'remove untouched placeholders from partial replies' in path.read_text()


def test_disjoint_partial_results_retain_completed_pages_without_unresolved_backlog(prepared):
    sid, _, path, payload = prepared(tuple(f'Page {i}.' for i in range(1, 16)))
    first = {**payload, 'pages': deepcopy(payload['pages'][:5])}
    finish(first, [f'Corrected page {i}.' for i in range(1, 6)])
    info = run(sid, path, first)
    assert len(info['completed']) == 5 and len(info['pending']) == 10
    assert info['unresolved'] == []
    second = {**payload, 'pages': deepcopy(payload['pages'][5:10])}
    finish(second, [f'Corrected page {i}.' for i in range(6, 11)])
    info = run(sid, path.with_name('Real_Scenario_02.md'), second)
    assert len(info['completed']) == 10 and len(info['pending']) == 5
    assert info['unresolved'] == [] and not info['published_id']
    draft = json.loads((source._root(sid, payload['export_id']) / 'draft.json').read_text())
    for row in first['pages']:
        assert draft['pages'][row['page_id']] == row


def test_clean_removes_only_owned_preparation_and_allows_same_id_recreation(prepared):
    sid, workbook, path, payload = prepared(('Armor 2.',))
    finish(payload, ['Armor 2.'])
    published = run(sid, path, payload)['published_id']
    second = source.export_source(sid)
    standalone = templates.IMPORT_DIR / 'returned.md'
    write(standalone, payload)
    other_sid, other_workbook, _, _ = prepared(('Unrelated original.',))
    unrelated = templates.IMPORT_DIR / 'unrelated.md'
    unrelated.write_text('Unrelated notes')
    owned_exports = [workbook.parent.parent, second.parent.parent]
    pdf = (library._path(sid) / 'source.pdf').read_bytes()
    text = (library._path(sid) / 'scenario.txt').read_text()
    library.clean_scenario(sid)
    templates.clean_scenario(sid)
    assert not library._path(sid).exists()
    assert not (library.SCENARIO_LIBRARY_DIR / '.source-authoring' / sid).exists()
    assert all(not path.exists() for path in owned_exports)
    assert not standalone.exists()
    assert unrelated.read_text() == 'Unrelated notes'
    assert other_workbook.exists() and library._path(other_sid).exists()
    assert library._path(published).exists()  # A separate published scenario.
    library.save_scenario(pdf, title='Recreated', filename='source.pdf', preview='test', text=text,
                          indexes={}, pregens=[], page_maps={}, page_images={}, scenario_id=sid)
    assert source.status(sid) == []


def test_clean_validates_ownership_before_deleting_anything(prepared):
    sid, workbook, _, payload = prepared(('Armor 2.',))
    registry_path = source._root(sid, payload['export_id']) / 'registry.json'
    registry = json.loads(registry_path.read_text())
    registry['scenario_id'] = 'another-scenario'
    authoring.atomic_json(registry_path, registry)
    with pytest.raises(ValueError, match='ownership'):
        library.clean_scenario(sid)
    assert workbook.exists() and library._path(sid).exists()


def test_clean_does_not_follow_import_directory_symlink(prepared, tmp_path):
    import shutil
    sid, workbook, _, _ = prepared(('Armor 2.',))
    output = workbook.parent.parent
    shutil.rmtree(output)
    outside = tmp_path / 'unrelated'
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep')
    output.symlink_to(outside, target_is_directory=True)
    library.clean_scenario(sid)
    assert not output.is_symlink()
    assert (outside / 'keep.txt').read_text() == 'keep'


@pytest.mark.parametrize('asset,field', [
    ('manifest', 'chapters'), ('manifest', 'title'), ('manifest', 'created_at'),
    ('manifest', 'source_review'), ('source_review', 'imported_by'),
    ('source_review', 'imported_at'), ('source_review', 'changes'),
    ('source_review', 'revision_receipts'), ('source_review', 'image_sha256'),
])
def test_published_retry_rejects_all_modified_metadata(prepared, asset, field):
    sid, _, path, payload = prepared(('Armor 2.',))
    finish(payload, ['Armor 2.'])
    target = run(sid, path, payload)['published_id']
    file = library._path(target) / (asset + '.json')
    metadata = json.loads(file.read_text())
    if field == 'chapters':
        metadata[field][0]['start_page'] += 1
    else:
        metadata[field] = 'modified'
    authoring.atomic_json(file, metadata)
    with pytest.raises(ValueError, match='immutable metadata changed'):
        run(sid, path, payload)
    assert len(library.list_scenarios()) == 2


def test_published_retry_without_receipt_fails_closed(prepared):
    sid, _, path, payload = prepared(('Armor 2.',))
    finish(payload, ['Armor 2.'])
    target = run(sid, path, payload)['published_id']
    (source._root(sid, payload['export_id']) / 'publication.json').unlink()
    with pytest.raises(ValueError, match='metadata receipt missing'):
        run(sid, path, payload)
    assert library.load_context(target)['text']  # Still usable for gameplay.


def test_publication_receipt_failure_does_not_publish(prepared, monkeypatch):
    sid, _, path, payload = prepared(('Armor 2.',))
    finish(payload, ['Armor 2.'])
    atomic = authoring.atomic_json
    def fail(path, value):
        if path.name == 'publication.json':
            raise OSError('seal failed')
        atomic(path, value)
    monkeypatch.setattr(authoring, 'atomic_json', fail)
    with pytest.raises(OSError, match='seal failed'):
        run(sid, path, payload)
    assert len(library.list_scenarios()) == 1
    monkeypatch.setattr(authoring, 'atomic_json', atomic)
    assert run(sid, path, payload)['published_id']


def test_crash_after_seal_before_rename_reuses_metadata(prepared, monkeypatch):
    from pathlib import Path
    sid, _, path, payload = prepared(('Armor 2.',))
    finish(payload, ['Armor 2.'])
    rename = Path.rename
    def fail(self, target):
        if self.name.startswith('.source-ai-'):
            raise OSError('rename failed')
        return rename(self, target)
    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(OSError, match='rename failed'):
        run(sid, path, payload)
    receipt = source._root(sid, payload['export_id']) / 'publication.json'
    sealed = receipt.read_bytes()
    assert len(library.list_scenarios()) == 1
    monkeypatch.setattr(Path, 'rename', rename)
    result = run(sid, path, payload)
    assert result['published_id'] and receipt.read_bytes() == sealed
    assert run(sid, path, payload)['published_id'] == result['published_id']


def test_clean_waits_for_inflight_publication_and_preserves_published_child(prepared, monkeypatch):
    import threading
    sid, _, path, payload = prepared(('Armor 2.',))
    finish(payload, ['Armor 2.'])
    entered, release, cleaning = threading.Event(), threading.Event(), threading.Event()
    publish = source._publish
    def held(registry, draft):
        entered.set()
        assert release.wait(5)
        return publish(registry, draft)
    def clean():
        cleaning.set()
        library.clean_scenario(sid)
    monkeypatch.setattr(source, '_publish', held)
    with ThreadPoolExecutor(max_workers=2) as workers:
        publishing = workers.submit(run, sid, path, payload)
        try:
            assert entered.wait(5)
            removing = workers.submit(clean)
            assert cleaning.wait(5)
            assert not removing.done() and library._path(sid).exists()
        finally:
            release.set()
        result = publishing.result(timeout=5)
        removing.result(timeout=5)
    assert library._path(result['published_id']).exists()
    assert not library._path(sid).exists()
    assert not source._root(sid, payload['export_id']).exists()
