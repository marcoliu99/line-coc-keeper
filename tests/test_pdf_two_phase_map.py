"""Public provider boundary exercises all map phases and bounded certification."""
import copy
import json
from types import SimpleNamespace

import pytest

from app import config, pdf_map_analysis
from app.providers import registry


def loc(label='Entrance', section='ground', hint='entrance'):
    return {'label': label, 'floor_or_section': section, 'id_hint': hint,
            'visible': True, 'kind': 'location', 'evidence': 'Label printed in original image'}


def final_audit(payload):
    graph = payload['graph']
    return {'complete': True, 'uncertainties': [],
        'visible_locations': [{'label': room['visible_label'], 'room_id': room['id']} for room in graph['rooms']],
        'rooms': [{'room_id': room['id'], 'verdict': 'supported', 'evidence': 'Original image label'} for room in graph['rooms']],
        'edges': [{'edge_id': edge['edge_id'], 'verdict': 'supported', 'basis': edge['visual_basis'], 'evidence': 'Original image traversal symbol'} for edge in payload['edge_inventory']],
        'entry': {'room_id': graph['entry_room_id'], 'verdict': 'supported', 'evidence': 'Visible front entrance'}}


def test_two_phase_inventory_audit_precedes_connectivity_and_canonical_build(monkeypatch):
    calls = []
    monkeypatch.setattr(config, 'PDF_MAP_INVENTORY_TIMEOUT_SECONDS', 11)
    monkeypatch.setattr(config, 'PDF_MAP_CONNECTIVITY_TIMEOUT_SECONDS', 22)
    monkeypatch.setattr(config, 'PDF_MAP_AUDIT_TIMEOUT_SECONDS', 44)
    def analyze(png, tool, prompt, **options):
        assert png == b'original-image'
        assert options['max_retries'] == 0
        expected_timeout = {'inventory_map_locations': 11, 'audit_map_inventory': 11, 'extract_map_connectivity': 22}.get(tool['name'], 44)
        assert options['timeout'] == expected_timeout
        calls.append(tool['name'])
        if tool['name'] == 'inventory_map_locations':
            assert 'exits' not in tool['input_schema']['properties']
            return {'page_type': 'map', 'locations': [loc()]}
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        if tool['name'] == 'audit_map_inventory':
            return {'complete': True, 'uncertainties': [], 'confirmed_ids': [payload['inventory'][0]['id']],
                    'missing_locations': [loc('Lamp Room', 'side elevation', 'lamp')]}
        if tool['name'] == 'extract_map_connectivity':
            schema = tool['input_schema']['properties']['edges']['items']['properties']
            for key in ('type', 'visual_basis'):
                assert schema[key]['enum'] == ['door', 'open_passage', 'stairs', 'one_way']
            assert schema['compass']['enum'] == ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW', 'U', 'D']
            assert len(payload['inventory']) == 2
            return {'entry': {'status': 'resolved', 'room_id': 'entrance', 'evidence': 'Visible entry'},
                    'edges': [{'from': 'entrance', 'to': 'lamp', 'type': 'stairs', 'visual_basis': 'stairs',
                               'compass': 'U', 'evidence': 'Stair symbol reaches lamp'}], 'missing_locations': []}
        return final_audit(payload)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=analyze))
    result = pdf_map_analysis.analyze(b'original-image', reserve=lambda: True, candidate=True)
    assert calls == ['inventory_map_locations', 'audit_map_inventory', 'extract_map_connectivity', 'audit_scene_map_image']
    assert result.analysis['status'] == 'MAP_GRAPH_VERIFIED'
    assert result.analysis['map_phase1_missing_found'] == 1
    assert len(result.graph['rooms']) == 2
    assert result.analysis['repair_attempts'] == 0
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'original-image')


