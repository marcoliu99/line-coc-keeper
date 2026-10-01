"""Image-derived graphs must be certified before they become playable artifacts."""
from types import SimpleNamespace

import pytest

from app import config, pdf_loader, scene_map
from app.providers import registry

GRAPH = {'page_type': 'map', 'description': 'A visible entrance.', 'entry_room_id': 'door',
         'rooms': [{'id': 'door', 'name': 'Entrance', 'exits': []}]}
AUDIT = {'complete': True, 'uncertainties': [],
         'visible_locations': [{'label': 'Entrance', 'room_id': 'door'}],
         'rooms': [{'room_id': 'door', 'verdict': 'supported', 'evidence': 'Entrance shown in image'}],
         'edges': [], 'entry': {'room_id': 'door', 'verdict': 'supported', 'evidence': 'Visible door'}}


@pytest.fixture
def map_pdf(monkeypatch):
    import pymupdf

    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox((30, 30, 550, 700), 'FLOOR PLAN Entrance\n' + 'Visible native source text. ' * 15)
        for n in range(8):
            page.draw_rect((40 + n * 10, 710, 45 + n * 10, 720))
        source = doc.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    return source


def test_valid_graph_requires_image_audit_before_it_is_verified(monkeypatch):
    from app import pdf_map_analysis

    calls = []
    def analyze(_png, tool, _prompt, **_options):
        calls.append(tool['name'])
        return GRAPH if tool['name'] == 'analyze_page_image' else AUDIT
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=analyze))
    result = pdf_map_analysis.analyze(b'original-image', reserve=lambda: True, candidate=True)
    assert result.analysis['status'] == 'MAP_GRAPH_VERIFIED'
    assert result.graph == GRAPH
    assert calls == ['analyze_page_image', 'audit_scene_map_image']
    assert scene_map.validate_scene_map(result.graph) == []


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_invalid_entry_gets_one_image_grounded_repair_and_revalidation(monkeypatch, repair_succeeds):
    from app import pdf_map_analysis

    invalid = {**GRAPH, 'entry_room_id': 'missing'}
    prompts = []
    def analyze(png, tool, prompt, **options):
        assert png == b'original-image'
        assert options['max_retries'] == 0
        if tool['name'] == 'audit_scene_map_image':
            return AUDIT
        prompts.append(prompt)
        return GRAPH if len(prompts) == 2 and repair_succeeds else invalid
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=analyze))
    result = pdf_map_analysis.analyze(b'original-image', reserve=lambda: True, candidate=True)
    assert result.analysis['repair_attempts'] == 1
    assert len(prompts) == 2
    assert 'invalid_entry_room' in prompts[1]
    assert 'missing' in prompts[1]
    attempt = result.analysis['attempts'][1]
    assert attempt['stage'] == 'repair'
    assert attempt['input_graph_sha256'] == pdf_map_analysis.graph_hash(invalid)
    assert attempt['validation_errors'] == ['invalid_entry_room']
    assert attempt['elapsed_seconds'] >= 0
    assert result.analysis['status'] == ('MAP_GRAPH_VERIFIED' if repair_succeeds else 'MAP_GRAPH_INVALID')
    assert result.graph == (GRAPH if repair_succeeds else None)


def test_unsupported_wall_edge_cannot_be_certified_even_if_structure_is_valid(monkeypatch):
    from app import pdf_map_analysis

    graph = {**GRAPH, 'rooms': [{'id': 'door', 'name': 'Entrance', 'exits': [{'to': 'cellar', 'compass': 'E'}]},
                               {'id': 'cellar', 'name': 'Cellar', 'exits': []}]}
    evidence = {**AUDIT, 'visible_locations': [{'label': 'Entrance', 'room_id': 'door'},
                                              {'label': 'Cellar', 'room_id': 'cellar'}],
                'rooms': [{'room_id': 'door', 'verdict': 'supported', 'evidence': 'Visible entrance'},
                          {'room_id': 'cellar', 'verdict': 'supported', 'evidence': 'Visible cellar'}],
                'edges': [{'edge_id': 'door:0', 'verdict': 'unsupported', 'basis': 'wall',
                           'evidence': 'Solid wall with no opening'}]}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda _png, tool, _prompt, **_options: graph if tool['name'] == 'analyze_page_image' else evidence))
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True, candidate=True)
    assert result.analysis['status'] == 'MAP_GRAPH_INVALID'
    assert result.graph is None
    assert result.analysis['repair_attempts'] == 1


