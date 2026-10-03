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


def test_valid_graph_requires_all_image_phases_before_verification(map_evidence_provider):
    from app import pdf_map_analysis
    calls = map_evidence_provider()
    result = pdf_map_analysis.analyze(b'original-image', reserve=lambda: True, candidate=True)
    assert result.analysis['status'] == 'MAP_GRAPH_VERIFIED'
    assert len(calls) == 4
    assert result.graph['rooms'][0]['visible_label'] == 'Entrance'
    assert scene_map.validate_scene_map(result.graph) == []
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'original-image')


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_invalid_connectivity_gets_one_scoped_repair(map_evidence_provider, repair_succeeds):
    from app import pdf_map_analysis
    calls = map_evidence_provider(phase_error='repair' if repair_succeeds else 'invalid')
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    assert result.analysis['repair_attempts'] == 1
    attempt = result.analysis['attempts'][3]
    assert attempt['stage'] == 'targeted_repair'
    assert attempt['input_graph_sha256']
    assert 'dangling_exit' in attempt['validation_errors']
    assert attempt['elapsed_seconds'] >= 0
    assert len(calls) == (5 if repair_succeeds else 4)
    assert result.analysis['status'] == ('MAP_GRAPH_VERIFIED' if repair_succeeds else 'MAP_GRAPH_INVALID')
    assert (result.graph is not None) == repair_succeeds


def test_independent_image_rejection_never_certifies(map_evidence_provider):
    from app import pdf_map_analysis
    map_evidence_provider(final_error='unsupported')
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    assert result.analysis['status'] == 'MAP_GRAPH_INVALID'
    assert result.graph is None
    assert result.analysis['repair_attempts'] == 0  # No full regeneration after final audit.


def test_missing_image_visible_location_remains_incomplete(map_evidence_provider):
    from app import pdf_map_analysis
    map_evidence_provider(final_error='missing')
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    assert result.analysis['status'] == 'MAP_GRAPH_INCOMPLETE'
    assert 'missing_visible_location:Lamp Room' in result.analysis['completeness_errors']
    assert result.graph is None


def test_invalid_graph_stays_private_and_never_in_loader_maps(map_pdf, map_evidence_provider, monkeypatch, tmp_path):
    from app import pdf_ingestion_drafts as drafts
    map_evidence_provider(phase_error='invalid')
    report = {}
    result = pdf_loader.extract_text(map_pdf, quality_report=report)
    assert result[4] == {}
    assert report['soft_review_pages'] == [1]
    assert report['blocked_pages'] == []
    assert report['map_graph_generated'] == report['map_graph_invalid'] == 1
    candidate = report['pages'][0]['map_analysis']['candidate_graph']
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    lease = drafts.reserve('map-test', map_pdf, 'map.pdf')
    saved = drafts.checkpoint(lease, report, result)
    assert saved['pages']['1']['map'] is None
    assert saved['pages']['1']['report']['map_analysis']['candidate_graph'] == candidate


def test_invalid_graph_cannot_bypass_library_guard_with_missing_quality_report(map_pdf, monkeypatch, tmp_path):
    from app import scenario_library

    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    with pytest.raises(ValueError, match='scene_map'):
        scenario_library.save_scenario(map_pdf, title='Map', filename='map.pdf', preview='',
            text='--- 第 1 頁 ---\nOriginal source', indexes={}, pregens=[], page_images={},
            page_maps={1: {**GRAPH, 'entry_room_id': 'missing'}})
    assert list(tmp_path.iterdir()) == []


