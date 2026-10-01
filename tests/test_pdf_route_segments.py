"""Synthetic canonical assertions: two walls must remain two transitions."""
from app import pdf_map_analysis, scene_map

SOURCE = ('--- 第 7 頁 ---\nA hidden route runs from Basement through breakable wall Wall A '
          'into an unnamed enterable space, then through breakable wall Wall B '
          'to Corbitt hiding place; it is necessary for progress.')


def chain_result(certified_map_result, source=SOURCE):
    visual = certified_map_result('', {'entry_room_id': 'basement', 'rooms': [
        {'id': 'basement', 'name': 'Basement', 'exits': []},
        {'id': 'hiding', 'name': 'Corbitt hiding place', 'exits': []}]}, image=b'map')
    return pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis, source)


def test_source_two_walls_certifies_ordered_segments_without_shortcut(certified_map_result):
    result = chain_result(certified_map_result)
    chain = result.graph['source_route_chains'][0]
    first, second = chain['segments']
    assert first['to'] == second['from']
    assert first['barrier_id'] != second['barrier_id']
    assert [edge['availability'] for edge in result.graph['source_topology']] == ['blocked', 'blocked']
    assert not any(edge['from'] == first['from'] and edge['to'] == second['to']
                   for edge in result.graph['source_topology'])
    transit = result.graph['source_transit_nodes'][0]
    assert transit['id'].startswith('source_transit_')
    assert transit['kind'] == 'source_transit' and transit['player_label'] == ''
    assert transit['source_evidence']['page'] == 7
    assert result.graph['rooms'][0]['exits'] == []
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'map', canonical_source=SOURCE)
    assert scene_map.validate_scene_map(result.graph) == []


def test_corbitt_two_wall_rulings_preserve_independent_state(monkeypatch, tmp_path):
    import pytest

    from app import map_routes
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    from tests.test_pdf_hidden_topology import published_barrier

    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text=SOURCE.split('\n', 1)[1])
    chain = graph['source_route_chains'][0]
    first, second = chain['segments']
    state = GroupState(group_id='two-walls', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph}, current_room_id={'player': first['from']}, current_map_page={'player': '1'})
    save_state(state)
    assert scene_map.visible_exits(graph, first['from']) == []
    assert not scene_map.resolve_source_route(graph, first['from'], second['to'])['ok']
    with pytest.raises(ValueError, match='reachable'):
        map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'discovered', barrier_id=second['barrier_id'])
    with pytest.raises(ValueError, match='discovered'):
        map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'opened', barrier_id=first['barrier_id'])
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'discovered', barrier_id=first['barrier_id'])
    fresh = load_state(state.group_id)
    assert map_routes.available_routes(fresh, '1') == frozenset()
    assert [b['barrier_id'] for b in map_routes.visible_barriers(fresh, '1', first['from'])] == [first['barrier_id']]
    assert scene_map.visible_exits(graph, first['from']) == []
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'opened', barrier_id=first['barrier_id'])
    fresh = load_state(state.group_id)
    allowed = map_routes.available_routes(fresh, '1')
    assert allowed == frozenset({first['id']})
    assert scene_map.resolve_source_route(graph, first['from'], first['to'], available_routes=allowed)['ok']
    assert not scene_map.resolve_source_route(graph, second['from'], second['to'], available_routes=allowed)['ok']
    assert map_routes.route_progress(fresh, '1', chain['id']) == 1
    assert map_routes.visible_barriers(fresh, '1', second['from']) == []
    with pytest.raises(ValueError, match='reachable'):
        map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'discovered', barrier_id=second['barrier_id'])
    fresh.current_room_id['player'] = first['to']
    save_state(fresh)
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'discovered', barrier_id=second['barrier_id'])
    result = map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'failed',
        barrier_id=second['barrier_id'], consequence='noise')
    assert result['retryable'] and result['availability'] == 'blocked'
    fresh = load_state(state.group_id)
    assert map_routes.available_routes(fresh, '1') == allowed
    assert fresh.map_route_states[first['id']]['state'] == 'opened'
    assert fresh.map_route_states[second['id']]['state'] == 'blocked'
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'opened', barrier_id=second['barrier_id'])
    fresh = load_state(state.group_id)
    assert map_routes.route_progress(fresh, '1', chain['id']) == 2
    assert scene_map.resolve_source_route(graph, second['from'], second['to'],
        available_routes=map_routes.available_routes(fresh, '1'))['ok']


