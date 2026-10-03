"""Reject stale PDF installs before mutating published library authority."""
import asyncio

import pymupdf
import pytest

from app import db, legacy_commands, pdf_ingestion_drafts, pdf_loader, scenario_library
from app.repositories import group_state


@pytest.mark.parametrize('race', ['revision', 'pending_choice'])
def test_final_publication_guard_keeps_existing_library_unchanged(monkeypatch, tmp_path, race):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    monkeypatch.setattr(group_state, 'DATA_DIR', tmp_path / 'groups')
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(pdf_ingestion_drafts, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    db._ensure_tables()
    with pymupdf.open() as document:
        document.new_page().insert_text((30, 50), 'Synthetic scenario')
        raw = document.tobytes()
    text = '--- 第 1 頁 ---\nThe Keeper describes the scene. ' * 30
    sid = scenario_library.save_scenario(raw, title='Synthetic', filename='test.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={}, page_images={})
    published = scenario_library.scenario_path(sid)
    before = {str(path.relative_to(published)): path.read_bytes() for path in published.rglob('*') if path.is_file()}
    state = group_state.load_state('publication-race')
    state.scenario_library_id = sid
    state.scenario_text = text
    group_state.save_state(state)
    expected_revision = group_state.load_state(state.group_id).state_revision

    def extract(_pdf, *, quality_report, **_kwargs):
        quality_report.update(pages=[], scenario_readiness='READY')
        return text, [], False, {}, {}

    def concurrent_update(_text):
        current = group_state.load_state(state.group_id)
        if race == 'pending_choice':
            current.pending_pdf_upload = {'title': 'Other upload', 'scenario_id': sid}
        current.game_started = True
        group_state.save_state(current)
        return {'npcs': [], 'locations': []}

    monkeypatch.setattr(pdf_loader, 'extract_text', extract)
    monkeypatch.setattr(legacy_commands.scenario_index, 'extract_scenario_index', concurrent_update)
    monkeypatch.setattr(legacy_commands.pregen_extractor, 'extract_pregens', lambda *_: [])
    messages = []

    async def reply(message):
        messages.append(str(message))

    result = asyncio.run(legacy_commands.handle_pdf_upload(state.group_id, reply, reply, raw, 'test.pdf',
        skip_similarity=True, reparse_candidate_id=sid,
        expected_revision=expected_revision if race == 'revision' else None))
    assert result is False
    assert {str(path.relative_to(published)): path.read_bytes() for path in published.rglob('*') if path.is_file()} == before
    current = group_state.load_state(state.group_id)
    assert current.game_started is True
    assert current.scenario_text == text
    assert any('狀態已更新' in message or '等待處理' in message for message in messages)
    if race == 'pending_choice':
        assert current.pending_pdf_upload['title'] == 'Other upload'
