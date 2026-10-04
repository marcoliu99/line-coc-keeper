"""Staged PDFs survive aborted Help reparses without overwriting newer state."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import db, locks, scenario_library
from app.commands.handlers import system
from app.models import GroupState
from app.repositories.group_state import load_state, save_state
from app.services import scenario_ingestion


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    db._ensure_tables()
    key = scenario_library.stage_upload(b'original-pdf')
    state = GroupState(group_id='reparse-review', timeline_id='original-timeline',
                       kp_assistant_user_id='kp', scenario_text='existing scenario')
    state.pending_scenario_upload = {'key': key, 'file_name': 'source.pdf', 'matches': []}
    save_state(state)
    return state, key


def reparse(state):
    latest = load_state(state.group_id)
    return system.handle_system_command(state.group_id, 'kp', AsyncMock(), AsyncMock(),
        AsyncMock(), AsyncMock(), ['/coc', 'scenario', 'reparse'],
        expected_revision=latest.state_revision)


def test_guarded_reparse_preserves_concurrent_write_without_restoring_stale_pending(staged):
    state, key = staged
    def extract(_, **kwargs):
        current = load_state(state.group_id)
        current.keeper_persona = 'concurrent change'
        save_state(current)
        return 'parsed text', [], False, {}, {}
    context = {'text': 'parsed text', 'indexes': {'npcs': [], 'locations': []}, 'pregens': [], 'scene_maps': {},
               'manifest': {'title': 'parsed title'}, 'active_chapter_id': 'one', 'context_chapter_ids': ['one']}
    with patch.object(scenario_ingestion.pdf_loader, 'extract_text', side_effect=extract), \
         patch.object(scenario_ingestion.pdf_loader, 'extract_preview', return_value='preview'), \
         patch.object(scenario_ingestion.scenario_index, 'extract_scenario_index', return_value={'npcs': [], 'locations': []}), \
         patch.object(scenario_ingestion.pregen_extractor, 'extract_pregens', return_value=[]), \
         patch.object(scenario_library, 'save_scenario', return_value='parsed-id'), \
         patch.object(scenario_library, 'load_context', return_value=context):
        asyncio.run(reparse(state))
        latest = load_state(state.group_id)
        assert latest.pending_scenario_upload is None
        assert latest.keeper_persona == 'concurrent change'
        assert latest.pending_pdf_upload is None
        assert scenario_library.read_staged_upload(key) == b'original-pdf'


@pytest.mark.parametrize('failure', [RuntimeError('extraction failed'), asyncio.CancelledError()])
def test_reparse_exception_restores_pending(staged, failure):
    state, key = staged
    with patch.object(system, 'handle_pdf_upload', AsyncMock(side_effect=failure)), pytest.raises(type(failure)):
        asyncio.run(reparse(state))
    assert load_state(state.group_id).pending_scenario_upload['key'] == key
    assert scenario_library.read_staged_upload(key) == b'original-pdf'


@pytest.mark.parametrize('replacement', ['new_upload', 'new_timeline'])
def test_failed_reparse_never_overwrites_newer_pending_or_timeline(staged, replacement):
    state, key = staged
    async def abort(*args, **kwargs):
        current = load_state(state.group_id)
        if replacement == 'new_upload':
            current.pending_scenario_upload = {'key': 'new-key', 'file_name': 'new.pdf'}
        else:
            current.timeline_id = 'new-timeline'
        save_state(current)
        return False
    with patch.object(system, 'handle_pdf_upload', AsyncMock(side_effect=abort)):
        asyncio.run(reparse(state))
    latest = load_state(state.group_id)
    if replacement == 'new_upload':
        assert latest.pending_scenario_upload['key'] == 'new-key'
    else:
        assert latest.pending_scenario_upload is None
        assert latest.timeline_id == 'new-timeline'
    assert scenario_library.read_staged_upload(key) == b'original-pdf'


def test_reparse_without_help_revision_cannot_apply_to_a_new_timeline(staged):
    state, key = staged

    def extract(_bytes, **_kwargs):
        current = load_state(state.group_id)
        current.timeline_id = 'replacement-timeline'
        current.scenario_text = 'replacement scenario'
        save_state(current)
        return '--- 第 1 頁 ---\nobsolete parse', [], False, {}, {}

    context = {
        'text': 'obsolete parse', 'indexes': {'npcs': [], 'locations': []},
        'pregens': [], 'scene_maps': {}, 'manifest': {'title': 'obsolete'},
        'active_chapter_id': 'one', 'context_chapter_ids': ['one'],
    }
    with patch.object(scenario_ingestion.pdf_loader, 'extract_text', side_effect=extract), \
         patch.object(scenario_ingestion.pdf_loader, 'extract_preview', return_value='preview'), \
         patch.object(scenario_ingestion.pdf_loader, 'guess_title', return_value='obsolete'), \
         patch.object(scenario_ingestion.scenario_index, 'extract_scenario_index', return_value={'npcs': [], 'locations': []}), \
         patch.object(scenario_ingestion.pregen_extractor, 'extract_pregens', return_value=[]), \
         patch.object(scenario_library, 'save_scenario', return_value='obsolete-id'), \
         patch.object(scenario_library, 'load_context', return_value=context):
        asyncio.run(system.handle_system_command(
            state.group_id, 'kp', AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(),
            ['/coc', 'scenario', 'reparse'],
        ))

    latest = load_state(state.group_id)
    assert latest.timeline_id == 'replacement-timeline'
    assert latest.scenario_text == 'replacement scenario'
    assert latest.pending_pdf_upload is None
    assert latest.pending_scenario_upload is None
    assert scenario_library.read_staged_upload(key) == b'original-pdf'


def test_newer_pending_candidate_prevents_restore_during_unlocked_parse(staged):
    state, key = staged

    async def concurrent_candidate(*_args, **_kwargs):
        async with locks.get_conversation_lock(state.group_id):
            latest = load_state(state.group_id)
            latest.pending_scenario_upload = {'key': 'new-key', 'file_name': 'new.pdf'}
            save_state(latest)
        return False

    with patch.object(system, 'handle_pdf_upload', AsyncMock(side_effect=concurrent_candidate)):
        asyncio.run(asyncio.wait_for(reparse(state), timeout=2))

    latest = load_state(state.group_id)
    assert latest.pending_scenario_upload == {'key': 'new-key', 'file_name': 'new.pdf'}
    assert latest.pending_pdf_upload is None
    assert scenario_library.read_staged_upload(key) == b'original-pdf'


def test_old_reparse_result_cannot_apply_over_newer_pending_candidate(staged):
    state, key = staged

    def extract(_bytes, **_kwargs):
        latest = load_state(state.group_id)
        latest.pending_scenario_upload = {'key': 'new-key', 'file_name': 'new.pdf'}
        save_state(latest)
        return '--- 第 1 頁 ---\nobsolete parse', [], False, {}, {}

    context = {
        'text': 'obsolete parse', 'indexes': {'npcs': [], 'locations': []},
        'pregens': [], 'scene_maps': {}, 'manifest': {'title': 'obsolete'},
        'active_chapter_id': 'one', 'context_chapter_ids': ['one'],
    }
    with patch.object(scenario_ingestion.pdf_loader, 'extract_text', side_effect=extract), \
         patch.object(scenario_ingestion.pdf_loader, 'extract_preview', return_value='preview'), \
         patch.object(scenario_ingestion.pdf_loader, 'guess_title', return_value='obsolete'), \
         patch.object(scenario_ingestion.scenario_index, 'extract_scenario_index', return_value={'npcs': [], 'locations': []}), \
         patch.object(scenario_ingestion.pregen_extractor, 'extract_pregens', return_value=[]), \
         patch.object(scenario_library, 'save_scenario', return_value='obsolete-id'), \
         patch.object(scenario_library, 'load_context', return_value=context):
        asyncio.run(system.handle_system_command(
            state.group_id, 'kp', AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(),
            ['/coc', 'scenario', 'reparse'],
        ))

    latest = load_state(state.group_id)
    assert latest.pending_scenario_upload == {'key': 'new-key', 'file_name': 'new.pdf'}
    assert latest.pending_pdf_upload is None
    assert latest.scenario_text == 'existing scenario'
    assert scenario_library.read_staged_upload(key) == b'original-pdf'


def test_reparse_without_help_revision_rejects_concurrent_same_timeline_write(staged):
    state, key = staged

    def extract(_bytes, **_kwargs):
        latest = load_state(state.group_id)
        latest.keeper_persona = 'new concurrent command'
        save_state(latest)
        return '--- 第 1 頁 ---\nobsolete parse', [], False, {}, {}

    context = {
        'text': 'obsolete parse', 'indexes': {'npcs': [], 'locations': []},
        'pregens': [], 'scene_maps': {}, 'manifest': {'title': 'obsolete'},
        'active_chapter_id': 'one', 'context_chapter_ids': ['one'],
    }
    with patch.object(scenario_ingestion.pdf_loader, 'extract_text', side_effect=extract), \
         patch.object(scenario_ingestion.pdf_loader, 'extract_preview', return_value='preview'), \
         patch.object(scenario_ingestion.pdf_loader, 'guess_title', return_value='obsolete'), \
         patch.object(scenario_ingestion.scenario_index, 'extract_scenario_index', return_value={'npcs': [], 'locations': []}), \
         patch.object(scenario_ingestion.pregen_extractor, 'extract_pregens', return_value=[]), \
         patch.object(scenario_library, 'save_scenario', return_value='obsolete-id'), \
         patch.object(scenario_library, 'load_context', return_value=context):
        asyncio.run(system.handle_system_command(
            state.group_id, 'kp', AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(),
            ['/coc', 'scenario', 'reparse'],
        ))

    latest = load_state(state.group_id)
    assert latest.keeper_persona == 'new concurrent command'
    assert latest.pending_pdf_upload is None
    assert latest.pending_scenario_upload is None
    assert scenario_library.read_staged_upload(key) == b'original-pdf'


def test_failed_reparse_does_not_resurrect_claim_after_newer_revision(staged):
    state, key = staged

    async def fail_after_write(*_args, **_kwargs):
        current = load_state(state.group_id)
        current.keeper_persona = 'newer command'
        save_state(current)
        raise asyncio.CancelledError()

    with patch.object(system, 'handle_pdf_upload', AsyncMock(side_effect=fail_after_write)), \
         pytest.raises(asyncio.CancelledError):
        asyncio.run(reparse(state))

    latest = load_state(state.group_id)
    assert latest.keeper_persona == 'newer command'
    assert latest.pending_scenario_upload is None
    assert scenario_library.read_staged_upload(key) == b'original-pdf'
