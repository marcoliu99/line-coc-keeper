"""Certified artifacts survive source upgrades and persisted library reload."""
import copy
import hashlib

import pymupdf
import pytest

from app import config, pdf_loader, scenario_library


@pytest.fixture
def published_map(monkeypatch, tmp_path, map_evidence_provider):
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
    sid = scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps=maps, page_images=images, parse_quality=report)
    return raw, text, images, maps, report, sid


@pytest.mark.parametrize('status', ['MAP_NOT_ANALYZED', 'MAP_ANALYSIS_FAILED', 'MAP_GRAPH_INVALID', None])
def test_weaker_map_keeps_published_certificate_on_reload(published_map, status):
    raw, text, images, maps, report, sid = published_map
    candidate = copy.deepcopy(report)
    if status:
        candidate['pages'][0]['map_analysis'] = {'status': status, 'verified': False}
    else:
        candidate['pages'][0].pop('map_analysis')
    scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={}, page_images=images,
        parse_quality=candidate, reparse_candidate_id=sid)
    assert scenario_library.load_context(sid)['scene_maps'] == {'1': maps[1]}


def test_source_page_upgrade_retains_independent_visual_map(published_map, tmp_path):
    raw, _text, images, maps, report, sid = published_map
    # A map can be certified even when source text on its page is quarantined.
    original = copy.deepcopy(report)
    original['pages'][0].update(selected_text='', selected_sha256=hashlib.sha256(b'').hexdigest(),
        source_authority='QUARANTINED', disposition='soft_review', publication_severity='SOFT_REVIEW')
    core = 'The Keeper introduces the scene and follows its instructions.'
    original['pages'].append({'page': 2, 'selected_text': core,
        'selected_sha256': hashlib.sha256(core.encode()).hexdigest(), 'source_authority': 'VERIFIED'})
    old_text = '--- 第 1 頁 ---\n\n--- 第 2 頁 ---\n' + core
    sid = scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=old_text, indexes={}, pregens=[], page_maps=maps, page_images=images,
        parse_quality=original, scenario_id=sid)
    assert scenario_library.load_context(sid)['scene_maps'] == {'1': maps[1]}
    candidate = copy.deepcopy(original)
    upgrade = 'A verified instruction is recovered on this page.'
    candidate['pages'][0].update(selected_text=upgrade, selected_sha256=hashlib.sha256(upgrade.encode()).hexdigest(),
        source_authority='VERIFIED', disposition='accepted', publication_severity='NONE',
        map_analysis={'status': 'MAP_ANALYSIS_FAILED', 'verified': False})
    new_text = '--- 第 1 頁 ---\n' + upgrade + '\n\n--- 第 2 頁 ---\n' + core
    scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=new_text, indexes={}, pregens=[], page_maps={}, page_images=images,
        parse_quality=candidate, reparse_candidate_id=sid)
    context = scenario_library.load_context(sid)
    assert upgrade in context['text']
    assert context['scene_maps'] == {'1': maps[1]}


