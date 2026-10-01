"""Availability of immutable source routes, changed only by explicit KP confirmation.

Action/check/damage resolution stays in its existing workflows. This module
records a subsequent authorized ruling; narration cannot call it as a tool.
"""
from __future__ import annotations

from typing import Literal, TypedDict, cast

from app import locks, pdf_source_topology, scenario_library
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


def available_routes(state: GroupState, page: str) -> frozenset[str]:
    """Only current-timeline, graph-bound committed outcomes authorize traversal."""
    graph = state.scene_maps.get(page, {})
    if not pdf_source_topology.structurally_valid(graph):
        return frozenset()
    graph_digest = pdf_source_topology.route_hash(graph)
    available = set()
    for route in graph.get('source_topology', []):
        record = state.map_route_states.get(route['id'])
        if (isinstance(record, dict) and record.get('timeline_id') == state.timeline_id
                and record.get('map_key') == page and record.get('graph_sha256') == graph_digest
                and record.get('route_sha256') == pdf_source_topology.route_hash(route)
                and record.get('authority') == 'kp_ruling' and record.get('actor_id')
                and record.get('availability') == 'available'
                and isinstance(record.get('world_state'), dict)
                and record['world_state'].get(route['condition']['key']) is True):
            available.add(route['id'])
    return frozenset(available)


def commit_outcome(conversation_id: str, actor_id: str, page: str, route_id: str,
                   outcome: RouteOutcome, *, consequence: str = '') -> dict:
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
        graph = state.scene_maps.get(page)
        if not graph or not state.scenario_library_id:
            raise ValueError('A published source-certified map is required')
        # Revalidate source/certificate at this authority boundary, using full library source.
        current = scenario_library.load_context(state.scenario_library_id, state.active_chapter_id)['scene_maps'].get(page)
        if current != graph:
            raise ValueError('Map/source certificate changed; reload the scenario map')
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