def test_certified_graph_persists_without_changing_source(map_pdf, map_evidence_provider, monkeypatch, tmp_path):
    from app import scenario_library
    map_evidence_provider()
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(map_pdf, quality_report=report)
    assert 'A visible entrance.' not in text
    assert 'Visible native source text.' in text
    assert report['pages'][0]['map_analysis']['verified']
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    scenario = scenario_library.save_scenario(map_pdf, title='Map', filename='map.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=report)
    assert scenario_library.load_context(scenario)['scene_maps'] == {'1': maps[1]}


def test_shared_budget_is_checkpointed_before_every_dispatch(map_pdf, monkeypatch, map_evidence_provider):
    from app import pdf_layout_adapters
    snapshots = []
    calls = map_evidence_provider()
    provider = registry.ANALYSIS_PROVIDERS[config.ANALYSIS_PROVIDER]
    original = provider.analyze_image
    def analyze(*args, **options):
        assert snapshots[-1]['consumed_requests'] == len(calls) + 1
        return original(*args, **options)
    provider.analyze_image = analyze
    budget = pdf_layout_adapters.new_budget()
    report = {}
    pdf_loader.extract_text(map_pdf, quality_report=report, layout_budget=budget,
        layout_budget_checkpoint=lambda value: snapshots.append(dict(value)))
    assert len(calls) == 4
    assert report['layout_budget']['consumed_requests'] == 4


def test_exhausted_budget_keeps_map_unanalyzed_without_dispatch(map_pdf, monkeypatch):
    from unittest.mock import Mock

    from app import pdf_layout_adapters

    budget = pdf_layout_adapters.new_budget()
    budget['consumed_requests'] = budget['configured_max_requests']
    call = Mock(return_value=GRAPH)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=call))
    report = {}
    pdf_loader.extract_text(map_pdf, quality_report=report, layout_budget=budget)
    assert report['soft_review_pages'] == [1]
    assert report['blocked_pages'] == []
    call.assert_not_called()
    assert report['pages'][0]['map_analysis']['status'] == 'MAP_NOT_ANALYZED'
    assert report['map_analysis_attempted'] == 0


def test_verified_boolean_without_image_audit_is_not_a_certificate():
    from app import pdf_map_analysis

    claims = {'version': pdf_map_analysis.VERSION, 'status': 'MAP_GRAPH_VERIFIED', 'verified': True,
              'graph_sha256': pdf_map_analysis.graph_hash(GRAPH)}
    assert not pdf_map_analysis.verified_graph(GRAPH, claims)


def test_modified_cached_graph_is_reanalyzed(map_pdf, map_evidence_provider):
    import copy
    import hashlib
    calls = map_evidence_provider()
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
    assert len(calls) == 8
    assert result[4][1] == maps[1]
    assert not final['pages'][0].get('resumed')


def test_explicit_reanalysis_retains_previous_patch_provenance(map_pdf, map_evidence_provider, monkeypatch, tmp_path):
    from app import pdf_ingestion_drafts as drafts
    map_evidence_provider(phase_error='invalid')
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    lease = drafts.reserve('map-history', map_pdf, 'map.pdf')
    report = {}
    first = pdf_loader.extract_text(map_pdf, quality_report=report)
    saved = drafts.checkpoint(lease, report, first)
    lease = drafts.reserve('map-history', map_pdf, 'map.pdf', resume_draft_id=saved['draft_id'])
    report = {}
    second = pdf_loader.extract_text(map_pdf, quality_report=report, layout_budget=saved['report']['layout_budget'])
    saved = drafts.checkpoint(lease, report, second)
    row = saved['pages']['1']['report']
    assert row['map_analysis']['repair_attempts'] == 1
    assert len(row['map_analysis_history']) == 1
    previous = row['map_analysis_history'][0]
    assert previous['repair_attempts'] == 1
    assert previous['attempts'][3]['output_evidence'] == previous['targeted_patch']
    assert saved['report']['layout_budget']['consumed_requests'] == 8


@pytest.mark.parametrize(('response', 'status'), [(None, 'MAP_ANALYSIS_FAILED'),
    ({'page_type': 'map', 'rooms': []}, 'MAP_GRAPH_MISSING'),
    ({'page_type': 'other', 'description': 'Not a map'}, 'MAP_GRAPH_MISSING')])
def test_attempted_analysis_failure_and_missing_graph_are_distinct(monkeypatch, response, status):
    from app import pdf_map_analysis

    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_options: response))
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True, candidate=True)
    assert result.analysis['analysis_attempted'] is True
    assert result.analysis['status'] == status
    assert result.analysis['graph_generated'] is False
    assert result.graph is None
    assert result.analysis['repair_attempts'] == 0


def test_final_audit_cannot_erase_inventory_label(map_evidence_provider, monkeypatch):
    from app import pdf_map_analysis
    map_evidence_provider()
    provider = registry.ANALYSIS_PROVIDERS[config.ANALYSIS_PROVIDER]
    original = provider.analyze_image
    def analyze(png, tool, prompt, **options):
        evidence = original(png, tool, prompt, **options)
        if tool['name'] == 'audit_scene_map_image':
            evidence['visible_locations'] = []
        return evidence
    provider.analyze_image = analyze
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True, candidate=True)
    assert result.analysis['status'] == 'MAP_GRAPH_INCOMPLETE'
    assert 'incomplete_visible_location_audit' in result.analysis['completeness_errors']
    assert result.graph is None
