"""Published improvements cannot silently rebind another group's running source."""
import asyncio
import hashlib

import pymupdf
import pytest

from app import (
    db,
    legacy_commands,
    pdf_ingestion_drafts,
    pdf_loader,
    scenario_activation,
    scenario_library,
)
from app.models import GroupState
from app.repositories import group_state


@pytest.fixture
def library(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    monkeypatch.setattr(group_state, 'DATA_DIR', tmp_path / 'groups')
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(pdf_ingestion_drafts, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    db._ensure_tables()
    monkeypatch.setattr(scenario_library, 'build_chapters', lambda *_: [
        {'id': f'chapter-{page}', 'title': f'Section {page}', 'kind': 'playable', 'start_page': page, 'end_page': page}
        for page in range(1, 5)])
    with pymupdf.open() as document:
        for _ in range(4):
            document.new_page().insert_text((30, 50), 'Synthetic scenario')
        pdf = document.tobytes()
    body = '\n\n'.join(f'--- 第 {page} 頁 ---\n' + f'The Keeper describes scene {page}. ' * 20 for page in range(1, 4))
    old = body + '\n\n--- 第 4 頁 ---\n'
    sid = scenario_library.save_scenario(pdf, title='Synthetic', filename='test.pdf', preview='',
        text=old, indexes={}, pregens=[], page_maps={}, page_images={3: b'old image'},
        parse_quality={'quarantined_pages': [4], 'pages': [{'page': 4, 'source_authority': 'QUARANTINED'}]})
    return sid, pdf, old


def test_two_groups_keep_pinned_revision_after_one_explicit_reparse(monkeypatch, library):
    sid, pdf, old = library
    context = scenario_library.load_context(sid, 'chapter-3')
    for group in ('reparse-owner', 'other-game'):
        state = GroupState(group_id=group, game_started=True, timeline_id=f'timeline-{group}')
        scenario_activation.install_context_fields(state, sid, context)
        state.pending_checks = {'player': {'skill': 'Listen', 'value': 50}}
        state.pending_luck_decisions = {'player': {'rolled': 51}}
        group_state.save_state(state)
    before = group_state.load_state('reparse-owner').to_dict()
    other_before = group_state.load_state('other-game').to_dict()
    recovered = 'The Keeper gives a verified final instruction.'
    new = old + recovered

    def extract(_raw, *, quality_report, **_kwargs):
        quality_report.update(quarantined_pages=[], pages=[{'page': 4, 'source_authority': 'VERIFIED',
            'selected_sha256': hashlib.sha256(recovered.encode()).hexdigest()}])
        return new, [], False, {}, {}

    monkeypatch.setattr(pdf_loader, 'extract_text', extract)
    monkeypatch.setattr(legacy_commands.scenario_index, 'extract_scenario_index', lambda *_: {})
    monkeypatch.setattr(legacy_commands.pregen_extractor, 'extract_pregens', lambda *_: [])

    async def reply(_message):
        pass

    assert asyncio.run(legacy_commands.handle_pdf_upload('reparse-owner', reply, reply, pdf, 'test.pdf',
        skip_similarity=True, reparse_candidate_id=sid))
    after = group_state.load_state('reparse-owner').to_dict()
    assert after['active_chapter_id'] == 'chapter-3'
    for key in ('game_started', 'timeline_id', 'pending_checks', 'pending_luck_decisions', 'characters', 'combat'):
        assert after[key] == before[key]
    assert after['scenario_library_revision'] != before['scenario_library_revision']
    assert recovered in after['scenario_text']
    other = group_state.load_state('other-game')
    assert other.to_dict() == other_before
    assert recovered not in scenario_activation.load_state_context(other)['text']
    assert recovered not in scenario_activation.load_state_context(other, 'chapter-4')['text']
    assert recovered in scenario_library.load_context(sid, 'chapter-4')['text']
    assert scenario_library.next_chapter_id(sid, 'chapter-3', revision=other.scenario_library_revision) == 'chapter-4'
    scenario_library.scenario_path(sid).joinpath('images/page_3.png').write_bytes(b'new image')
    assert scenario_activation.refresh_restored_images(other)
    assert group_state.load_page_image(other.group_id, 3) == b'old image'


def test_snapshot_tampering_is_rejected(library):
    sid, _, _ = library
    context = scenario_library.load_context(sid)
    snapshot = scenario_library.revision_path(sid, context['library_revision'])
    (snapshot / 'scenario.txt').write_text('Tampered source')
    with pytest.raises(ValueError, match='changed'):
        scenario_library.load_context(sid, revision=context['library_revision'])


def test_identical_unconfirmed_upload_does_not_mutate_published_entry(monkeypatch, library):
    sid, pdf, old = library
    monkeypatch.setattr(pdf_loader, 'guess_title', lambda *_args, **_kwargs: 'Synthetic')
    state = GroupState(group_id='upload-choice', game_started=True)
    scenario_activation.install_context_fields(state, sid, scenario_library.load_context(sid, 'chapter-3'))
    group_state.save_state(state)
    target = scenario_library.scenario_path(sid)
    before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()}

    def extract(_raw, *, quality_report, **_kwargs):
        quality_report.update(pages=[])
        return old, [], False, {}, {}

    monkeypatch.setattr(pdf_loader, 'extract_text', extract)
    monkeypatch.setattr(legacy_commands.scenario_index, 'extract_scenario_index', lambda *_: {})
    monkeypatch.setattr(legacy_commands.pregen_extractor, 'extract_pregens', lambda *_: [])

    async def reply(_message):
        pass

    assert asyncio.run(legacy_commands.handle_pdf_upload(state.group_id, reply, reply, pdf, 'test.pdf', skip_similarity=True))
    pending = group_state.load_state(state.group_id)
    assert pending.pending_pdf_upload['scenario_id'] != sid
    assert pending.scenario_library_id == sid
    assert pending.active_chapter_id == 'chapter-3'
    assert {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()} == before


def test_artifact_only_improvement_changes_revision(library):
    import json
    sid, _, _ = library
    old = scenario_library.load_context(sid, 'chapter-3')
    indexes = {'npcs': [{'name': 'Synthetic NPC', 'page': 3}], 'locations': []}
    scenario_library.scenario_path(sid).joinpath('indexes.json').write_text(json.dumps(indexes))
    new = scenario_library.load_context(sid, 'chapter-3')
    assert new['text'] == old['text']
    assert new['library_revision'] != old['library_revision']
    assert scenario_library.load_context(sid, 'chapter-3', revision=old['library_revision'])['indexes'] != indexes


def test_legacy_state_can_match_retained_source_revision(library):
    sid, pdf, old = library
    context = scenario_library.load_context(sid, 'chapter-3')
    state = GroupState(group_id='legacy', scenario_library_id=sid, active_chapter_id='chapter-3',
        scenario_text=context['text'])
    recovered = 'The Keeper gives a verified final instruction.'
    scenario_library.save_scenario(pdf, title='Synthetic', filename='test.pdf', preview='',
        text=old + recovered, indexes={}, pregens=[], page_maps={}, page_images={}, reparse_candidate_id=sid,
        parse_quality={'pages': [{'page': 4, 'source_authority': 'VERIFIED',
            'selected_sha256': hashlib.sha256(recovered.encode()).hexdigest()}]})
    assert state.scenario_library_revision == ''
    assert recovered not in scenario_activation.load_state_context(state)['text']