def test_segment_command_targets_one_barrier_and_preserves_legacy_syntax(monkeypatch, tmp_path):
    import asyncio

    from app import map_routes
    from app.commands.handlers.map_handler import handle_map_command
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    from tests.test_pdf_hidden_topology import published_barrier

    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text=SOURCE.split('\n', 1)[1].replace('hidden route', 'route'))
    chain = graph['source_route_chains'][0]
    first, second = chain['segments']
    state = GroupState(group_id='segment-command', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph}, current_room_id={'player': first['from']}, current_map_page={'player': '1'})
    save_state(state)
    replies = []
    async def reply(message):
        replies.append(message)
    async def image(*_):
        raise AssertionError('No image call')
    assert asyncio.run(handle_map_command(state.group_id, 'kp', reply, image,
        ['coc', 'route', '1', chain['id'], first['barrier_id'], 'opened']))
    assert map_routes.available_routes(load_state(state.group_id), '1') == frozenset({first['id']})
    assert not asyncio.run(handle_map_command(state.group_id, 'kp', reply, image,
        ['coc', 'route', '1', chain['id'], second['barrier_id'], 'opened']))
    assert not asyncio.run(handle_map_command(state.group_id, 'player', reply, image,
        ['coc', 'route', '1', chain['id'], first['barrier_id'], 'opened']))


def test_transit_requires_explicit_enterable_source_and_never_invents_a_name(certified_map_result):
    import pytest

    from app import pdf_source_topology
    result = chain_result(certified_map_result)
    assert result.graph['source_transit_nodes'] == chain_result(certified_map_result).graph['source_transit_nodes']
    for text in (SOURCE.replace('an unnamed enterable space', 'adjacent walls'),
                 SOURCE.replace('A hidden route', 'Perhaps a hidden route'),
                 SOURCE.replace('A hidden route', 'There is no hidden route')):
        rejected = chain_result(certified_map_result, text)
        assert not (rejected.graph or {}).get('source_transit_nodes')
    transit = result.graph['source_transit_nodes'][0]
    room = scene_map.get_room(result.graph, transit['id'])
    assert room['name'] == '' and room['player_label'] == ''
    assert scene_map.find_room_by_text(result.graph, transit['id']) is None
    broken = dict(result.graph, source_transit_nodes=[{'id': 'not-source-evidenced'}])
    assert not pdf_source_topology.structurally_valid(broken)
    with pytest.raises(ValueError):
        pdf_map_analysis.certify_source_topology(result.graph, result.analysis, SOURCE)


def test_named_canonical_intermediate_uses_existing_location(certified_map_result):
    visual = certified_map_result('', {'entry_room_id': 'basement', 'rooms': [
        {'id': 'basement', 'name': 'Basement', 'exits': []},
        {'id': 'cavity', 'name': 'Cavity', 'exits': []},
        {'id': 'hiding', 'name': 'Corbitt hiding place', 'exits': []}]})
    source = SOURCE.replace('an unnamed enterable space', 'Cavity')
    result = pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis, source)
    assert result.graph['source_route_chains'][0]['segments'][0]['to'] == visual.graph['rooms'][1]['id']
    assert not result.graph.get('source_transit_nodes')
    assert result.graph['rooms'] == visual.graph['rooms']


