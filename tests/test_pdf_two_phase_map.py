"""Public provider boundary exercises all map phases and bounded certification."""
import json
from types import SimpleNamespace

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
    def analyze(png, tool, prompt, **options):
        assert png == b'original-image'
        assert options['max_retries'] == 0
        calls.append(tool['name'])
        if tool['name'] == 'inventory_map_locations':
            assert 'exits' not in tool['input_schema']['properties']
            return {'page_type': 'map', 'locations': [loc()]}
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        if tool['name'] == 'audit_map_inventory':
            return {'complete': True, 'uncertainties': [], 'confirmed_ids': [payload['inventory'][0]['id']],
                    'missing_locations': [loc('Lamp Room', 'side elevation', 'lamp')]}
        if tool['name'] == 'extract_map_connectivity':
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


def test_unmarked_entry_is_incomplete_without_guess_or_retry(monkeypatch):
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
