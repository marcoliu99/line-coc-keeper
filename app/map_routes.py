"""Availability of immutable source routes, changed only by explicit KP confirmation.

Action/check/damage resolution stays in its existing workflows. This module
records a subsequent authorized ruling; narration cannot call it as a tool.
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict, cast

from app import locks, pdf_source_topology, scenario_activation, scene_map
from app.commands import permissions
from app.models import GroupState
from app.repositories.group_state import load_state, save_state
from app.services import mutation_admission

RouteOutcome = Literal['opened', 'discovered', 'failed']


class RouteRuntimeState(TypedDict):
    authority: Literal['kp_ruling']
    actor_id: str
    timeline_id: str
    map_key: str
    graph_sha256: str
    route_sha256: str
    availability: pdf_source_topology.Availability
    world_state: dict[str, bool]
    attempts: int
    last_outcome: RouteOutcome
    consequence: str
    route_id: NotRequired[str]
    barrier_id: NotRequired[str]
    source_sha256: NotRequired[str]
    state: NotRequired[Literal['blocked', 'opened']]
    discovered: NotRequired[bool]


def _current_record(state: GroupState, page: str, route: dict, *, chain: dict | None = None,
                    segment: dict | None = None) -> dict:
    record = state.map_route_states.get(route['id'], {})
    graph = state.scene_maps.get(page, {})
    if (not isinstance(record, dict) or record.get('timeline_id') != state.timeline_id
            or record.get('map_key') != page or record.get('graph_sha256') != pdf_source_topology.route_hash(graph)
            or record.get('route_sha256') != pdf_source_topology.route_hash(route)
            or record.get('authority') != 'kp_ruling' or not record.get('actor_id')):
        return {}
    if (chain is not None and segment is not None
            and (record.get('route_id') != chain['id'] or record.get('barrier_id') != segment['barrier_id']
                 or record.get('source_sha256') != chain['source_evidence']['canonical_source_sha256'])):
        return {}
    return record


def available_routes(state: GroupState, page: str) -> frozenset[str]:
    """Only current-timeline, graph-bound outcomes authorize individual traversal."""
    graph = state.scene_maps.get(page, {})
    if not pdf_source_topology.structurally_valid(graph):
        return frozenset()
    chain_segments = {s['id']: (c, s) for c in graph.get('source_route_chains', []) for s in c['segments']}
    available = set()
    for route in graph.get('source_topology', []):
        chain, segment = chain_segments.get(route['id'], (None, None))
        record = _current_record(state, page, route, chain=chain, segment=segment)
        if (record.get('availability') == 'available' and isinstance(record.get('world_state'), dict)
                and record['world_state'].get(route['condition']['key']) is True
                and (chain is None or record.get('state') == 'opened')):
            available.add(route['id'])
    # Receipts for a later segment cannot bypass a missing or incompatible prefix.
    for chain in graph.get('source_route_chains', []):
        prefix_open = True
        for segment in chain['segments']:
            prefix_open = prefix_open and segment['id'] in available
            if not prefix_open:
                available.discard(segment['id'])
    return frozenset(available)


def route_progress(state: GroupState, page: str, route_id: str) -> int:
    """Derived consecutive opened prefix, never authoritative state."""
    allowed = available_routes(state, page)
    chain = next((c for c in state.scene_maps.get(page, {}).get('source_route_chains', []) if c['id'] == route_id), None)
    if chain is None:
        return 0
    progress = 0
    for segment in chain['segments']:
        if segment['id'] not in allowed:
            break
        progress += 1
    return progress


def visible_barriers(state: GroupState, page: str, room_id: str) -> list[dict]:
    """Only the reached layer's independently discovered barrier is public."""
    graph = state.scene_maps.get(page, {})
    if not pdf_source_topology.structurally_valid(graph):
        return []
    allowed, result = available_routes(state, page), []
    for chain in graph.get('source_route_chains', []):
        for index, segment in enumerate(chain['segments']):
            if segment['from'] != room_id or any(s['id'] not in allowed for s in chain['segments'][:index]):
                continue
            edge = next(r for r in graph['source_topology'] if r['id'] == segment['id'])
            record = _current_record(state, page, edge, chain=chain, segment=segment)
            barrier = chain['barriers'][index]
            if barrier['visibility'] == 'hidden' and record.get('discovered') is not True:
                continue
            result.append({'route_id': chain['id'], 'barrier_id': segment['barrier_id'],
                           'state': 'opened' if segment['id'] in allowed else 'blocked',
                           'progression_policy': barrier['progression_policy']})
    return result


def _published_graph(state: GroupState, page: str) -> dict:
    graph = state.scene_maps.get(page)
    if not graph or not state.scenario_library_id or not pdf_source_topology.structurally_valid(graph):
        raise ValueError('A published source-certified map is required')
    # Replay full published source/certificate at both authority boundaries.
    current = scenario_activation.load_state_context(state)['scene_maps'].get(page)
    if current != graph:
        raise ValueError('Map/source certificate changed; reload the scenario map')
    return graph