def test_order_and_transit_evidence_tampering_invalidate_certificate(certified_map_result):
    import copy

    result = chain_result(certified_map_result)
    for change in ('order', 'barrier_count', 'transit_evidence', 'source_span'):
        graph = copy.deepcopy(result.graph)
        if change == 'order':
            graph['source_route_chains'][0]['segments'].reverse()
        elif change == 'barrier_count':
            graph['source_route_chains'][0]['barriers'].pop()
        elif change == 'transit_evidence':
            graph['source_transit_nodes'][0]['source_evidence']['page'] = 9
        else:
            graph['source_topology'][0]['source_evidence']['span_start'] += 1
        assert not pdf_map_analysis.verified_graph(graph, result.analysis, canonical_source=SOURCE)
    restored = pdf_map_analysis.reusable_visual_graph(result.graph, result.analysis, b'map')
    assert restored is not None
    assert not any(k.startswith('source_') for k in restored.graph)
    assert pdf_map_analysis.verified_graph(restored.graph, restored.analysis, b'map')


def test_three_barriers_preserve_order_and_no_transit_without_enterable_assertion(certified_map_result):
    source = SOURCE.replace(', then through breakable wall Wall B',
        ', then through sealed door Door C into an unnamed enterable space, then through breakable wall Wall B')
    result = chain_result(certified_map_result, source)
    assert len(result.graph['source_route_chains'][0]['segments']) == 3
    assert len(result.graph['source_transit_nodes']) == 2
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=source)


def test_persisted_barriers_are_bound_to_timeline_graph_route_barrier_and_source(monkeypatch, tmp_path):
    import copy

    from app import map_routes
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    from tests.test_pdf_hidden_topology import published_barrier
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text=SOURCE.split('\n', 1)[1].replace('hidden route', 'route'))
    chain = graph['source_route_chains'][0]
    first = chain['segments'][0]
    state = GroupState(group_id='bound-barrier', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph}, current_room_id={'player': first['from']}, current_map_page={'player': '1'})
    save_state(state)
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'opened', barrier_id=first['barrier_id'])
    state = load_state(state.group_id)
    assert map_routes.available_routes(state, '1') == frozenset({first['id']})
    for field in ('timeline_id', 'map_key', 'graph_sha256', 'route_sha256', 'route_id', 'barrier_id', 'source_sha256'):
        tampered = copy.deepcopy(state)
        tampered.map_route_states[first['id']][field] = 'changed'
        assert not map_routes.available_routes(tampered, '1'), field
    with_changed_source = copy.deepcopy(state)
    with_changed_source.scene_maps['1']['source_route_chains'][0]['source_evidence']['span_start'] += 1
    assert not map_routes.available_routes(with_changed_source, '1')


def test_visible_beacon_stairs_have_no_conditional_overlay(certified_map_result):
    visual = certified_map_result('', {'entry_room_id': 'hall', 'rooms': [
        {'id': 'hall', 'name': 'Hall', 'exits': [{'to': 'lamp', 'compass': 'U', 'label': 'stairs'}]},
        {'id': 'lamp', 'name': 'Lamp Room', 'exits': [{'to': 'hall', 'compass': 'D', 'label': 'stairs'}]}]})
    result = pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis,
        '--- 第 1 頁 ---\nThe stairs ascend to the lamp room.')
    assert result.graph == visual.graph
    assert scene_map.resolve_move({'1': result.graph}, '1', result.graph['entry_room_id'], 'N', 'up')['ok']
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis)


