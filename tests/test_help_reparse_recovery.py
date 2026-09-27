"""Staged PDFs survive aborted Help reparses without overwriting newer state."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import db, legacy_commands, scenario_library
from app.commands.handlers import system
from app.models import GroupState
from app.repositories.group_state import load_state, save_state


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


def test_guarded_reparse_preserves_source_and_can_retry(staged):
    state, key = staged
    def extract(_, **kwargs):
        current = load_state(state.group_id)
        current.keeper_persona = 'concurrent change'
        save_state(current)
        return 'parsed text', [], False, {}, {}
    context = {'text': 'parsed text', 'indexes': {'npcs': [], 'locations': []}, 'pregens': [], 'scene_maps': {},
               'manifest': {'title': 'parsed title'}, 'active_chapter_id': 'one', 'context_chapter_ids': ['one']}
    with patch.object(legacy_commands.pdf_loader, 'extract_text', side_effect=extract) as extraction, \
         patch.object(legacy_commands.pdf_loader, 'extract_preview', return_value='preview'), \
         patch.object(legacy_commands.scenario_index, 'extract_scenario_index', return_value={'npcs': [], 'locations': []}), \
         patch.object(legacy_commands.pregen_extractor, 'extract_pregens', return_value=[]), \
         patch.object(scenario_library, 'save_scenario', return_value='parsed-id'), \
         patch.object(scenario_library, 'load_context', return_value=context):
        asyncio.run(reparse(state))
        latest = load_state(state.group_id)
        assert latest.pending_scenario_upload['key'] == key
        assert latest.keeper_persona == 'concurrent change'
        assert latest.pending_pdf_upload is None
        assert scenario_library.read_staged_upload(key) == b'original-pdf'
        extraction.side_effect = None
        extraction.return_value = ('parsed text', [], False, {}, {})
        asyncio.run(reparse(state))
    latest = load_state(state.group_id)
    assert latest.pending_scenario_upload is None
    assert latest.pending_pdf_upload['scenario_id'] == 'parsed-id'
    with pytest.raises(FileNotFoundError):
        scenario_library.read_staged_upload(key)


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
