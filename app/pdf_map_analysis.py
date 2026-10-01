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
from app.providers.registry import analysis_provider

VERSION = 'image-map-certification-v1'
MapStatus = Literal['MAP_NOT_ANALYZED', 'MAP_ANALYSIS_FAILED', 'MAP_GRAPH_MISSING',
                    'MAP_GRAPH_INVALID', 'MAP_GRAPH_INCOMPLETE', 'MAP_GRAPH_VERIFIED', 'NOT_MAP']
MapStage = Literal['generation', 'repair', 'image_audit']


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
    evidence, attempts = analysis.get('image_evidence'), analysis.get('attempts')
    if (not isinstance(evidence, list) or not evidence or any(not isinstance(e, dict) for e in evidence)
            or not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], dict)):
        return False
    audit = attempts[-1]
    if (audit.get('stage') != 'image_audit' or audit.get('input_graph_sha256') != graph_hash(graph)
            or audit.get('image_sha256') != analysis.get('image_sha256')
            or audit.get('output_evidence') != evidence[-1]):
        return False
    return not _audit_errors(graph, evidence[-1], _visible_labels(evidence[:-1]))


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
            'basis': {'type': 'string', 'enum': ['door', 'passage', 'stairs', 'one_way', 'wall', 'adjacency', 'uncertain']},
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
    'An unmarked entry can have room_id="" and verdict=not_visible; never invent an entrance.\n'
    'Keep each evidence string under ten words; do not repeat graph descriptions.\n'
)


def _dispatch(record: MapAnalysis, png: bytes, reserve: Callable[[], bool], stage: MapStage,
              request: Callable[[], dict | None], graph: Any = None) -> dict | None:
    if not reserve():
        record['completeness_errors'].append('map_budget_exhausted')
        return None
    attempt: dict = {'attempt_number': len(record['attempts']) + 1, 'stage': stage,
               'provider': config.ANALYSIS_PROVIDER, 'image_sha256': hashlib.sha256(png).hexdigest(),
               'input_graph_sha256': graph_hash(graph) if graph is not None else None,
               'validation_errors': list(record['validation_errors']) + list(record['completeness_errors'])}
    record['attempts'].append(attempt)
    started = time.perf_counter()
    try:
        output = request()
        attempt['output_graph' if stage != 'image_audit' else 'output_evidence'] = copy.deepcopy(output)
        return output
    except Exception as error:  # noqa: BLE001 - failed import evidence remains private.
        attempt['failure'] = type(error).__name__
        return None
    finally:
        attempt['elapsed_seconds'] = round(time.perf_counter() - started, 3)


def _structural(record: MapAnalysis, graph: Any) -> None:
    checks = scene_map.inspect_scene_map(graph)
    record.update({'candidate_graph': copy.deepcopy(graph), 'graph_generated': True,
                   'graph_sha256': graph_hash(graph), 'validation_errors': [e['code'] for e in checks['errors']],
                   'validation_details': checks['errors'], 'diagnostics': checks['diagnostics']})
    record['status'] = 'MAP_GRAPH_INVALID' if checks['errors'] else 'MAP_GRAPH_INCOMPLETE'
    record['attempts'][-1]['validation_result'] = copy.deepcopy(checks)


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
    for label in prior_labels:
        if not any(_label_key(label) in _label_key(room['name']) for room in rooms.values()):
            errors.append('missing_prior_visible_location:' + label)
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
            if kind == 'edges' and row.get('basis') not in {'door', 'passage', 'stairs', 'one_way'}:
                errors.append('non_traversable_edge:' + row[key])
    entry = evidence.get('entry')
    if (not isinstance(entry, dict) or entry.get('room_id') != graph.get('entry_room_id', '')
            or not isinstance(entry.get('evidence'), str) or not entry['evidence'].strip()
            or entry.get('verdict') != ('supported' if graph.get('entry_room_id') else 'not_visible')):
        errors.append('image_entry_unverified')
    return list(dict.fromkeys(errors))


def _audit(record: MapAnalysis, png: bytes, graph: dict, reserve: Callable[[], bool]) -> bool:
    provider = analysis_provider()
    if provider is None:
        record['completeness_errors'] = ['image_audit_unavailable']
        return False
    edges = [{'edge_id': f'{room["id"]}:{index}', 'from': room['id'], **edge}
             for room in graph['rooms'] for index, edge in enumerate(room.get('exits', []))]
    prompt = _AUDIT_PROMPT + json.dumps({'graph': graph, 'edge_inventory': edges}, ensure_ascii=False)
    evidence = _dispatch(record, png, reserve, 'image_audit', lambda: provider.analyze_image(
        png, _AUDIT_TOOL, prompt, timeout=config.PDF_LAYOUT_IMAGE_TIMEOUT_SECONDS, max_retries=0), graph)
    if not isinstance(evidence, dict):
        record['completeness_errors'] = list(dict.fromkeys(record['completeness_errors'] + ['image_audit_unavailable']))
        return False
    prior_labels = _visible_labels(record['image_evidence'])
    record['image_evidence'].append(copy.deepcopy(evidence))
    record['completeness_errors'] = _audit_errors(graph, evidence, prior_labels)
    record['status'] = ('MAP_GRAPH_INVALID' if any(reason.startswith(('unsupported_', 'non_traversable_edge:'))
                                                 for reason in record['completeness_errors'])
                        else 'MAP_GRAPH_INCOMPLETE')
    record['attempts'][-1]['validation_result'] = {'completeness_errors': list(record['completeness_errors'])}
    return not record['completeness_errors']


