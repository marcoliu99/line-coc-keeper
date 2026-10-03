"""Library reparse retains published authority; explicit source review is separate."""
import json

import pytest

from app import scenario_library


def save(text, **kwargs):
    return scenario_library.save_scenario(b'%PDF-1.4 fake', title='Synthetic', filename='test.pdf',
        preview='Synthetic', text=text, indexes={'npcs': [], 'locations': []}, pregens=[],
        page_maps={}, page_images={}, **kwargs)


def test_reparse_conflict_retains_published_legacy_authority(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    old = '--- 第 1 頁 ---\n' + 'The Keeper describes the room. ' * 30 + 'Damage 1d6.'
    scenario_id = save(old)
    import hashlib
    candidate = old.replace('1d6', '1d8')
    assert save(candidate, reparse_candidate_id=scenario_id, parse_quality={'pages': [{'page': 1,
        'source_authority': 'VERIFIED', 'selected_sha256': hashlib.sha256(candidate.split('\n', 1)[1].encode()).hexdigest()}]}) == scenario_id
    assert scenario_library.load_context(scenario_id)['text'].strip() == old.strip()
    report = json.loads((tmp_path / scenario_id / 'parse_quality.json').read_text())
    assert report['reparse_diff']['conflicts'] == 1
    assert report['reparse_diff']['downgrades_applied'] == 0


def test_unrelated_reparse_cannot_replace_existing(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    old = '--- 第 1 頁 ---\n' + 'The Keeper describes the room. ' * 30
    scenario_id = save(old)
    new_id = save('An entirely unrelated expedition travels across the sea. ' * 30,
                  reparse_candidate_id=scenario_id)
    assert new_id != scenario_id
    assert scenario_library.load_context(scenario_id)['text'].strip() == old.strip()


def test_failed_reparse_leaves_published_version_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    old = '--- 第 1 頁 ---\n' + 'The Keeper describes the room. ' * 30
    scenario_id = save(old)
    monkeypatch.setattr(scenario_library, 'build_chapters', lambda *_: (_ for _ in ()).throw(ValueError('invalid staging')))
    with pytest.raises(ValueError, match='invalid staging'):
        save(old, reparse_candidate_id=scenario_id)
    assert (tmp_path / scenario_id / 'scenario.txt').read_text() == old


def test_unknown_new_keeps_verified_and_upgrades_old_quarantine(monkeypatch, tmp_path):
    import hashlib
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    old = '--- 第 1 頁 ---\nThe Keeper describes the room.\n\n--- 第 2 頁 ---\n'
    old_report = {'scenario_readiness': 'READY_WITH_WARNINGS', 'quarantined_pages': [2], 'pages': [
        {'page': 1, 'source_authority': 'VERIFIED'}, {'page': 2, 'source_authority': 'QUARANTINED'}]}
    scenario_id = save(old, parse_quality=old_report)
    # Keep sufficient shared text for identity matching; first page is weaker.
    new = '--- 第 1 頁 ---\nThe Keeper describes the room.\n\n--- 第 2 頁 ---\nVerified clue.'
    new_report = {'pages': [{'page': 1, 'source_authority': 'QUARANTINED'},
        {'page': 2, 'source_authority': 'VERIFIED', 'selected_sha256': hashlib.sha256(b'Verified clue.').hexdigest()}]}
    assert save(new, parse_quality=new_report, reparse_candidate_id=scenario_id) == scenario_id
    context = scenario_library.load_context(scenario_id)
    assert 'The Keeper describes' in context['text']
    assert 'Verified clue.' in context['text']
    diff = json.loads((tmp_path / scenario_id / 'parse_quality.json').read_text())['reparse_diff']
    assert diff['retained_verified'] == 1 and diff['upgraded'] == 1
    assert diff['downgrades_applied'] == 0


def test_region_upgrade_does_not_overwrite_verified_sibling():
    import hashlib

    from app import scenario_reparse
    def region(identifier, text, verified):
        return {'id': identifier, 'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                'authority': 'VERIFIED' if verified else 'UNKNOWN'}
    old_text = '--- 第 1 頁 ---\nDamage 1d6.'
    new_text = '--- 第 1 頁 ---\nDamage 1d6.\n\nVerified instruction.'
    old = {'pdf_sha256': 'same-source', 'pages': [{'page': 1,
        'source_regions': [region('body', 'Damage 1d6.', True), region('inset', '', False)],
        'ordered_region_ids': ['body', 'inset']}]}
    new = {'pdf_sha256': 'same-source', 'pages': [{'page': 1, 'source_authority': 'VERIFIED',
        'selected_sha256': hashlib.sha256(new_text.split('\n', 1)[1].encode()).hexdigest(),
        'source_regions': [region('body', 'Damage 1d6.', True), region('inset', 'Verified instruction.', True)],
        'ordered_region_ids': ['body', 'inset']}]}
    result = scenario_reparse.merge(old_text, old, new_text, new)
    assert 'Verified instruction.' in result.text
    assert result.report['reparse_diff']['conflicts'] == 0
    assert result.report['reparse_diff']['upgraded_regions'] == 1
    assert result.report['reparse_diff']['downgrades_applied'] == 0
    new['pages'][0]['source_regions'][0] = region('body', 'Damage 1d8.', True)
    new['pages'][0]['selected_sha256'] = hashlib.sha256(new_text.split('\n', 1)[1].replace('1d6', '1d8').encode()).hexdigest()
    result = scenario_reparse.merge(old_text, old, new_text.replace('1d6', '1d8'), new)
    assert result.text == old_text
    assert result.report['reparse_diff']['conflicts'] == 1


def test_quarantine_has_no_runtime_image_or_public_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    report = {'quarantined_pages': [2], 'pages': [{'page': 2, 'source_authority': 'QUARANTINED',
        'quarantine_evidence': {'selected_text': 'private-marker'}, 'candidates': {'vision': 'private-marker'},
        'evidence': {'words': ['private-marker'], 'blocks': [{'text': 'private-marker'}]},
        'layout': {'selected_text': 'private-marker'}, 'image_transcription': {'text': 'private-marker'},
        'repair': {'source_fragments': ['private-marker']}}]}
    sid = scenario_library.save_scenario(b'%PDF fake', title='Synthetic', filename='x.pdf', preview='',
        text='--- 第 1 頁 ---\nThe Keeper runs the scenario.\n\n--- 第 2 頁 ---\n',
        indexes={}, pregens=[], page_maps={}, page_images={2: b'private-image'}, parse_quality=report)
    context = scenario_library.load_context(sid)
    assert 2 not in context['page_numbers']
    assert context['manifest']['image_assets'] == []
    assert 'private-marker' not in (tmp_path / sid / 'parse_quality.json').read_text()
    assert 'private-marker' in (tmp_path / sid / '.ingestion-provenance.json').read_text()


def test_reparse_adds_certified_map_without_replacing_old_source(monkeypatch, tmp_path, map_evidence_provider):
    import pymupdf

    from app import config, pdf_loader
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    map_evidence_provider()
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox((30, 30, 550, 700), 'FLOOR PLAN\n' + 'Original playable source. ' * 16)
        for index in range(8):
            page.draw_rect((40 + index * 10, 710, 45 + index * 10, 720))
        raw = doc.tobytes()
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(raw, quality_report=report)
    assert maps
    old_report = json.loads(json.dumps(report))
    old_report['pages'][0].pop('map_analysis')
    sid = scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={}, page_images=images, parse_quality=old_report)
    # The new source is uncertain; independently certified visual artifact may improve.
    report['pages'][0]['source_authority'] = 'QUARANTINED'
    scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps=maps, page_images=images, parse_quality=report,
        reparse_candidate_id=sid)
    context = scenario_library.load_context(sid)
    assert context['scene_maps'] == {'1': maps[1]}
    assert (tmp_path / sid / 'scenario.txt').read_text() == text


def test_verified_pregen_added_without_replacing_existing_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    text = '--- 第 1 頁 ---\nSynthetic Investigator STR 60 CON 50 SIZ 55 DEX 65. '
    text += 'The Keeper describes the room. ' * 30
    sid = save(text)
    card = {'name': 'Synthetic Investigator', 'str_': 60, 'con': 50, 'siz': 55, 'dex': 65,
            'skills': {}}
    scenario_library.save_scenario(b'%PDF-1.4 fake', title='Synthetic', filename='test.pdf',
        preview='Synthetic', text=text, indexes={}, pregens=[card], page_maps={}, page_images={},
        reparse_candidate_id=sid)
    assert scenario_library.load_context(sid)['pregens'][0]['str_'] == 60
    card['str_'] = 80
    scenario_library.save_scenario(b'%PDF-1.4 fake', title='Synthetic', filename='test.pdf',
        preview='Synthetic', text=text, indexes={}, pregens=[card], page_maps={}, page_images={},
        reparse_candidate_id=sid)
    assert scenario_library.load_context(sid)['pregens'][0]['str_'] == 60


@pytest.mark.parametrize('outcome', ['conflict', 'improved', 'no_improvement'])
def test_reparse_command_preserves_live_game_and_reports_outcome(monkeypatch, tmp_path, outcome):
    import asyncio
    from types import SimpleNamespace

    import pymupdf

    from app import config, db, pdf_ingestion_drafts, pdf_loader
    from app.commands.router import handle_text_message
    from app.models import Character
    from app.providers import registry
    from app.repositories import group_state
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    db._ensure_tables()
    monkeypatch.setattr(group_state, 'DATA_DIR', tmp_path / 'groups')
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(pdf_ingestion_drafts, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_a, **_k: None,
                                        analyze_text=lambda *_a, **_k: None))
    with pymupdf.open() as doc:
        doc.new_page().insert_textbox((30, 80, 550, 700), 'The Keeper describes the room. ' * 30 + 'Damage 1d8.')
        if outcome == 'improved':
            doc.new_page().insert_textbox((30, 80, 550, 700), 'The Keeper describes another room. ' * 5)
        raw = doc.tobytes()
    published = '--- 第 1 頁 ---\n' + 'The Keeper describes the room. ' * 30 + ('Damage 1d6.' if outcome == 'conflict' else 'Damage 1d8.')
    report = None
    if outcome == 'improved':
        published += '\n\n--- 第 2 頁 ---\n'
        report = {'pages': [{'page': 2, 'source_authority': 'QUARANTINED'}]}
    sid = scenario_library.save_scenario(raw, title='Synthetic', filename='test.pdf', preview='',
        text=published, indexes={}, pregens=[], page_maps={}, page_images={}, parse_quality=report)
    state = group_state.load_state('reparse-live')
    state.kp_assistant_user_id = 'keeper'
    state.scenario_library_id = sid
    state.scenario_text = published
    state.game_started = state.active = True
    state.characters['player'] = Character(name='Synthetic', owner_id='player', hp=7, san=42, mp=6)
    state.pending_checks = {'player': {'investigator': 'Synthetic', 'skill': 'Listen', 'value': 50}}
    state.pending_luck_decisions = {'player': {'investigator': 'Synthetic', 'rolled': 51}}
    state.current_map_page = {'player': '1'}
    state.current_room_id = {'player': 'room'}
    group_state.save_state(state)
    before = state.to_dict()
    messages = []
    async def reply(value):
        messages.append(str(value))
    async def display(_user):
        return 'Synthetic Keeper'
    async def sink(*_args):
        pass
    asyncio.run(handle_text_message('reparse-live', 'keeper', display, reply, sink, sink, sink,
                                    '/coc scenario reparse'))
    after = group_state.load_state('reparse-live').to_dict()
    for key in ('game_started', 'timeline_id', 'characters', 'pending_checks', 'pending_luck_decisions',
                'current_map_page', 'current_room_id', 'combat', 'kp_assistant_user_id'):
        if key in before:
            assert after[key] == before[key], key
    if outcome == 'improved':
        assert 'The Keeper describes another room.' in after['scenario_text']
    else:
        assert after['scenario_text'].strip() == published.strip()
    assert after['pending_pdf_upload'] is None
    assert any({'conflict': '衝突', 'improved': '已補強', 'no_improvement': '沒有找到'}[outcome]
               in message for message in messages)


