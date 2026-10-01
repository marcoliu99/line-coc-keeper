"""Import-only image graph certification; unverified candidates stay private."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from app import config, scene_map
from app import pdf_map_evidence as maps
from app.providers import image_diagnostics
from app.providers.registry import analysis_provider

VERSION = 'image-map-certification-v2'
MapStatus = Literal['MAP_NOT_ANALYZED', 'MAP_ANALYSIS_FAILED', 'MAP_GRAPH_MISSING',
                    'MAP_GRAPH_INVALID', 'MAP_GRAPH_INCOMPLETE', 'MAP_GRAPH_VERIFIED', 'NOT_MAP']
MapStage = Literal['phase1_generation', 'phase1_audit', 'phase2_generation', 'targeted_repair', 'image_audit']


class MapAnalysis(TypedDict):
    version: str
    image_sha256: str
    status: MapStatus
    candidate: bool
    analysis_attempted: bool
    graph_generated: bool
    verified: bool
    graph_sha256: str
    candidate_graph: Any
    validation_errors: list[str]
    validation_details: list[scene_map.GraphIssue]
    diagnostics: list[scene_map.GraphIssue]
    completeness_errors: list[str]
    repair_attempts: int
    attempts: list[dict]
    image_evidence: list[dict]
    initial_status: MapStatus
    inventory: list[maps.Location]
    inventory_audit: dict
    connectivity: dict
    graph_errors: list[maps.MapError]
    targeted_patch: Any
    entry_status: Literal['resolved', 'unresolved']
    map_phase1_locations: int
    map_phase1_missing_found: int
    map_phase2_edges: int
    map_targeted_repairs: int
    map_patch_add_locations: int
    map_patch_remove_edges: int
    map_patch_add_edges: int


@dataclass(frozen=True)
class MapResult:
    description: str
    graph: dict | None
    analysis: MapAnalysis


def graph_hash(graph: Any) -> str:
    return hashlib.sha256(json.dumps(graph, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verified_graph(graph: Any, analysis: dict | None, image: bytes | None = None) -> bool:
    """Recheck certificates at cache/publication boundaries without trusting status alone."""
    if (not isinstance(analysis, dict) or analysis.get('version') != VERSION
            or analysis.get('status') != 'MAP_GRAPH_VERIFIED' or analysis.get('verified') is not True
            or scene_map.validate_scene_map(graph) or analysis.get('graph_sha256') != graph_hash(graph)
            or (image is not None and analysis.get('image_sha256') != hashlib.sha256(image).hexdigest())):
        return False
    inventory, connectivity, inventory_audit = (analysis.get(key) for key in ('inventory', 'connectivity', 'inventory_audit'))
    if (not isinstance(inventory, list) or not inventory or not isinstance(connectivity, dict)
            or not isinstance(inventory_audit, dict) or inventory_audit.get('complete') is not True
            or inventory_audit.get('uncertainties') != []):
        return False
    evidence, attempts = analysis.get('image_evidence'), analysis.get('attempts')
    if (not isinstance(evidence, list) or not evidence or any(not isinstance(e, dict) for e in evidence)
            or not isinstance(attempts, list) or any(not isinstance(a, dict) for a in attempts)):
        return False
    if not _certificate_phases(analysis, attempts, graph):
        return False
    audit = attempts[-1]
    if (audit.get('stage') != 'image_audit' or audit.get('input_graph_sha256') != graph_hash(graph)
            or audit.get('image_sha256') != analysis.get('image_sha256')
            or audit.get('output_evidence') != evidence[-1]):
        return False
    return not _audit_errors(graph, evidence[-1], _visible_labels(evidence[:-1]))


def _certificate_phases(analysis: dict, attempts: list[dict], graph: dict) -> bool:
    """Replay deterministic merges/patches, rather than trusting saved phase claims."""
    stages = [a.get('stage') for a in attempts]
    base = ['phase1_generation', 'phase1_audit', 'phase2_generation']
    if (stages not in [base + ['image_audit'], base + ['targeted_repair', 'image_audit']]
            or any(a.get('image_sha256') != analysis['image_sha256'] for a in attempts)):
        return False
    try:
        generation = attempts[0]['output_evidence']
        if generation.get('page_type') != 'map' or any(k in generation for k in ('rooms', 'edges', 'exits')):
            return False
        inventory, errors = maps.merge_inventory([], generation['locations'])
        audit = attempts[1]['output_evidence']
        if (errors or not inventory or audit != analysis['inventory_audit'] or audit.get('complete') is not True
                or audit.get('uncertainties') != [] or not isinstance(audit.get('confirmed_ids'), list)
                or any(not isinstance(i, str) for i in audit['confirmed_ids'])
                or Counter(audit['confirmed_ids']) != Counter(r['id'] for r in inventory)):
            return False
        inventory, errors = maps.merge_inventory(inventory, audit['missing_locations'])
        connectivity, connection_errors = maps.normalize_connectivity(attempts[2]['output_evidence'])
        if errors or connection_errors:
            return False
        if stages[3] == 'targeted_repair':
            current, errors = maps.build_graph(inventory, connectivity)
            patch = attempts[3]['output_evidence']
            if (not errors or attempts[3].get('input_graph_sha256') != graph_hash(current)
                    or patch != analysis['targeted_patch']):
                return False
            inventory, connectivity, errors = maps.apply_patch(
                inventory, connectivity, patch, errors, verified_edges=set(), verified_entry=False)
            if errors:
                return False
        rebuilt, errors = maps.build_graph(inventory, connectivity)
        return not errors and rebuilt == graph and inventory == analysis['inventory'] and connectivity == analysis['connectivity']
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


_AUDIT_TOOL = {
    'name': 'audit_scene_map_image',
    'description': 'Independently inspect the original image for complete spatial labels and every proposed graph edge.',
    'input_schema': {'type': 'object', 'properties': {
        'complete': {'type': 'boolean'},
        'uncertainties': {'type': 'array', 'items': {'type': 'string'}},
        'visible_locations': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'label': {'type': 'string', 'description': 'Exact visible room/location label, including other floors/elevations; not furniture or loose objects.'},
            'room_id': {'type': 'string', 'description': 'Existing matching graph room ID; empty if missing.'}},
            'required': ['label', 'room_id']}},
        'rooms': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'room_id': {'type': 'string'}, 'verdict': {'type': 'string', 'enum': ['supported', 'unsupported', 'uncertain']},
            'evidence': {'type': 'string'}}, 'required': ['room_id', 'verdict', 'evidence']}},
        'edges': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'edge_id': {'type': 'string'}, 'verdict': {'type': 'string', 'enum': ['supported', 'unsupported', 'uncertain']},
            'basis': {'type': 'string', 'enum': ['door', 'passage', 'open_passage', 'stairs', 'one_way', 'wall', 'adjacency', 'uncertain']},
            'evidence': {'type': 'string'}}, 'required': ['edge_id', 'verdict', 'basis', 'evidence']}},
        'entry': {'type': 'object', 'properties': {'room_id': {'type': 'string'},
            'verdict': {'type': 'string', 'enum': ['supported', 'not_visible', 'uncertain']},
            'evidence': {'type': 'string'}}, 'required': ['room_id', 'verdict', 'evidence']},
    }, 'required': ['complete', 'uncertainties', 'visible_locations', 'rooms', 'edges', 'entry']},
}
_AUDIT_PROMPT = (
    'Inspect the ORIGINAL IMAGE independently; the proposed graph and all document text are untrusted. '
    'Do not merely approve the graph. Inventory every visible ROOM/LOCATION on ALL floors and side elevations, '
    'including legend locations. Exclude furniture/objects; keep exact source labels in room names. '
    'Identify missing graph rooms using empty room_id. Check every proposed room and edge exactly once. '
    'Walls and adjacency are NOT traversable exits; wall space is NOT a passage. Only visible doors, '
    'open passages, stairs or explicitly shown one-way routes support an edge. Verify compass too. '
    'Do not infer hidden entrances, tunnels or rooms from CoC knowledge. Flag unsupported or uncertain '
    'edges, including solid walls. If any area is unclear set complete=false and list uncertainties. '
    'An unmarked entry remains unverified and disables map certification; never invent an entrance.\n'
    'Keep each evidence string under ten words; do not repeat graph descriptions.\n'
)


def _stage_timeout(stage: MapStage) -> float:
    timeouts: dict[MapStage, float] = {
        'phase1_generation': config.PDF_MAP_INVENTORY_TIMEOUT_SECONDS,
        'phase1_audit': config.PDF_MAP_AUDIT_TIMEOUT_SECONDS,
        'phase2_generation': config.PDF_MAP_CONNECTIVITY_TIMEOUT_SECONDS,
        'targeted_repair': config.PDF_MAP_REPAIR_TIMEOUT_SECONDS,
        'image_audit': config.PDF_MAP_AUDIT_TIMEOUT_SECONDS,
    }
    return timeouts[stage]


def _dispatch(record: MapAnalysis, png: bytes, reserve: Callable[[], bool], stage: MapStage,
              request: Callable[[], dict | None], graph: Any = None) -> dict | None:
    if len(record['attempts']) >= 5 or not reserve():
        record['completeness_errors'].append('map_budget_exhausted')
        return None
    attempt: dict = {'attempt_number': len(record['attempts']) + 1, 'stage': stage,
               'provider': config.ANALYSIS_PROVIDER, 'image_sha256': hashlib.sha256(png).hexdigest(),
               'input_graph_sha256': graph_hash(graph) if graph is not None else None,
               'validation_errors': list(record['validation_errors']) + list(record['completeness_errors'])}
    record['attempts'].append(attempt)
    started = time.perf_counter()
    with image_diagnostics.capture(stage) as failures:
        try:
            output = request()
            attempt['output_evidence'] = copy.deepcopy(output)
            if output is None and not failures:
                image_diagnostics.record(config.ANALYSIS_PROVIDER, 'EmptyResponse')
            return output
        except Exception as error:  # noqa: BLE001 - quarantine failure without private exception text.
            image_diagnostics.record(config.ANALYSIS_PROVIDER, error)
            return None
        finally:
            if failures:
                attempt['failure'] = failures[-1]
            attempt['elapsed_seconds'] = round(time.perf_counter() - started, 3)


def _label_key(text: str) -> str:
    return ''.join(c for c in text.casefold() if c.isalnum())


def _visible_labels(evidence: list[dict]) -> list[str]:
    return [location['label'] for item in evidence
            for location in (item['visible_locations'] if isinstance(item.get('visible_locations'), list) else [])
            if isinstance(location, dict) and isinstance(location.get('label'), str) and _label_key(location['label'])]


def _audit_errors(graph: dict, evidence: dict, prior_labels: list[str]) -> list[str]:
    errors = []
    if evidence.get('complete') is not True or evidence.get('uncertainties') != []:
        errors.append('image_completeness_uncertain')
    rooms = {room['id']: room for room in graph['rooms']}
    locations = evidence.get('visible_locations')
    if not isinstance(locations, list) or not locations:
        errors.append('visible_location_inventory_missing')
        locations = []
    for location in locations:
        if not isinstance(location, dict) or not isinstance(location.get('label'), str) or not _label_key(location['label']):
            errors.append('malformed_visible_location')
            continue
        label, room_id = location['label'], location.get('room_id')
        room = rooms.get(room_id) if isinstance(room_id, str) else None
        if room is None or _label_key(label) not in _label_key(room['name']):
            errors.append('missing_visible_location:' + label)
    represented = {location.get('room_id') for location in locations if isinstance(location, dict)
                   and isinstance(location.get('room_id'), str)}
    if represented != set(rooms):
        errors.append('incomplete_visible_location_audit')
    for location in locations:
        if isinstance(location, dict) and isinstance(location.get('room_id'), str):
            room = rooms.get(location['room_id'])
            if room and room.get('visible_label') and location.get('label') != room['visible_label']:
                errors.append('visible_label_mismatch:' + location['room_id'])
    for label in prior_labels:
        if not any(_label_key(label) in _label_key(room['name']) for room in rooms.values()):
            errors.append('missing_prior_visible_location:' + label)
    edge_bases = {f'{room["id"]}:{i}': edge.get('visual_basis') for room in rooms.values()
                  for i, edge in enumerate(room.get('exits', []))}
    expected = {'rooms': set(rooms), 'edges': {f'{room["id"]}:{index}' for room in rooms.values()
                                              for index, _ in enumerate(room.get('exits', []))}}
    for kind, key in [('rooms', 'room_id'), ('edges', 'edge_id')]:
        rows = evidence.get(kind)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get(key), str) for row in rows):
            errors.append('malformed_image_' + kind + '_audit')
            continue
        if Counter(row[key] for row in rows) != Counter(expected[kind]):
            errors.append('incomplete_image_' + kind + '_audit')
        for row in rows:
            if row.get('verdict') != 'supported' or not isinstance(row.get('evidence'), str) or not row['evidence'].strip():
                prefix = 'unsupported_' if row.get('verdict') == 'unsupported' else 'uncertain_'
                errors.append(prefix + kind + ':' + row[key])
            if kind == 'edges' and maps.traversal_kind(row.get('basis')) is None:
                errors.append('non_traversable_edge:' + row[key])
            if kind == 'edges' and edge_bases.get(row[key]) and maps.traversal_kind(row.get('basis')) != edge_bases[row[key]]:
                errors.append('conflicting_edge_basis:' + row[key])
    entry = evidence.get('entry')
    if (not isinstance(entry, dict) or entry.get('room_id') != graph.get('entry_room_id', '')
            or not isinstance(entry.get('evidence'), str) or not entry['evidence'].strip()
            or entry.get('verdict') != 'supported'):
        errors.append('image_entry_unverified')
    return list(dict.fromkeys(errors))


def _audit(record: MapAnalysis, png: bytes, graph: dict, reserve: Callable[[], bool]) -> bool:
    provider = analysis_provider()
    if provider is None:
        record['completeness_errors'] = ['image_audit_unavailable']
        return False
    edges = [{'edge_id': f'{room["id"]}:{index}', 'from': room['id'], **edge}
             for room in graph['rooms'] for index, edge in enumerate(room.get('exits', []))]
    prompt = _AUDIT_PROMPT + '\nINPUT_JSON\n' + json.dumps({'graph': graph, 'edge_inventory': edges}, ensure_ascii=False)
    evidence = _dispatch(record, png, reserve, 'image_audit', lambda: provider.analyze_image(
        png, _AUDIT_TOOL, prompt, timeout=_stage_timeout('image_audit'), max_retries=0), graph)
    if not isinstance(evidence, dict):
        record['completeness_errors'] = list(dict.fromkeys(record['completeness_errors'] + ['image_audit_unavailable']))
        return False
    prior_labels = _visible_labels(record['image_evidence'])
    record['image_evidence'].append(copy.deepcopy(evidence))
    record['completeness_errors'] = _audit_errors(graph, evidence, prior_labels)
    record['status'] = ('MAP_GRAPH_INVALID' if any(reason.startswith(('unsupported_', 'non_traversable_edge:', 'conflicting_edge_basis:'))
                                                 for reason in record['completeness_errors'])
                        else 'MAP_GRAPH_INCOMPLETE')
    record['attempts'][-1]['validation_result'] = {'completeness_errors': list(record['completeness_errors'])}
    return not record['completeness_errors']


_LOCATION_SCHEMA = {'type': 'object', 'properties': {
    'id_hint': {'type': 'string'}, 'label': {'type': 'string'},
    'floor_or_section': {'type': 'string'}, 'visible': {'type': 'boolean'},
    'kind': {'type': 'string', 'enum': ['location', 'room', 'furniture', 'object']},
    'evidence': {'type': 'string'}},
    'required': ['id_hint', 'label', 'floor_or_section', 'visible', 'kind', 'evidence']}
_EDGE_SCHEMA: dict[str, Any] = {'type': 'object', 'properties': {
    key: {'type': 'string'} for key in ('id', 'from', 'to', 'type', 'compass', 'visual_basis', 'evidence')},
    'required': ['id', 'from', 'to', 'type', 'compass', 'visual_basis', 'evidence']}
for _field in ('type', 'visual_basis'):
    _EDGE_SCHEMA['properties'][_field]['enum'] = ['door', 'open_passage', 'stairs', 'one_way']
_EDGE_SCHEMA['properties']['compass']['enum'] = list(maps.COMPASS_DIRECTIONS)
_ENTRY_SCHEMA = {'type': 'object', 'properties': {
    'status': {'type': 'string', 'enum': ['resolved', 'unresolved']},
    'room_id': {'type': 'string'}, 'evidence': {'type': 'string'}, 'reason': {'type': 'string'}},
    'required': ['status', 'room_id', 'evidence']}


def _tool(name: str, properties: dict) -> dict:
    return {'name': name, 'description': name.replace('_', ' '),
            'input_schema': {'type': 'object', 'properties': properties, 'required': list(properties)}}


_INVENTORY_TOOL = _tool('inventory_map_locations', {
    'page_type': {'type': 'string', 'enum': ['map', 'illustration', 'other']},
    'description': {'type': 'string'}, 'locations': {'type': 'array', 'items': _LOCATION_SCHEMA}})
_INVENTORY_AUDIT_TOOL = _tool('audit_map_inventory', {
    'complete': {'type': 'boolean'}, 'uncertainties': {'type': 'array', 'items': {'type': 'string'}},
    'confirmed_ids': {'type': 'array', 'items': {'type': 'string'}},
    'missing_locations': {'type': 'array', 'items': _LOCATION_SCHEMA}})
_CONNECTIVITY_TOOL = _tool('extract_map_connectivity', {
    'entry': _ENTRY_SCHEMA, 'edges': {'type': 'array', 'items': _EDGE_SCHEMA},
    'missing_locations': {'type': 'array', 'items': _LOCATION_SCHEMA}})
_PATCH_TOOL = _tool('patch_map_evidence', {
    'add_locations': {'type': 'array', 'items': _LOCATION_SCHEMA},
    'remove_edges': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'edge_id': {'type': 'string'}, 'evidence': {'type': 'string'}, 'reason': {'type': 'string'}},
        'required': ['edge_id', 'evidence', 'reason']}},
    'add_edges': {'type': 'array', 'items': _EDGE_SCHEMA},
    'replace_edges': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'edge_id': {'type': 'string'}, 'edge': _EDGE_SCHEMA, 'reason': {'type': 'string'}},
        'required': ['edge_id', 'edge', 'reason']}},
    'entry_update': {'anyOf': [_ENTRY_SCHEMA, {'type': 'null'}]}})
_IMAGE_RULES = ('Inspect ONLY the original image. No narrative, CoC knowledge or invisible rooms. '
                'Cover ALL floors, side elevations, insets and location legends. Furniture is not a room. ')


def _request(record: MapAnalysis, png: bytes, reserve: Callable[[], bool], stage: MapStage,
             tool: dict, instruction: str, payload: dict, graph: Any = None) -> dict | None:
    provider = analysis_provider()
    if provider is None:
        return None
    prompt = _IMAGE_RULES + instruction + '\nINPUT_JSON\n' + json.dumps(payload, ensure_ascii=False)
    count = len(record['attempts'])
    response = _dispatch(record, png, reserve, stage, lambda: provider.analyze_image(
        png, tool, prompt, timeout=_stage_timeout(stage), max_retries=0), graph)
    if len(record['attempts']) > count:
        record['attempts'][-1]['input_evidence_sha256'] = graph_hash(payload)
    return response


def _build(record: MapAnalysis) -> dict:
    graph, errors = maps.build_graph(record['inventory'], record['connectivity'])
    record['graph_errors'] = errors
    record['entry_status'] = 'resolved' if graph['entry_room_id'] else 'unresolved'
    record['candidate_graph'] = copy.deepcopy(graph)
    record['graph_generated'] = True
    record['graph_sha256'] = graph_hash(graph)
    record['validation_errors'] = list(dict.fromkeys(e['code'] for e in errors))
    checks = scene_map.inspect_scene_map(graph)
    record['validation_details'] = checks['errors']
    record['diagnostics'] = checks['diagnostics']
    resolved_claim = record['connectivity'].get('entry', {}).get('status') == 'resolved'
    record['status'] = ('MAP_GRAPH_INVALID' if any(e['code'] not in {'unverified_entry', 'missing_location'}
                                                 or (e['code'] == 'unverified_entry' and resolved_claim)
                                                 for e in errors) else 'MAP_GRAPH_INCOMPLETE')
    record['attempts'][-1]['validation_result'] = copy.deepcopy(errors)
    return graph


def analyze(png: bytes, *, reserve: Callable[[], bool], candidate: bool = False) -> MapResult:
    """At most five budgeted image requests; repair only pre-audit failing evidence."""
    record = not_analyzed(candidate=candidate)
    record['image_sha256'] = hashlib.sha256(png).hexdigest()
    response = _request(record, png, reserve, 'phase1_generation', _INVENTORY_TOOL,
                        'Inventory visible locations ONLY. Exact labels and sections. NO edges/exits or entry.', {})
    record['analysis_attempted'] = bool(record['attempts'])
    if not isinstance(response, dict):
        record['status'] = 'MAP_ANALYSIS_FAILED' if record['analysis_attempted'] or analysis_provider() is None else 'MAP_NOT_ANALYZED'
        record['initial_status'] = record['status']
        return MapResult('', None, record)
    description = response.get('description', '')
    description = description if isinstance(description, str) else ''
    if response.get('page_type') != 'map':
        record['status'] = 'MAP_GRAPH_MISSING' if candidate else 'NOT_MAP'
        record['initial_status'] = record['status']
        return MapResult(description, None, record)
    record['candidate'] = True
    inventory, errors = maps.merge_inventory([], response.get('locations', []))
    record['inventory'] = inventory
    if errors or not inventory or any(key in response for key in ('edges', 'exits', 'rooms')):
        record['candidate_graph'] = copy.deepcopy(response)
        record['graph_errors'] = errors
        malformed = errors or any(response.get(key) for key in ('edges', 'exits', 'rooms'))
        record['validation_errors'] = [e['code'] for e in errors] or (['malformed_inventory'] if malformed else [])
        record['status'] = 'MAP_GRAPH_INVALID' if malformed else 'MAP_GRAPH_MISSING'
        record['initial_status'] = record['status']
        return MapResult(description, None, record)
    audit = _request(record, png, reserve, 'phase1_audit', _INVENTORY_AUDIT_TOOL,
                     'Audit locations ONLY; no topology. Confirm each existing ID once. Return only newly '
                     'image-verified missing locations; never rebuild inventory. Mark unclear areas uncertain.',
                     {'inventory': inventory})
    record['inventory_audit'] = copy.deepcopy(audit) if isinstance(audit, dict) else {}
    confirmed = audit.get('confirmed_ids') if isinstance(audit, dict) else None
    if (not isinstance(audit, dict) or audit.get('complete') is not True or audit.get('uncertainties') != []
            or not isinstance(confirmed, list) or any(not isinstance(i, str) for i in confirmed)
            or Counter(confirmed) != Counter(room['id'] for room in inventory)):
        record['status'] = 'MAP_GRAPH_INCOMPLETE'
        record['completeness_errors'].append('image_completeness_uncertain')
        record['initial_status'] = record['status']
        return MapResult(description, None, record)
    merged, errors = maps.merge_inventory(inventory, audit.get('missing_locations'))
    record['map_phase1_missing_found'] = len(merged) - len(inventory)
    record['inventory'] = merged
    record['map_phase1_locations'] = len(merged)
    if errors:
        record['status'] = 'MAP_GRAPH_INCOMPLETE'
        record['graph_errors'] = errors
        record['initial_status'] = record['status']
        return MapResult(description, None, record)
    raw = _request(record, png, reserve, 'phase2_generation', _CONNECTIVITY_TOOL,
                   'Extract entry and connectivity ONLY using inventory IDs. NO new rooms. If another '
                   'visible location is noticed report missing_locations. Each edge needs type, compass, '
                   'visual_basis and specific image evidence: door/open_passage/stairs/one_way only. '
                   'Walls, adjacency, shared walls or proximity never establish traversal. Do not guess '
                   'whether a wall can be broken. Unmarked entry must be unresolved.', {'inventory': merged})
    connectivity, errors = maps.normalize_connectivity(raw)
    record['connectivity'] = connectivity
    if errors:
        record['status'] = 'MAP_GRAPH_INVALID' if isinstance(raw, dict) else 'MAP_ANALYSIS_FAILED'
        record['graph_errors'] = errors
        record['validation_errors'] = [e['code'] for e in errors]
        record['initial_status'] = record['status']
        return MapResult(description, None, record)
    record['map_phase2_edges'] = len(connectivity['edges'])
    graph = _build(record)
    record['initial_status'] = record['status']
    # A genuinely unresolved entry alone is not a request to invent one.
    repairable = any(e['code'] != 'unverified_entry' or connectivity.get('entry', {}).get('status') == 'resolved'
                     for e in record['graph_errors'])
    if repairable:
        patch = _request(record, png, reserve, 'targeted_repair', _PATCH_TOOL,
                         'Recheck ONLY listed errors against the original image and return a patch. '
                         'NO full rooms/graph regeneration; preserve unrelated evidence. Only add visible '
                         'missing locations. Replace/remove verified evidence only with fresh evidence AND '
                         'reason. Do not invent entry, passage, source transcription or story.',
                         {'inventory': merged, 'connectivity': connectivity, 'graph': graph,
                          'errors': record['graph_errors']}, graph)
        record['repair_attempts'] = sum(a['stage'] == 'targeted_repair' for a in record['attempts'])
        record['map_targeted_repairs'] = record['repair_attempts']
        record['targeted_patch'] = copy.deepcopy(patch)
        repaired_inventory, repaired_connectivity, errors = maps.apply_patch(
            merged, connectivity, patch, record['graph_errors'], verified_edges=set(), verified_entry=False)
        if errors:
            record['attempts'][-1]['validation_result'] = copy.deepcopy(errors)
            record['validation_errors'].extend(e['code'] for e in errors)
            return MapResult(description, None, record)
        assert isinstance(patch, dict)
        record['map_patch_add_locations'] = len(repaired_inventory) - len(merged)
        record['map_patch_remove_edges'] = len(patch['remove_edges'])
        record['map_patch_add_edges'] = len(patch['add_edges'])
        record['inventory'], record['connectivity'] = repaired_inventory, repaired_connectivity
        graph = _build(record)
    if record['graph_errors']:
        return MapResult(description, None, record)
    if _audit(record, png, graph, reserve):
        record.update({'status': 'MAP_GRAPH_VERIFIED', 'verified': True})
        if not record['repair_attempts']:
            record['initial_status'] = 'MAP_GRAPH_VERIFIED'
        return MapResult(description, graph, record)
    return MapResult(description, None, record)


def not_analyzed(*, candidate: bool = True) -> MapAnalysis:
    return {'version': VERSION, 'image_sha256': '', 'status': 'MAP_NOT_ANALYZED', 'initial_status': 'MAP_NOT_ANALYZED',
        'candidate': candidate, 'analysis_attempted': False, 'graph_generated': False, 'verified': False,
        'graph_sha256': '', 'candidate_graph': None, 'validation_errors': [], 'validation_details': [],
        'diagnostics': [], 'completeness_errors': [], 'repair_attempts': 0, 'attempts': [], 'image_evidence': [],
        'inventory': [], 'inventory_audit': {}, 'connectivity': {}, 'graph_errors': [], 'targeted_patch': None, 'entry_status': 'unresolved',
        'map_phase1_locations': 0, 'map_phase1_missing_found': 0, 'map_phase2_edges': 0,
        'map_targeted_repairs': 0, 'map_patch_add_locations': 0, 'map_patch_remove_edges': 0, 'map_patch_add_edges': 0}


def metrics(pages: list[dict]) -> dict[str, int]:
    records = [row['map_analysis'] for row in pages if row.get('map_analysis', {}).get('candidate')]
    result = {'map_candidates': len(records),
              'map_analysis_attempted': sum(r['analysis_attempted'] for r in records),
              'map_graph_generated': sum(r['graph_generated'] for r in records),
              'map_graph_repaired': sum(r['verified'] and r['repair_attempts'] > 0 for r in records)}
    for suffix, status in [('analysis_failed', 'MAP_ANALYSIS_FAILED'), ('graph_invalid', 'MAP_GRAPH_INVALID'),
                           ('graph_incomplete', 'MAP_GRAPH_INCOMPLETE'), ('graph_verified', 'MAP_GRAPH_VERIFIED')]:
        result['map_' + suffix] = sum(r['status'] == status for r in records)
    for key in ('map_phase1_locations', 'map_phase1_missing_found', 'map_phase2_edges', 'map_targeted_repairs',
                'map_patch_add_locations', 'map_patch_remove_edges', 'map_patch_add_edges'):
        result[key] = sum(r.get(key, 0) for r in records)
    return result


def publication_summary(analysis: dict) -> dict:
    """Expose feature diagnostics; raw image/provider evidence stays private."""
    public = copy.deepcopy(analysis)
    for key in ('candidate_graph', 'image_evidence', 'inventory', 'inventory_audit', 'connectivity', 'targeted_patch'):
        public.pop(key, None)
    for attempt in public.get('attempts', []):
        for key in ('output_graph', 'output_evidence', 'output_patch'):
            attempt.pop(key, None)
    return public