def analyze(png: bytes, *, reserve: Callable[[], bool], candidate: bool = False) -> MapResult:
    record = not_analyzed(candidate=candidate)
    record['image_sha256'] = hashlib.sha256(png).hexdigest()
    response = _dispatch(record, png, reserve, 'generation', lambda: scene_map.request_page_image(png))
    record['analysis_attempted'] = bool(record['attempts'])
    if response is None:
        if record['analysis_attempted']:
            record['status'] = 'MAP_ANALYSIS_FAILED'
        record['initial_status'] = record['status']
        return MapResult('', None, record)
    raw_description = response.get('description')
    description = raw_description if isinstance(raw_description, str) else ''
    if response.get('page_type') != 'map':
        record['status'] = 'MAP_GRAPH_MISSING' if candidate else 'NOT_MAP'
        record['initial_status'] = record['status']
        return MapResult(description, None, record)
    record['candidate'] = True
    if not response.get('rooms'):
        record.update({'status': 'MAP_GRAPH_MISSING', 'candidate_graph': copy.deepcopy(response),
                       'initial_status': 'MAP_GRAPH_MISSING'})
        return MapResult(description, None, record)
    _structural(record, response)
    record['initial_status'] = record['status']
    if not record['validation_errors'] and _audit(record, png, response, reserve):
        record.update({'status': 'MAP_GRAPH_VERIFIED', 'initial_status': 'MAP_GRAPH_VERIFIED', 'verified': True})
        return MapResult(description, response, record)
    record['initial_status'] = record['status']
    if not record['validation_errors'] and any(reason in record['completeness_errors']
                                              for reason in ('map_budget_exhausted', 'image_audit_unavailable')):
        return MapResult(description, None, record)
    prompt = ('Repair ONLY the graph using the ORIGINAL IMAGE and the supplied validation/image-audit errors. '
              'Reinspect all floors/elevations. Do not change source transcription, invent rooms or passages, '
              'use scenario knowledge, or remove genuinely visible locations merely to satisfy validation. '
              'If an exit crosses a wall remove it; do not turn wall adjacency into a passage. '
              'Only add a missing room when clearly visible in the image.\n')
    prompt += json.dumps({'current_graph': response, 'validation_errors': record['validation_details'],
                          'image_errors': record['completeness_errors'], 'image_evidence': record['image_evidence']},
                         ensure_ascii=False)
    repaired = _dispatch(record, png, reserve, 'repair', lambda: scene_map.request_page_image(png, prompt), response)
    record['repair_attempts'] = sum(a['stage'] == 'repair' for a in record['attempts'])
    if not isinstance(repaired, dict):
        return MapResult(description, None, record)
    if repaired.get('page_type') != 'map':
        record['attempts'][-1]['failure'] = 'repair_output_not_map'
        return MapResult(description, None, record)
    _structural(record, repaired)
    if not record['validation_errors'] and _audit(record, png, repaired, reserve):
        record.update({'status': 'MAP_GRAPH_VERIFIED', 'verified': True})
        return MapResult(description, repaired, record)
    return MapResult(description, None, record)


def not_analyzed(*, candidate: bool = True) -> MapAnalysis:
    return {'version': VERSION, 'image_sha256': '', 'status': 'MAP_NOT_ANALYZED', 'initial_status': 'MAP_NOT_ANALYZED',
        'candidate': candidate, 'analysis_attempted': False, 'graph_generated': False, 'verified': False,
        'graph_sha256': '', 'candidate_graph': None, 'validation_errors': [], 'validation_details': [],
        'diagnostics': [], 'completeness_errors': [], 'repair_attempts': 0, 'attempts': [], 'image_evidence': []}


def metrics(pages: list[dict]) -> dict[str, int]:
    records = [row['map_analysis'] for row in pages if row.get('map_analysis', {}).get('candidate')]
    result = {'map_candidates': len(records),
              'map_analysis_attempted': sum(r['analysis_attempted'] for r in records),
              'map_graph_generated': sum(r['graph_generated'] for r in records),
              'map_graph_repaired': sum(r['verified'] and r['repair_attempts'] > 0 for r in records)}
    for suffix, status in [('analysis_failed', 'MAP_ANALYSIS_FAILED'), ('graph_invalid', 'MAP_GRAPH_INVALID'),
                           ('graph_incomplete', 'MAP_GRAPH_INCOMPLETE'), ('graph_verified', 'MAP_GRAPH_VERIFIED')]:
        result['map_' + suffix] = sum(r['status'] == status for r in records)
    return result