def test_missing_image_visible_location_remains_incomplete_after_repair(monkeypatch):
    from app import pdf_map_analysis

    evidence = {**AUDIT, 'visible_locations': [{'label': 'Entrance', 'room_id': 'door'},
                                              {'label': 'Lamp Room', 'room_id': ''}]}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda _png, tool, _prompt, **_options: GRAPH if tool['name'] == 'analyze_page_image' else evidence))
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True, candidate=True)
    assert result.analysis['status'] == 'MAP_GRAPH_INCOMPLETE'
    assert 'missing_visible_location:Lamp Room' in result.analysis['completeness_errors']
    assert result.graph is None


def test_invalid_graph_stays_in_private_draft_and_never_in_loader_maps(map_pdf, monkeypatch, tmp_path):
    from app import pdf_ingestion_drafts as drafts

    invalid = {**GRAPH, 'entry_room_id': 'missing'}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_args, **_options: invalid))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
        pdf_loader.extract_text(map_pdf, quality_report=report)
    assert pending.value.result[4] == {}
    assert report['pages'][0]['map_analysis']['status'] == 'MAP_GRAPH_INVALID'
    assert report['map_graph_generated'] == report['map_graph_invalid'] == 1
    assert report['map_graph_verified'] == 0
    assert report['pages'][0]['map_analysis']['candidate_graph'] == invalid
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    lease = drafts.reserve('map-test', map_pdf, 'map.pdf')
    saved = drafts.checkpoint(lease, report, pending.value.result)
    assert saved['pages']['1']['map'] is None
    assert saved['pages']['1']['report']['map_analysis']['candidate_graph'] == invalid


def test_invalid_graph_cannot_bypass_library_guard_with_missing_quality_report(map_pdf, monkeypatch, tmp_path):
    from app import scenario_library

    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    with pytest.raises(ValueError, match='scene_map'):
        scenario_library.save_scenario(map_pdf, title='Map', filename='map.pdf', preview='',
            text='--- 第 1 頁 ---\nOriginal source', indexes={}, pregens=[], page_images={},
            page_maps={1: {**GRAPH, 'entry_room_id': 'missing'}})
    assert list(tmp_path.iterdir()) == []


def test_certified_graph_persists_without_changing_pdf_source_transcription(map_pdf, monkeypatch, tmp_path):
    from app import scenario_library

    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda _png, tool, _prompt, **_options: GRAPH if tool['name'] == 'analyze_page_image' else AUDIT))
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(map_pdf, quality_report=report)
    assert 'A visible entrance.' not in text
    assert 'Visible native source text.' in text
    assert report['pages'][0]['map_analysis']['verified']
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    scenario = scenario_library.save_scenario(map_pdf, title='Map', filename='map.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=report)
    assert scenario_library.load_context(scenario)['scene_maps'] == {'1': GRAPH}


def test_shared_budget_is_checkpointed_before_every_actual_map_dispatch(map_pdf, monkeypatch):
    from app import pdf_layout_adapters

    snapshots, calls = [], []
    budget = pdf_layout_adapters.new_budget()
    def analyze(_png, tool, _prompt, **_options):
        calls.append(tool['name'])
        assert snapshots[-1]['consumed_requests'] == len(calls)
        return GRAPH if tool['name'] == 'analyze_page_image' else AUDIT
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=analyze))
    report = {}
    pdf_loader.extract_text(map_pdf, quality_report=report, layout_budget=budget,
        layout_budget_checkpoint=lambda value: snapshots.append(dict(value)))
    assert calls == ['analyze_page_image', 'audit_scene_map_image']
    assert report['layout_budget']['consumed_requests'] == 2


