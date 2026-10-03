"""Deterministic inventory, traversal graph and atomic error-scoped patches."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from typing import Any, Literal, TypedDict, cast, get_args

from app import scene_map

ErrorCode = Literal['missing_location', 'dangling_exit', 'unsupported_edge', 'unverified_entry',
                    'duplicate_room', 'invalid_compass', 'image_completeness_uncertain',
                    'malformed_inventory', 'malformed_connectivity', 'malformed_patch',
                    'out_of_scope_patch', 'verified_evidence_change']


class MapError(TypedDict):
    code: ErrorCode
    subject: str


class Location(TypedDict):
    id: str
    label: str
    floor_or_section: str
    original_labels: list[str]
    id_hints: list[str]
    evidence: str


TraversalKind = Literal['door', 'open_passage', 'stairs', 'one_way']
Compass = Literal['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S',
                  'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW', 'U', 'D']


class TraversalExit(TypedDict):
    to: str
    compass: Compass
    label: str
    type: TraversalKind
    visual_basis: TraversalKind
    evidence: str
    evidence_id: str


def traversal_kind(value: Any) -> TraversalKind | None:
    """Provider evidence must use the same closed kinds as the tool schema."""
    return cast(TraversalKind, value) if isinstance(value, str) and value in get_args(TraversalKind) else None


COMPASS_DIRECTIONS = get_args(Compass)
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def label_key(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def merge_inventory(existing: list[Location], additions: Any) -> tuple[list[Location], list[MapError]]:
    """Append visible locations; never remove/re-ID existing, verified locations."""
    result = copy.deepcopy(existing)
    errors: list[MapError] = []
    if not isinstance(additions, list):
        return result, [{'code': 'malformed_inventory', 'subject': 'locations'}]
    keys = {(label_key(room['label']), label_key(room['floor_or_section'])): room for room in result}
    for index, row in enumerate(additions):
        if not isinstance(row, dict):
            errors.append({'code': 'malformed_inventory', 'subject': str(index)})
            continue
        if row.get('kind') in ('furniture', 'object') or row.get('visible') is False:
            continue
        if (row.get('kind', 'location') not in ('location', 'room') or row.get('visible') is not True
                or any(not isinstance(row.get(key), str) or not row[key].strip()
                       for key in ('label', 'floor_or_section', 'evidence'))):
            errors.append({'code': 'malformed_inventory', 'subject': str(index)})
            continue
        label, section = row['label'], row['floor_or_section']
        key = (label_key(label), label_key(section))
        hint = row.get('id_hint', '')
        if not isinstance(hint, str):
            errors.append({'code': 'malformed_inventory', 'subject': str(index)})
            continue
        if key not in keys:
            identity = 'loc_' + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]
            if any(room['id'] == identity for room in result):
                errors.append({'code': 'duplicate_room', 'subject': identity})
                continue
            room: Location = {'id': identity, 'label': label, 'floor_or_section': section,
                              'original_labels': [], 'id_hints': [], 'evidence': row['evidence']}
            keys[key] = room
            result.append(room)
        room = keys[key]
        if label not in room['original_labels']:
            room['original_labels'].append(label)
        if hint and hint not in room['id_hints']:
            room['id_hints'].append(hint)
    return result, errors


def normalize_connectivity(raw: Any) -> tuple[dict, list[MapError]]:
    """Retain each proposed edge under a stable patch-addressable ID."""
    if not isinstance(raw, dict) or not isinstance(raw.get('edges'), list) or not isinstance(raw.get('entry'), dict):
        return {}, [{'code': 'malformed_connectivity', 'subject': 'connectivity'}]
    result = copy.deepcopy(raw)
    errors: list[MapError] = []
    ids = set()
    missing = result.get('missing_locations', [])
    if (any(key in raw for key in ('rooms', 'locations', 'exits', 'source_topology', 'source_evidence')) or not isinstance(missing, list)
            or any(not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                   for key in ('label', 'floor_or_section', 'evidence')) or row.get('visible') is not True for row in missing)):
        errors.append({'code': 'malformed_connectivity', 'subject': 'missing_locations'})
    result['missing_locations'] = missing if isinstance(missing, list) else []
    for index, edge in enumerate(result['edges']):
        if not isinstance(edge, dict):
            errors.append({'code': 'malformed_connectivity', 'subject': f'edge_{index + 1}'})
            continue
        edge.setdefault('id', f'edge_{index + 1}')
        if not isinstance(edge['id'], str) or not _ID.fullmatch(edge['id']) or edge['id'] in ids:
            errors.append({'code': 'malformed_connectivity', 'subject': f'edge_{index + 1}'})
            continue
        ids.add(edge['id'])
    return result, errors


def _resolve(reference: Any, inventory: list[Location]) -> str | None:
    if not isinstance(reference, str):
        return None
    exact = [room['id'] for room in inventory if reference == room['id']]
    if exact:
        return exact[0]
    hints = [room['id'] for room in inventory if reference in room['id_hints']]
    return hints[0] if len(hints) == 1 else None


def build_graph(inventory: list[Location], connectivity: dict) -> tuple[dict, list[MapError]]:
    """Provider supplies evidence, never canonical rooms/exits. No reciprocal guessing."""
    graph: dict[str, Any] = {'page_type': 'map', 'description': '', 'entry_room_id': '', 'rooms': [
        {'id': room['id'], 'name': room['label'] + ' [' + room['floor_or_section'] + ']',
         'visible_label': room['label'], 'floor_or_section': room['floor_or_section'],
         'description': room['evidence'], 'exits': []} for room in inventory]}
    rooms = {room['id']: room for room in graph['rooms']}
    errors: list[MapError] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in connectivity.get('edges', []):
        if not isinstance(edge, dict):
            errors.append({'code': 'malformed_connectivity', 'subject': 'edge'})
            continue
        subject = str(edge.get('id', 'edge'))
        origin, target = _resolve(edge.get('from'), inventory), _resolve(edge.get('to'), inventory)
        basis, kind = traversal_kind(edge.get('visual_basis')), traversal_kind(edge.get('type'))
        compass = edge.get('compass')
        if origin is None or target is None:
            errors.append({'code': 'dangling_exit', 'subject': subject})
        if (any(key in edge for key in ('authority', 'visibility', 'availability', 'source_evidence', 'condition'))
                or basis is None or kind != basis
                or not isinstance(edge.get('evidence'), str) or not edge['evidence'].strip()):
            errors.append({'code': 'unsupported_edge', 'subject': subject})
        if compass not in COMPASS_DIRECTIONS:
            errors.append({'code': 'invalid_compass', 'subject': subject})
        if any(error['subject'] == subject for error in errors):
            continue
        assert origin is not None and target is not None
        compass = cast(Compass, compass)
        key = (origin, target, compass)
        if key in seen or origin == target:
            errors.append({'code': 'unsupported_edge', 'subject': subject})
            continue
        seen.add(key)
        assert kind is not None and basis is not None
        exit_: TraversalExit = {'to': target, 'compass': cast(Compass, compass), 'label': edge['evidence'],
            'type': kind, 'visual_basis': basis, 'evidence': edge['evidence'], 'evidence_id': subject}
        rooms[origin]['exits'].append(exit_)
    entry = connectivity.get('entry', {})
    room_id = _resolve(entry.get('room_id'), inventory) if isinstance(entry, dict) else None
    if (not isinstance(entry, dict) or entry.get('status') != 'resolved' or room_id is None
            or not isinstance(entry.get('evidence'), str) or not entry['evidence'].strip()):
        errors.append({'code': 'unverified_entry', 'subject': 'entry'})
    else:
        graph['entry_room_id'] = room_id
    for location in connectivity.get('missing_locations', []):
        if isinstance(location, dict) and isinstance(location.get('label'), str):
            errors.append({'code': 'missing_location', 'subject': location['label']})
        else:
            errors.append({'code': 'malformed_connectivity', 'subject': 'missing_locations'})
    # Retain the existing validator; unresolved entry is explicitly incomplete evidence.
    checks = scene_map.inspect_scene_map(graph)
    for error in checks['errors']:
        if error['code'] == 'invalid_entry_room' and not graph['entry_room_id']:
            continue
        code: ErrorCode = 'invalid_compass' if error['code'] == 'invalid_compass' else 'malformed_connectivity'
        errors.append({'code': code, 'subject': error['message']})
    return graph, errors


def apply_patch(inventory: list[Location], connectivity: dict, patch: Any, errors: list[MapError], *,
                verified_edges: set[str], verified_entry: bool) -> tuple[list[Location], dict, list[MapError]]:
    """Apply one bounded patch atomically; no full graph replacement or room deletion."""
    rejected: list[MapError] = []
    allowed_keys = {'add_locations', 'remove_edges', 'add_edges', 'replace_edges', 'entry_update'}
    if (not isinstance(patch, dict) or set(patch) != allowed_keys
            or any(not isinstance(patch[key], list) for key in allowed_keys - {'entry_update'})):
        return inventory, connectivity, [{'code': 'malformed_patch', 'subject': 'patch'}]
    scoped_edges = {e['subject'] for e in errors if e['code'] in {'dangling_exit', 'unsupported_edge', 'invalid_compass'}}
    missing = {label_key(e['subject']) for e in errors if e['code'] == 'missing_location'}
    edges = {edge['id']: copy.deepcopy(edge) for edge in connectivity.get('edges', []) if isinstance(edge, dict) and isinstance(edge.get('id'), str)}
    dangling_refs = {edge.get('to') for key, edge in edges.items() if key in scoped_edges and isinstance(edge.get('to'), str)}
    for location in patch['add_locations']:
        if (not isinstance(location, dict) or not isinstance(location.get('label'), str)
                or (label_key(location['label']) not in missing and (not isinstance(location.get('id_hint'), str) or location['id_hint'] not in dangling_refs))):
            rejected.append({'code': 'out_of_scope_patch', 'subject': 'add_locations'})
    rooms, location_errors = merge_inventory(inventory, patch['add_locations'])
    rejected.extend(location_errors)
    added_ids = {room['id'] for room in rooms} - {room['id'] for room in inventory}

    def allowed_change(edge_id: Any, evidence: Any, reason: Any) -> bool:
        if not isinstance(edge_id, str) or edge_id not in edges or edge_id not in scoped_edges:
            rejected.append({'code': 'out_of_scope_patch', 'subject': str(edge_id)})
            return False
        if edge_id in verified_edges and (not isinstance(evidence, str) or not evidence.strip()
                or evidence == edges[edge_id].get('evidence') or not isinstance(reason, str) or not reason.strip()):
            rejected.append({'code': 'verified_evidence_change', 'subject': edge_id})
            return False
        return True

    changed = set()
    for removal in patch['remove_edges']:
        if (not isinstance(removal, dict) or any(not isinstance(removal.get(key), str) or not removal[key].strip()
                                                for key in ('edge_id', 'evidence', 'reason'))):
            rejected.append({'code': 'malformed_patch', 'subject': 'remove_edges'})
            continue
        edge_id, evidence, reason = removal['edge_id'], removal['evidence'], removal['reason']
        if allowed_change(edge_id, evidence, reason):
            changed.add(edge_id)
            del edges[edge_id]
    for replacement in patch['replace_edges']:
        if (not isinstance(replacement, dict) or not isinstance(replacement.get('edge'), dict)
                or not isinstance(replacement.get('reason'), str) or not replacement['reason'].strip()):
            rejected.append({'code': 'malformed_patch', 'subject': 'replace_edges'})
            continue
        edge_id, edge = replacement.get('edge_id'), copy.deepcopy(replacement['edge'])
        if allowed_change(edge_id, edge.get('evidence'), replacement.get('reason')):
            changed.add(edge_id)
            edge['id'] = edge_id
            edges[edge_id] = edge
    for edge in patch['add_edges']:
        if (not isinstance(edge, dict) or not isinstance(edge.get('id'), str) or not _ID.fullmatch(edge['id'])
                or edge['id'] in edges):
            rejected.append({'code': 'malformed_patch', 'subject': 'add_edges'})
            continue
        if (edge['id'] not in changed
                and _resolve(edge.get('from'), rooms) not in added_ids and _resolve(edge.get('to'), rooms) not in added_ids):
            rejected.append({'code': 'out_of_scope_patch', 'subject': edge['id']})
            continue
        edges[edge['id']] = copy.deepcopy(edge)
    result = copy.deepcopy(connectivity)
    result['edges'] = list(edges.values())
    update = patch['entry_update']
    if update is not None:
        if (not isinstance(update, dict) or not any(e['code'] == 'unverified_entry' for e in errors)):
            rejected.append({'code': 'out_of_scope_patch', 'subject': 'entry'})
        elif verified_entry and (not isinstance(update.get('reason'), str) or not update['reason'].strip()
                or update.get('evidence') == connectivity.get('entry', {}).get('evidence')):
            rejected.append({'code': 'verified_evidence_change', 'subject': 'entry'})
        else:
            result['entry'] = copy.deepcopy(update)
    # Newly noticed locations must now be present, not erased from the error list.
    remaining = []
    for location in connectivity.get('missing_locations', []):
        if (not isinstance(location, dict) or any(not isinstance(location.get(key), str) for key in ('label', 'floor_or_section')) or not any(label_key(room['label']) == label_key(location.get('label', ''))
                and label_key(room['floor_or_section']) == label_key(location.get('floor_or_section', '')) for room in rooms)):
            remaining.append(location)
    result['missing_locations'] = remaining
    _, build_errors = build_graph(rooms, result)
    rejected.extend(e for e in build_errors if e['code'] != 'unverified_entry')
    if rejected:
        return inventory, connectivity, rejected
    return rooms, result, []
