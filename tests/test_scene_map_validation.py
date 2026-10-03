"""Generated and uploaded graphs share a total structural validator."""
import pytest

from app import scene_map


@pytest.mark.parametrize('graph', [
    {'rooms': [{'id': ['room'], 'name': 'Room'}]},
    {'rooms': [{'id': 'room', 'name': 'Room', 'exits': {}}]},
    {'rooms': [{'id': 'room', 'name': 'Room', 'exits': [{'to': [], 'compass': 'N'}]}]},
    {'rooms': [{'id': 'room', 'name': 'Room', 'exits': [{'to': 'room', 'compass': []}]}]},
    {'rooms': [{'id': 'room', 'name': 'Room', 'exits': [{'to': '2:room:extra', 'compass': 'N'}]}]},
    {'rooms': [{'id': 'invalid:local', 'name': 'Room'}]},
])
def test_malformed_graph_is_rejected_without_crashing(graph):
    assert scene_map.validate_scene_map(graph)


@pytest.mark.parametrize(('graph', 'code'), [
    ({'entry_room_id': 'missing', 'rooms': [{'id': 'room', 'name': 'Room'}]}, 'invalid_entry_room'),
    ({'rooms': [{'id': 'room', 'name': 'Room', 'exits': [{'to': 'missing', 'compass': 'N'}]}]}, 'dangling_exit'),
    ({'rooms': [{'id': 'room', 'name': 'Room'}, {'id': 'room', 'name': 'Other'}]}, 'duplicate_room_id'),
    ({'rooms': [{'id': 'room', 'name': 'Room', 'exits': [{'to': 'room', 'compass': 'north'}]}]}, 'invalid_compass'),
])
def test_structural_failures_have_distinct_machine_readable_codes(graph, code):
    assert code in [error['code'] for error in scene_map.inspect_scene_map(graph)['errors']]


def test_one_way_and_cross_map_exits_are_valid_and_asymmetry_is_only_diagnostic():
    graph = {'entry_room_id': 'a', 'rooms': [
        {'id': 'a', 'name': 'Entrance', 'exits': [{'to': 'b', 'compass': 'U'},
                                                {'to': 'custom_outside:gate', 'compass': 'E'}]},
        {'id': 'b', 'name': 'Stair landing', 'exits': []}]}
    checks = scene_map.inspect_scene_map(graph)
    assert checks['errors'] == []
    assert [d['code'] for d in checks['diagnostics']] == ['asymmetric_indoor_connection']


def test_edge_conflicts_duplicates_and_self_edges_are_diagnostics():
    graph = {'entry_room_id': 'a', 'rooms': [{'id': 'a', 'name': 'Room', 'exits': [
        {'to': 'a', 'compass': 'N'}, {'to': 'a', 'compass': 'N'}, {'to': 'a', 'compass': 'E'}]}]}
    checks = scene_map.inspect_scene_map(graph)
    assert checks['errors'] == []
    assert {'duplicate_directed_edge', 'self_edge', 'conflicting_compass'} <= {d['code'] for d in checks['diagnostics']}


def test_missing_entry_is_invalid_instead_of_inventing_an_entry():
    checks = scene_map.inspect_scene_map({'rooms': [{'id': 'room', 'name': 'Room'}]})
    assert [error['code'] for error in checks['errors']] == ['invalid_entry_room']