def test_dangling_target_uses_one_patch_then_final_audit(monkeypatch):
    calls = []
    def analyze(_png, tool, prompt, **_options):
        calls.append(tool['name'])
        if tool['name'] == 'inventory_map_locations':
            return {'page_type': 'map', 'locations': [loc()]}
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        if tool['name'] == 'audit_map_inventory':
            return {'complete': True, 'uncertainties': [], 'confirmed_ids': [r['id'] for r in payload['inventory']], 'missing_locations': []}
        if tool['name'] == 'extract_map_connectivity':
            return {'entry': {'status': 'resolved', 'room_id': 'entrance', 'evidence': 'Visible entry'},
                    'edges': [{'id': 'stairs', 'from': 'entrance', 'to': 'lamp', 'type': 'stairs',
                               'visual_basis': 'stairs', 'compass': 'U', 'evidence': 'Visible stairs'}],
                    'missing_locations': [loc('Lamp Room', 'upper', 'lamp')]}
        if tool['name'] == 'patch_map_evidence':
            assert {'missing_location', 'dangling_exit'} <= {r['code'] for r in payload['errors']}
            return {'add_locations': [loc('Lamp Room', 'upper', 'lamp')], 'remove_edges': [],
                    'add_edges': [], 'replace_edges': [], 'entry_update': None}
        return final_audit(payload)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=analyze))
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    assert result.analysis['status'] == 'MAP_GRAPH_VERIFIED'
    assert len(calls) == 5
    assert result.analysis['repair_attempts'] == result.analysis['map_patch_add_locations'] == 1
    assert result.analysis['attempts'][3]['input_graph_sha256']
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'image')