def test_unnamed_transit_movement_uses_only_current_open_segment(monkeypatch, tmp_path):
    import asyncio

    from app import map_routes
    from app.commands.handlers.map_handler import handle_map_command
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    from tests.test_pdf_hidden_topology import published_barrier
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text=SOURCE.split('\n', 1)[1].replace('hidden route', 'route'))
    chain = graph['source_route_chains'][0]
    first, second = chain['segments']
    state = GroupState(group_id='transit-move', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph}, current_room_id={'player': first['from']}, current_map_page={'player': '1'})
    save_state(state)
    replies = []
    async def reply(message):
        replies.append(message)
    async def image(*_):
        raise AssertionError('No image call')
    def command(*parts):
        return asyncio.run(handle_map_command(state.group_id, 'player', reply, image, ['coc', *parts]))
    assert not command('traverse', first['id'])
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'opened', barrier_id=first['barrier_id'])
    assert command('where')
    assert '/coc traverse ' + first['id'] in replies[-1]
    assert second['id'] not in replies[-1] and second['to'] not in replies[-1]
    assert not command('traverse', second['id'])
    assert command('traverse', first['id'])
    assert load_state(state.group_id).current_room_id['player'] == first['to']
    assert not command('traverse', second['id'])
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'opened', barrier_id=second['barrier_id'])
    assert command('traverse', second['id'])
    assert load_state(state.group_id).current_room_id['player'] == second['to']


def test_malformed_source_overlays_fail_closed_without_crashing(certified_map_result):
    import copy

    from app import pdf_source_topology
    result = chain_result(certified_map_result)
    for field, value in (('from', []), ('to', {}), ('source_evidence', None)):
        graph = copy.deepcopy(result.graph)
        graph['source_topology'][0][field] = value
        assert not pdf_source_topology.structurally_valid(graph)
        assert not pdf_map_analysis.verified_graph(graph, result.analysis, canonical_source=SOURCE)


def test_hidden_discovery_does_not_expose_deeper_chain_in_where(monkeypatch, tmp_path):
    import asyncio

    from app import map_routes
    from app.commands.handlers.map_handler import handle_map_command
    from app.models import GroupState
    from app.repositories.group_state import save_state
    from tests.test_pdf_hidden_topology import published_barrier
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text=SOURCE.split('\n', 1)[1])
    chain = graph['source_route_chains'][0]
    first, second = chain['segments']
    state = GroupState(group_id='hidden-chain-public', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph}, current_room_id={'player': first['from']}, current_map_page={'player': '1'})
    save_state(state)
    replies = []
    async def reply(message):
        replies.append(message)
    async def image(*_):
        raise AssertionError('No image call')
    def where():
        assert asyncio.run(handle_map_command(state.group_id, 'player', reply, image, ['coc', 'where']))
        return replies[-1]
    assert '障礙' not in where()
    map_routes.commit_outcome(state.group_id, 'kp', '1', chain['id'], 'discovered', barrier_id=first['barrier_id'])
    public = where()
    assert '障礙' in public
    assert 'traverse' not in public
    assert second['barrier_id'] not in public and first['to'] not in public and 'Corbitt' not in public


def test_segment_identity_cannot_bypass_barrier_command(monkeypatch, tmp_path):
    import pytest

    from app import map_routes
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    from tests.test_pdf_hidden_topology import published_barrier
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text=SOURCE.split('\n', 1)[1])
    segment = graph['source_route_chains'][0]['segments'][0]
    state = GroupState(group_id='no-segment-bypass', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph})
    save_state(state)
    with pytest.raises(ValueError, match='route and barrier'):
        map_routes.commit_outcome(state.group_id, 'kp', '1', segment['id'], 'opened')
    assert load_state(state.group_id).map_route_states == {}


def test_all_open_still_requires_sequential_movement(certified_map_result):
    result = chain_result(certified_map_result)
    first, second = result.graph['source_route_chains'][0]['segments']
    allowed = frozenset({first['id'], second['id']})
    assert scene_map.resolve_source_route(result.graph, first['from'], first['to'], available_routes=allowed)['ok']
    assert scene_map.resolve_source_route(result.graph, second['from'], second['to'], available_routes=allowed)['ok']
    assert not scene_map.resolve_source_route(result.graph, first['from'], second['to'], available_routes=allowed)['ok']
    exits = scene_map.visible_exits(result.graph, first['from'], available_routes=allowed)
    assert [e['to'] for e in exits] == [first['to']]