def test_library_rejects_quarantined_text_even_on_unrelated_reparse(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    sid = save('The Keeper describes the room. ' * 30)
    with pytest.raises(ValueError, match='Quarantined content'):
        save('Different unverified source about an expedition. ' * 30, reparse_candidate_id=sid,
             parse_quality={'pages': [{'page': 1, 'source_authority': 'QUARANTINED'}]})


def test_staging_commit_failure_restores_previous_version(monkeypatch, tmp_path):
    from pathlib import Path
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    text = '--- 第 1 頁 ---\n' + 'The Keeper describes the room. ' * 30
    sid = save(text)
    real_replace = Path.replace
    def fail_staged(self, target):
        if self.name.startswith('.' + sid + '-'):
            raise OSError('synthetic commit failure')
        return real_replace(self, target)
    monkeypatch.setattr(Path, 'replace', fail_staged)
    with pytest.raises(OSError, match='synthetic commit failure'):
        save(text, reparse_candidate_id=sid)
    assert scenario_library.load_context(sid)['text'].strip() == text.strip()


def test_identity_comparison_checks_full_content_and_ignores_line_wrap(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    text = '--- 第 1 頁 ---\n' + 'The Keeper describes the room. ' * 30
    sid = save(text)
    assert scenario_library.content_similar(sid, text.replace('room. ', 'room.\n'))
    prefix = 'Shared introductory material. ' * 1000
    sid = save(prefix + 'An expedition crosses the sea. ' * 3000)
    assert not scenario_library.content_similar(sid, prefix + 'Investigators explore the forest. ' * 3000)


def test_repeated_first_import_cannot_erase_published_pregens(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    text = '--- 第 1 頁 ---\n' + 'The Keeper describes the room. ' * 30
    sid = scenario_library.save_scenario(b'%PDF fake', title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[{'name': 'Published Investigator'}], page_maps={}, page_images={})
    scenario_library.save_scenario(b'%PDF fake', title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={}, page_images={})
    assert scenario_library.load_context(sid)['pregens'] == [{'name': 'Published Investigator'}]


@pytest.mark.parametrize('row', [{}, {'source_authority': 'VERIFIED'},
                                {'selected_sha256': 'unbound-digest'}])
def test_new_candidate_without_complete_provenance_cannot_upgrade(row):
    from app import scenario_reparse
    old = '--- 第 1 頁 ---\nThe Keeper describes the room.\n\n--- 第 2 頁 ---\n'
    report = {'pages': [{'page': 1}, {'page': 2, 'source_authority': 'QUARANTINED'}]}
    new = old + 'Unverified candidate instruction.'
    merged = scenario_reparse.merge(old, report, new, {'pages': [{'page': 2, **row}]})
    assert merged.text == old
    assert merged.selected_new_pages == set()
    assert merged.report['reparse_diff']['upgraded'] == 0


def test_card_cannot_bind_another_character_on_same_page():
    from app import scenario_reparse
    text = 'Alice STR40 CON40 SIZ40 DEX40. Bob STR90 CON90 SIZ90 DEX90.'
    card = {'name': 'Alice', 'str_': 90, 'con': 90, 'siz': 90, 'dex': 90}
    assert scenario_reparse.validated_cards([card], text) == []
    assert scenario_reparse.validated_cards([{**card, 'name': 'Ali'}], text) == []


def test_reparse_retains_consumed_candidate_ledger_privately(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    text = '--- 第 1 頁 ---\nThe Keeper describes the room.'
    sid = save(text)
    candidate = {'layout_budget': {'requests_used': 8, 'private_marker': 'private-ledger'}, 'pages': []}
    save(text, reparse_candidate_id=sid, parse_quality=candidate)
    import json
    provenance = json.loads((tmp_path / sid / '.ingestion-provenance.json').read_text())
    assert provenance['reparse_attempt_history'][-1]['layout_budget']['requests_used'] == 8
    assert 'private-ledger' not in (tmp_path / sid / 'parse_quality.json').read_text()


def test_incidental_character_mention_cannot_bind_single_foreign_stat_set():
    from app import scenario_reparse
    text = 'Alice meets Bob.\nBob\nSTR90 CON90 SIZ90 DEX90.'
    card = {'name': 'Alice', 'str_': 90, 'con': 90, 'siz': 90, 'dex': 90}
    assert scenario_reparse.validated_cards([card], text) == []


def test_card_unseen_weapon_mechanics_are_not_adopted():
    from app import scenario_reparse
    text = 'Alice STR40 CON40 SIZ40 DEX40.'
    card = {'name': 'Alice', 'str_': 40, 'con': 40, 'siz': 40, 'dex': 40,
            'weapons': {'pistol': {'ammo': 999, 'ammo_max': 999}}}
    assert scenario_reparse.validated_cards([card], text) == []