def test_unmarked_entry_is_incomplete_without_guess_or_retry(monkeypatch, tmp_path):
    calls = []
    def analyze(_png, tool, prompt, **_options):
        calls.append(tool['name'])
        if tool['name'] == 'inventory_map_locations':
            return {'page_type': 'map', 'locations': [loc()]}
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        if tool['name'] == 'audit_map_inventory':
            return {'complete': True, 'uncertainties': [], 'confirmed_ids': [r['id'] for r in payload['inventory']], 'missing_locations': []}
        return {'entry': {'status': 'unresolved', 'room_id': '', 'evidence': ''}, 'edges': [], 'missing_locations': []}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=analyze))
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    assert result.graph is None
    assert result.analysis['status'] == 'MAP_GRAPH_INCOMPLETE'
    assert result.analysis['candidate_graph']['entry_room_id'] == ''
    assert result.analysis['validation_errors'] == ['unverified_entry']
    assert len(calls) == 3

    import pymupdf

    from app import pdf_loader, scenario_activation, scenario_library
    from app.models import GroupState

    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox((30, 30, 550, 700), 'FLOOR PLAN\n' + 'The safe original narrative remains playable. ' * 12)
        for index in range(8):
            page.draw_rect((40 + index * 10, 710, 45 + index * 10, 720))
        raw = doc.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(raw, quality_report=report)
    assert report['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert report['blocked_pages'] == []
    assert report['map_status'] == {'1': 'MAP_GRAPH_INCOMPLETE'}
    assert maps == {}
    sid = scenario_library.save_scenario(raw, title='Unmarked entry', filename='map.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=report)
    context = scenario_library.load_context(sid)
    state = GroupState(group_id='unmarked-entry')
    scenario_activation.install_context_fields(state, sid, context)
    assert 'The safe original narrative remains playable.' in state.scenario_text
    assert state.scene_maps == {}
    assert state.scenario_library_id == sid


def test_certificate_replays_phases_and_rejects_changed_inventory(map_evidence_provider):
    import copy
    map_evidence_provider()
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    changed = copy.deepcopy(result.analysis)
    changed['inventory'][0]['label'] = 'Invented location'
    assert not pdf_map_analysis.verified_graph(result.graph, changed, b'image')
    changed = copy.deepcopy(result.analysis)
    changed['attempts'] = changed['attempts'][-1:]
    assert not pdf_map_analysis.verified_graph(result.graph, changed, b'image')
    changed = copy.deepcopy(result.analysis)
    changed['inventory_audit']['confirmed_ids'] = []
    assert not pdf_map_analysis.verified_graph(result.graph, changed, b'image')


def test_budget_exhaustion_after_inventory_never_calls_connectivity(map_evidence_provider):
    calls = map_evidence_provider()
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: len(calls) < 1)
    assert len(calls) == 1
    assert result.analysis['status'] == 'MAP_GRAPH_INCOMPLETE'
    assert 'map_budget_exhausted' in result.analysis['completeness_errors']
    assert result.graph is None


def test_phase_and_patch_metrics_are_aggregated(map_evidence_provider):
    map_evidence_provider(phase_error='repair')
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    metrics = pdf_map_analysis.metrics([{'map_analysis': result.analysis}])
    assert metrics['map_phase1_locations'] == metrics['map_phase2_edges'] == 1
    assert metrics['map_targeted_repairs'] == metrics['map_patch_remove_edges'] == 1
    assert metrics['map_graph_verified'] == metrics['map_graph_repaired'] == 1
    assert metrics['map_graph_invalid'] == 0
    public = pdf_map_analysis.publication_summary(result.analysis)
    assert not {'inventory', 'inventory_audit', 'connectivity', 'targeted_patch', 'candidate_graph', 'image_evidence'} & public.keys()
    assert all('output_evidence' not in attempt for attempt in public['attempts'])
    assert result.analysis['inventory']


def test_malformed_removal_cannot_be_applied_or_replayed_as_certificate(map_evidence_provider):
    import copy
    calls = map_evidence_provider(phase_error='repair')
    valid = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    assert len(calls) == 5
    changed = copy.deepcopy(valid.analysis)
    changed['targeted_patch']['remove_edges'] = ['bad']
    changed['attempts'][3]['output_evidence'] = copy.deepcopy(changed['targeted_patch'])
    assert not pdf_map_analysis.verified_graph(valid.graph, changed, b'image')
    provider = registry.ANALYSIS_PROVIDERS[config.ANALYSIS_PROVIDER]
    original = provider.analyze_image
    def malformed(png, tool, prompt, **options):
        response = original(png, tool, prompt, **options)
        if tool['name'] == 'patch_map_evidence':
            response['remove_edges'] = ['bad']
        return response
    provider.analyze_image = malformed
    rejected = pdf_map_analysis.analyze(b'image', reserve=lambda: True)
    assert rejected.graph is None
    assert rejected.analysis['status'] == 'MAP_GRAPH_INVALID'
    assert 'malformed_patch' in rejected.analysis['validation_errors']


def test_connectivity_schema_is_closed_and_builder_rejects_free_text():
    from app import pdf_map_evidence

    inventory, errors = pdf_map_evidence.merge_inventory([], [loc(), loc('Hall', 'ground', 'hall')])
    assert errors == []
    edge = {'id': 'door', 'from': 'entrance', 'to': 'hall', 'type': 'door',
            'visual_basis': 'door', 'compass': 'E', 'evidence': 'Visible doorway'}
    evidence = {'entry': {'status': 'resolved', 'room_id': 'entrance', 'evidence': 'Front door'},
                'edges': [edge], 'missing_locations': []}
    normalized, errors = pdf_map_evidence.normalize_connectivity(evidence)
    assert errors == []
    graph, errors = pdf_map_evidence.build_graph(inventory, normalized)
    assert errors == []
    assert graph['rooms'][0]['exits'][0]['visual_basis'] == 'door'
    edge['visual_basis'] = 'visible doorway'
    normalized, _ = pdf_map_evidence.normalize_connectivity(evidence)
    _, errors = pdf_map_evidence.build_graph(inventory, normalized)
    assert any(error['code'] == 'unsupported_edge' for error in errors)
    edge['visual_basis'] = 'door'
    edge['compass'] = 'roughly east'
    normalized, _ = pdf_map_evidence.normalize_connectivity(evidence)
    _, errors = pdf_map_evidence.build_graph(inventory, normalized)
    assert any(error['code'] == 'invalid_compass' for error in errors)


@pytest.mark.parametrize('case', ['valid_patch', 'unrelated_change', 'unresolved_entry'])
def test_two_missing_locations_and_scoped_invalid_edges_preserve_other_evidence(monkeypatch, case):
    calls = []
    good = {'id': 'keep', 'from': 'entrance', 'to': 'hall', 'type': 'door',
            'visual_basis': 'door', 'compass': 'E', 'evidence': 'Original visible doorway'}
    missing = [loc('Lamp Room', 'upper', 'lamp'), loc('Lantern Gallery', 'upper', 'gallery')]
    def analyze(_png, tool, prompt, **options):
        calls.append(tool['name'])
        assert options['max_retries'] == 0
        if tool['name'] == 'inventory_map_locations':
            return {'page_type': 'map', 'locations': [loc(), loc('Hall', 'ground', 'hall')]}
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        if tool['name'] == 'audit_map_inventory':
            return {'complete': True, 'uncertainties': [],
                    'confirmed_ids': [r['id'] for r in payload['inventory']], 'missing_locations': []}
        if tool['name'] == 'extract_map_connectivity':
            entry = ({'status': 'unresolved', 'room_id': '', 'evidence': ''} if case == 'unresolved_entry'
                     else {'status': 'resolved', 'room_id': 'entrance', 'evidence': 'Visible front entrance'})
            return {'entry': entry, 'missing_locations': copy.deepcopy(missing), 'edges': [copy.deepcopy(good),
                {'id': 'stairs', 'from': 'hall', 'to': 'lamp', 'type': 'stairs', 'visual_basis': 'visible stairs',
                 'compass': 'U', 'evidence': 'Stair symbol'},
                {'id': 'gallery', 'from': 'lamp', 'to': 'gallery', 'type': 'door', 'visual_basis': 'visible doorway',
                 'compass': 'E', 'evidence': 'Gallery door'},
                {'id': 'wall', 'from': 'hall', 'to': 'entrance', 'type': 'door', 'visual_basis': 'wall',
                 'compass': 'W', 'evidence': 'Shared solid wall'}]}
        if tool['name'] == 'patch_map_evidence':
            assert options['timeout'] == 60
            for field in ('type', 'visual_basis'):
                assert tool['input_schema']['properties']['add_edges']['items']['properties'][field]['enum'] == [
                    'door', 'open_passage', 'stairs', 'one_way']
            assert {'missing_location', 'unsupported_edge', 'dangling_exit'} <= {e['code'] for e in payload['errors']}
            assert {e['subject'] for e in payload['errors'] if e['code'] == 'missing_location'} == {'Lamp Room', 'Lantern Gallery'}
            assert payload['connectivity']['edges'][0] == good
            patch = {'add_locations': copy.deepcopy(missing), 'remove_edges': [{'edge_id': 'wall',
                'evidence': 'Solid wall without opening', 'reason': 'No traversal in image'}],
                'add_edges': [], 'replace_edges': [
                    {'edge_id': 'stairs', 'reason': 'Visible stairs reach lamp', 'edge': {
                        'id': 'stairs', 'from': 'hall', 'to': 'lamp', 'type': 'stairs', 'visual_basis': 'stairs',
                        'compass': 'U', 'evidence': 'Stairs reach Lamp Room'}},
                    {'edge_id': 'gallery', 'reason': 'Visible door reaches gallery', 'edge': {
                        'id': 'gallery', 'from': 'lamp', 'to': 'gallery', 'type': 'door', 'visual_basis': 'door',
                        'compass': 'E', 'evidence': 'Door reaches Lantern Gallery'}}], 'entry_update': None}
            if case == 'unrelated_change':
                patch['replace_edges'].append({'edge_id': 'keep', 'edge': {**good, 'compass': 'N',
                    'evidence': 'Different alleged doorway'}, 'reason': 'Unrelated change'})
            return patch
        assert case == 'valid_patch'  # Never audit rejected patches or unresolved entry.
        return final_audit(payload)
    monkeypatch.setattr(config, 'PDF_MAP_REPAIR_TIMEOUT_SECONDS', 60)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=analyze))
    result = pdf_map_analysis.analyze(b'image', reserve=lambda: True, candidate=True)
    assert result.analysis['repair_attempts'] == 1
    assert result.analysis['connectivity']['edges'][0] == good
    if case == 'unrelated_change':
        assert result.graph is None
        assert result.analysis['status'] == 'MAP_GRAPH_INVALID'
        assert 'out_of_scope_patch' in result.analysis['validation_errors']
        assert len(result.analysis['inventory']) == 2  # Patch is rejected atomically.
        assert len(calls) == 4
    else:
        assert len(result.analysis['inventory']) == 4
        assert result.analysis['map_patch_add_locations'] == 2
        assert len(result.analysis['candidate_graph']['rooms']) == 4
        assert sum(len(room['exits']) for room in result.analysis['candidate_graph']['rooms']) == 3
        assert len(calls) == (5 if case == 'valid_patch' else 4)
        if case == 'valid_patch':
            assert result.analysis['status'] == 'MAP_GRAPH_VERIFIED'
            assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'image')
        else:
            assert result.graph is None
            assert result.analysis['status'] == 'MAP_GRAPH_INCOMPLETE'
            assert result.analysis['candidate_graph']['entry_room_id'] == ''
            assert result.analysis['entry_status'] == 'unresolved'