def test_exhausted_budget_keeps_map_unanalyzed_without_dispatch(map_pdf, monkeypatch):
    from unittest.mock import Mock

    from app import pdf_layout_adapters

    budget = pdf_layout_adapters.new_budget()
    budget['consumed_requests'] = budget['configured_max_requests']
    call = Mock(return_value=GRAPH)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=call))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(map_pdf, quality_report=report, layout_budget=budget)
    call.assert_not_called()
    assert report['pages'][0]['map_analysis']['status'] == 'MAP_NOT_ANALYZED'
    assert report['map_analysis_attempted'] == 0


def test_verified_boolean_without_image_audit_is_not_a_certificate():
    from app import pdf_map_analysis

    claims = {'version': pdf_map_analysis.VERSION, 'status': 'MAP_GRAPH_VERIFIED', 'verified': True,
              'graph_sha256': pdf_map_analysis.graph_hash(GRAPH)}
    assert not pdf_map_analysis.verified_graph(GRAPH, claims)


def test_modified_cached_graph_is_reanalyzed_instead_of_reused(map_pdf, monkeypatch):
    import copy
    import hashlib

    calls = []
    def analyze(_png, tool, _prompt, **_options):
        calls.append(tool['name'])
        return GRAPH if tool['name'] == 'analyze_page_image' else AUDIT
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=analyze))
    report = {}
    _, _, _, images, maps = pdf_loader.extract_text(map_pdf, quality_report=report)
    row = report['pages'][0]
    graph = copy.deepcopy(maps[1])
    graph['entry_room_id'] = 'missing'
    cached = {1: {'pdf_sha256': hashlib.sha256(map_pdf).hexdigest(),
        'pipeline_version': pdf_loader.PIPELINE_VERSION, 'renderer_version': pdf_loader.RENDERER_VERSION,
        'extraction_identity': report['extraction_identity'], 'selected_text': row['selected_text'],
        'selected_sha256': row['selected_sha256'], 'report': row, 'image': images[1], 'map': graph}}
    final = {}
    result = pdf_loader.extract_text(map_pdf, quality_report=final, resume_pages=cached)
    assert calls == ['analyze_page_image', 'audit_scene_map_image'] * 2
    assert result[4][1]['entry_room_id'] == 'door'
    assert not final['pages'][0].get('resumed')


def test_continued_draft_retains_previous_graph_repair_provenance(map_pdf, monkeypatch, tmp_path):
    from app import pdf_ingestion_drafts as drafts

    invalid = {**GRAPH, 'entry_room_id': 'missing'}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_args, **_options: invalid))
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    lease = drafts.reserve('map-history', map_pdf, 'map.pdf')
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as first:
        pdf_loader.extract_text(map_pdf, quality_report=report)
    saved = drafts.checkpoint(lease, report, first.value.result)
    lease = drafts.reserve('map-history', map_pdf, 'map.pdf', resume_draft_id=saved['draft_id'])
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as second:
        pdf_loader.extract_text(map_pdf, quality_report=report, layout_budget=saved['report']['layout_budget'])
    saved = drafts.checkpoint(lease, report, second.value.result)
    row = saved['pages']['1']['report']
    assert row['map_analysis']['repair_attempts'] == 1
    assert len(row['map_analysis_history']) == 1
    assert row['map_analysis_history'][0]['repair_attempts'] == 1
    assert row['map_analysis_history'][0]['attempts'][1]['output_graph'] == invalid
    assert saved['report']['layout_budget']['consumed_requests'] == 4