def commit_outcome(conversation_id: str, actor_id: str, page: str, route_id: str,
                   outcome: RouteOutcome, *, consequence: str = '', barrier_id: str | None = None) -> dict:
    """Persist a KP's explicit post-action ruling, never infer success from prose.

    Failed actions leave blocked/undiscovered availability unchanged and retryable.
    No skill difficulty, damage, cost or consequence is invented here.
    """
    mutation_admission.assert_admitted(conversation_id)
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        if not permissions.is_kp(state, actor_id):
            raise PermissionError(permissions.kp_only('確認地圖路線狀態'))
        if outcome not in ('opened', 'discovered', 'failed'):
            raise ValueError('Invalid route outcome')
        graph = _published_graph(state, page)
        chain = next((c for c in graph.get('source_route_chains', []) if c['id'] == route_id), None)
        if chain is not None:
            return _commit_barrier(state, actor_id, page, graph, chain, barrier_id, outcome, consequence)
        if barrier_id is not None or any(s['id'] == route_id for c in graph.get('source_route_chains', []) for s in c['segments']):
            raise ValueError('An ordered route requires its route and barrier identities')
        route = next((r for r in graph.get('source_topology', []) if r['id'] == route_id), None)
        if route is None:
            raise ValueError('Source-backed route not found')
        discovery = route['availability'] == 'undiscovered'
        if outcome != 'failed' and outcome != ('discovered' if discovery else 'opened'):
            raise ValueError('Use discovery for hidden routes and opening for blocked barriers')
        was_available = route_id in available_routes(state, page)
        attempts = 0
        availability: pdf_source_topology.Availability = ('available' if was_available or outcome != 'failed'
            else cast(pdf_source_topology.Availability, route['availability']))
        # Explicit two-way source routes share a condition; one barrier action opens both.
        for related in graph['source_topology']:
            if related['condition']['key'] != route['condition']['key']:
                continue
            previous = state.map_route_states.get(related['id'], {})
            valid_previous = (isinstance(previous, dict) and previous.get('timeline_id') == state.timeline_id
                              and previous.get('graph_sha256') == pdf_source_topology.route_hash(graph))
            attempts = previous.get('attempts', 0) if valid_previous else 0
            if type(attempts) is not int or attempts < 0:
                attempts = 0
            record: RouteRuntimeState = {'authority': 'kp_ruling', 'actor_id': actor_id, 'timeline_id': state.timeline_id,
                'map_key': page, 'graph_sha256': pdf_source_topology.route_hash(graph),
                'route_sha256': pdf_source_topology.route_hash(related), 'availability': availability,
                'world_state': {related['condition']['key']: availability == 'available'},
                'attempts': attempts + 1, 'last_outcome': outcome, 'consequence': consequence[:600]}
            state.map_route_states[related['id']] = dict(record)
        save_state(state, reason='map_route_ruling')
        return {'availability': availability, 'retryable': True, 'attempts': state.map_route_states[route_id]['attempts']}


def _commit_barrier(state: GroupState, actor_id: str, page: str, graph: dict, chain: dict,
                    barrier_id: str | None, outcome: RouteOutcome, consequence: str) -> dict:
    segment = next((s for s in chain['segments'] if s['barrier_id'] == barrier_id), None)
    if segment is None:
        raise ValueError('Source-backed barrier not found')
    index = chain['segments'].index(segment)
    allowed = available_routes(state, page)
    if (any(s['id'] not in allowed for s in chain['segments'][:index])
            or not any(state.current_map_page.get(player) == page and location == segment['from']
                       for player, location in state.current_room_id.items())):
        raise ValueError('The barrier must be currently reachable from the party location')
    edge = next(r for r in graph['source_topology'] if r['id'] == segment['id'])
    previous = _current_record(state, page, edge, chain=chain, segment=segment)
    discovered = previous.get('discovered') is True or edge['visibility'] == 'visible'
    if outcome == 'opened' and not discovered:
        raise ValueError('A hidden barrier must first be independently discovered')
    opened = segment['id'] in allowed or outcome == 'opened'
    attempts = previous.get('attempts', 0)
    if type(attempts) is not int or attempts < 0:
        attempts = 0
    availability: pdf_source_topology.Availability = 'available' if opened else 'blocked'
    record: RouteRuntimeState = {'authority': 'kp_ruling', 'actor_id': actor_id, 'timeline_id': state.timeline_id,
        'map_key': page, 'graph_sha256': pdf_source_topology.route_hash(graph),
        'route_sha256': pdf_source_topology.route_hash(edge), 'route_id': chain['id'],
        'barrier_id': segment['barrier_id'], 'source_sha256': chain['source_evidence']['canonical_source_sha256'],
        'state': 'opened' if opened else 'blocked', 'discovered': discovered or outcome == 'discovered',
        'availability': availability, 'world_state': {edge['condition']['key']: opened},
        'attempts': attempts + (outcome != 'discovered'), 'last_outcome': outcome,
        'consequence': consequence[:600]}
    state.map_route_states[segment['id']] = dict(record)
    save_state(state, reason='map_barrier_ruling')
    return {'availability': availability, 'retryable': True, 'attempts': record['attempts']}


def traverse_segment(conversation_id: str, actor_id: str, segment_id: str) -> dict:
    """Move through one already opened source segment without inventing a name/compass."""
    mutation_admission.assert_admitted(conversation_id)
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        page, origin = state.current_map_page.get(actor_id, ''), state.current_room_id.get(actor_id, '')
        graph = _published_graph(state, page)
        segment = next((s for c in graph.get('source_route_chains', []) for s in c['segments']
                        if s['id'] == segment_id and s['from'] == origin), None)
        allowed = available_routes(state, page)
        if (segment is None or segment_id not in allowed
                or not scene_map.resolve_source_route(graph, origin, segment['to'], available_routes=allowed)['ok']):
            raise ValueError('Only a current, opened segment is traversable')
        room = scene_map.get_room(graph, segment['to'])
        if room is None:
            raise ValueError('Source segment destination is missing')
        state.current_room_id[actor_id] = segment['to']
        save_state(state, reason='map_segment_movement')
        return room