def test_source_bound_map_invalidation_disables_only_artifact(monkeypatch, tmp_path, certified_map_result):
    import json

    from app import pdf_map_analysis
    from app import pdf_source_topology_discovery as discovery
    from tests.pdf_source_helpers import context as source_fixture
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    raw = b'%PDF-1.4 synthetic'
    text = '--- 第 1 頁 ---\nA hidden passage connects Hall to Vault.\n\n--- 第 2 頁 ---\n\n\n--- 第 3 頁 ---\nThe Keeper describes a quiet evening.'
    report = source_fixture(text).provenance
    report['pdf_sha256'] = hashlib.sha256(raw).hexdigest()
    report['pages'][1]['source_authority'] = 'QUARANTINED'
    visual = certified_map_result('', {'entry_room_id': 'hall', 'rooms': [
        {'id': 'hall', 'name': 'Hall', 'exits': []},
        {'id': 'vault', 'name': 'Vault', 'exits': []}]}, image=b'map')
    result = pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis, text,
        source_context=discovery.source_context(text, report, pdf_sha256=report['pdf_sha256']))
    assert result.graph.get('source_topology')
    report['pages'][0]['map_analysis'] = result.analysis
    sid = scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={1: result.graph}, page_images={1: b'map'}, parse_quality=report)
    assert scenario_library.load_context(sid)['scene_maps']
    candidate = copy.deepcopy(report)
    upgrade = 'The Keeper describes a recovered instruction.'
    candidate['pages'][1].update(selected_text=upgrade, selected_sha256=hashlib.sha256(upgrade.encode()).hexdigest(),
                                source_authority='VERIFIED')
    candidate['pages'][0]['map_analysis'] = {'status': 'MAP_NOT_ANALYZED', 'verified': False}
    scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text.replace('--- 第 2 頁 ---\n\n\n', '--- 第 2 頁 ---\n' + upgrade + '\n\n'), indexes={}, pregens=[], page_maps={}, page_images={},
        parse_quality=candidate, reparse_candidate_id=sid)
    loaded = scenario_library.load_context(sid)
    assert upgrade in loaded['text'] and loaded['scene_maps'] == {}
    from app import scenario_activation
    from app.models import GroupState
    state = GroupState(group_id='source-incompatible-map', active=True, game_started=True)
    state.scene_maps = {'1': result.graph}
    state.current_map_page = {'player': '1'}
    state.current_room_id = {'player': result.graph['entry_room_id']}
    state.party_facing = {'player': 'E'}
    state.timeline_id = 'ongoing-timeline'
    scenario_activation.install_context_fields(state, sid, loaded, preserve_maps=True)
    assert state.scene_maps == {} and state.current_map_page == {} and state.current_room_id == {}
    assert state.party_facing == {} and state.game_started and state.timeline_id == 'ongoing-timeline'

    saved = json.loads((tmp_path / sid / 'parse_quality.json').read_text())
    assert saved['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert saved['reparse_diff']['artifact_conflicts'][0]['reason'] == 'source_binding_changed'
    assert saved['reparse_diff']['downgrades_applied'] == 0


def test_retained_map_proof_and_active_position_survive_reload(published_map, tmp_path):
    import json

    from app import scenario_activation
    from app.models import GroupState
    raw, text, images, maps, report, sid = published_map
    state = GroupState(group_id='map-retention', active=True, game_started=True)
    state.timeline_id = 'unchanged-timeline'
    state.current_map_page = {'player': '1'}
    state.current_room_id = {'player': maps[1]['entry_room_id']}
    state.party_facing = {'player': 'E'}
    state.pending_checks = {'player': {'type': 'skill'}}
    expected = copy.deepcopy((state.current_map_page, state.current_room_id, state.party_facing, state.pending_checks))
    candidate = copy.deepcopy(report)
    candidate['pages'][0]['map_analysis'] = {'status': 'MAP_ANALYSIS_FAILED', 'verified': False}
    scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={}, page_images={},
        parse_quality=candidate, reparse_candidate_id=sid)
    scenario_activation.install_context_fields(state, sid, scenario_library.load_context(sid))
    assert state.scene_maps == {'1': maps[1]}
    assert (state.current_map_page, state.current_room_id, state.party_facing, state.pending_checks) == expected
    assert state.game_started and state.timeline_id == 'unchanged-timeline'
    private = json.loads((tmp_path / sid / '.ingestion-provenance.json').read_text())
    assert private['pages'][0]['map_analysis'] == report['pages'][0]['map_analysis']
    assert (tmp_path / sid / 'images' / 'page_1.png').read_bytes() == images[1]
    assert (tmp_path / sid / 'source.pdf').read_bytes() == raw


def test_invalid_candidate_graph_does_not_replace_verified_map(published_map):
    raw, text, images, maps, report, sid = published_map
    invalid = copy.deepcopy(maps[1])
    invalid['entry_room_id'] = 'absent'
    scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={1: invalid}, page_images=images,
        parse_quality=report, reparse_candidate_id=sid)
    assert scenario_library.load_context(sid)['scene_maps'] == {'1': maps[1]}


def test_verified_conflicting_map_keeps_published_artifact(published_map, certified_map_result, tmp_path):
    import json
    raw, text, images, maps, report, sid = published_map
    other = certified_map_result('', {'entry_room_id': 'entrance', 'rooms': [
        {'id': 'entrance', 'name': 'Entrance', 'exits': []},
        {'id': 'store', 'name': 'Store', 'exits': []}]}, image=images[1])
    candidate = copy.deepcopy(report)
    candidate['pages'][0]['map_analysis'] = other.analysis
    scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps={1: other.graph}, page_images=images,
        parse_quality=candidate, reparse_candidate_id=sid)
    assert scenario_library.load_context(sid)['scene_maps'] == {'1': maps[1]}
    public = json.loads((tmp_path / sid / 'parse_quality.json').read_text())
    assert public['reparse_diff']['artifact_conflicts'][0]['selection'] == 'published_verified'
    assert public['reparse_diff']['downgrades_applied'] == 0


def test_failed_map_transaction_preserves_published_revision(published_map, tmp_path, monkeypatch):
    raw, text, images, _maps, report, sid = published_map
    context = scenario_library.load_context(sid)
    monkeypatch.setattr(scenario_library, 'build_chapters', lambda *_: (_ for _ in ()).throw(ValueError('staging failed')))
    with pytest.raises(ValueError, match='staging failed'):
        scenario_library.save_scenario(raw, title='Synthetic', filename='x.pdf', preview='',
            text=text, indexes={}, pregens=[], page_maps={}, page_images=images,
            parse_quality=report, reparse_candidate_id=sid)
    assert scenario_library.load_context(sid) == context
    assert (tmp_path / sid / 'source.pdf').read_bytes() == raw
