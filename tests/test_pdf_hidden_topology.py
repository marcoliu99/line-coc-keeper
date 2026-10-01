"""Canonical source may add hidden topology, never an ordinary visual exit."""
import copy

from app import pdf_map_analysis, scene_map

SOURCE = '--- 第 7 頁 ---\nA hidden passage connects Basement to Corbitt hiding place.'


def visual_result(certified_map_result):
    return certified_map_result('', {'entry_room_id': 'basement', 'rooms': [
        {'id': 'basement', 'name': 'Basement', 'exits': []},
        {'id': 'hiding_place', 'name': 'Corbitt hiding place', 'exits': []}]}, image=b'map')


def test_source_backed_hidden_route_keeps_visual_proof_and_is_not_an_exit(certified_map_result):
    original = visual_result(certified_map_result)
    result = pdf_map_analysis.certify_source_topology(original.graph, original.analysis, SOURCE)
    route = result.graph['source_topology'][0]
    basement, hiding_place = [room['id'] for room in original.graph['rooms']]
    assert (route['from'], route['to'], route['type']) == (basement, hiding_place, 'hidden_passage')
    assert (route['authority'], route['visibility'], route['availability']) == ('scenario_source', 'hidden', 'undiscovered')
    assert route['source_evidence']['page'] == 7
    assert result.graph['rooms'] == original.graph['rooms']
    assert scene_map.get_room(result.graph, basement)['exits'] == []
    assert not scene_map.resolve_move({'7': result.graph}, '7', basement, 'N', 'right')['ok']
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'map', canonical_source=SOURCE)
    assert not pdf_map_analysis.verified_graph(result.graph, result.analysis, b'map')
    assert not pdf_map_analysis.verified_graph(result.graph, result.analysis, b'map', canonical_source=SOURCE + ' changed')
    assert pdf_map_analysis.verified_graph(original.graph, original.analysis, b'map')
    assert 'source_topology' not in original.graph


def test_hidden_proof_cannot_be_forged_by_changing_hashes(certified_map_result):
    original = visual_result(certified_map_result)
    result = pdf_map_analysis.certify_source_topology(original.graph, original.analysis, SOURCE)
    graph, analysis = copy.deepcopy(result.graph), copy.deepcopy(result.analysis)
    graph['source_topology'][0]['source_evidence']['span_start'] += 1
    analysis['graph_sha256'] = pdf_map_analysis.graph_hash(graph)
    analysis['source_topology_certificate']['merged_graph_sha256'] = analysis['graph_sha256']
    analysis['source_topology_certificate']['hidden_topology_sha256'] = pdf_map_analysis.graph_hash(graph['source_topology'])
    assert not pdf_map_analysis.verified_graph(graph, analysis, canonical_source=SOURCE)


def test_breakable_wall_is_blocked_source_backed_and_has_no_invented_difficulty(certified_map_result):
    original = visual_result(certified_map_result)
    source = '--- 第 7 頁 ---\nA breakable wall connects Basement to Corbitt hiding place; it is necessary for progress.'
    result = pdf_map_analysis.certify_source_topology(original.graph, original.analysis, source)
    route = result.graph['source_topology'][0]
    assert route['type'] == 'breakable_wall'
    assert route['visibility'] == 'visible'
    assert route['availability'] == 'blocked'
    assert route['progression_policy'] == 'fail_forward'
    assert route['condition']['kind'] == 'world_state'
    assert route['condition']['expected'] is True
    assert not {'difficulty', 'hp', 'armor', 'tool'} & route.keys()
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=source)


def test_shared_wall_and_speculation_cannot_create_source_topology(certified_map_result):
    original = visual_result(certified_map_result)
    for text in ('Basement shares a wall with Corbitt hiding place.',
                 'Perhaps a hidden passage connects Basement to Corbitt hiding place.',
                 'There is no secret door from Basement to Corbitt hiding place.'):
        result = pdf_map_analysis.certify_source_topology(original.graph, original.analysis,
            '--- 第 7 頁 ---\n' + text)
        assert not result.graph.get('source_topology')


def test_unproven_hidden_routes_and_hidden_exits_are_structurally_rejected(certified_map_result):
    graph = copy.deepcopy(visual_result(certified_map_result).graph)
    graph['source_topology'] = [{'from': graph['rooms'][0]['id'], 'to': graph['rooms'][1]['id'],
                                'type': 'secret_door', 'authority': 'scenario_source'}]
    assert scene_map.validate_scene_map(graph)
    graph.pop('source_topology')
    graph['rooms'][0]['exits'] = [{'to': graph['rooms'][1]['id'], 'compass': 'E',
                                 'authority': 'scenario_source', 'visibility': 'hidden'}]
    assert scene_map.validate_scene_map(graph)
    assert scene_map.visible_exits(graph, graph['rooms'][0]['id']) == []
    assert not scene_map.resolve_move({'7': graph}, '7', graph['rooms'][0]['id'], 'N', 'right')['ok']


def install_wall_map_provider(monkeypatch, *, visual_door=False):
    """Stub only image inference; run production inventory/audit/build/certificate."""
    import json
    from types import SimpleNamespace

    from app import config
    from app.providers import registry

    calls = []
    def analyze(_image, tool, prompt, **_options):
        stage = tool['name']
        calls.append(stage)
        if stage == 'inventory_map_locations':
            return {'page_type': 'map', 'description': 'Two rooms separated by a solid wall.', 'locations': [
                {'id_hint': name, 'label': name, 'floor_or_section': 'basement', 'visible': True,
                 'kind': 'location', 'evidence': 'Visible label'} for name in ('Basement', 'Corbitt hiding place')]}
        payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
        if stage == 'audit_map_inventory':
            return {'complete': True, 'uncertainties': [], 'missing_locations': [],
                    'confirmed_ids': [r['id'] for r in payload['inventory']]}
        if stage == 'extract_map_connectivity':
            return {'entry': {'status': 'resolved', 'room_id': payload['inventory'][0]['id'], 'evidence': 'Visible entrance'},
                    'edges': ([{'id': 'door', 'from': payload['inventory'][0]['id'], 'to': payload['inventory'][1]['id'],
                                'type': 'door', 'visual_basis': 'door', 'compass': 'E', 'evidence': 'Visible door'}]
                              if visual_door else []), 'missing_locations': []}
        assert stage == 'audit_scene_map_image'
        # Hidden routes are not subject to an image traversal verdict.
        graph = payload['graph']
        assert 'source_topology' not in graph
        return {'complete': True, 'uncertainties': [],
            'visible_locations': [{'label': r['visible_label'], 'room_id': r['id']} for r in graph['rooms']],
            'rooms': [{'room_id': r['id'], 'verdict': 'supported', 'evidence': 'Room visible with a door' if visual_door else 'Room visible; shared solid wall has no door'}
                      for r in graph['rooms']],
            'edges': [{'edge_id': r['id'] + ':' + str(i), 'verdict': 'supported', 'basis': 'door', 'evidence': 'Visible door'}
                      for r in graph['rooms'] for i, _ in enumerate(r['exits'])],
            'entry': {'room_id': graph['entry_room_id'], 'verdict': 'supported', 'evidence': 'Visible entrance'}}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=analyze))
    return calls


def source_pdf(text):
    import pymupdf
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox((30, 30, 550, 690), 'FLOOR PLAN.\n' + text + '\n\n' + 'Safe native narrative source. ' * 15)
        for n in range(8):
            page.draw_rect((40 + n * 10, 710, 45 + n * 10, 720))
        return doc.tobytes()


def published_barrier(monkeypatch, tmp_path, *, route_text=None, visual_door=False):
    from app import pdf_loader, scenario_library
    calls = install_wall_map_provider(monkeypatch, visual_door=visual_door)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    source = source_pdf(route_text or 'A breakable wall connects Basement to Corbitt hiding place to the east; it is necessary for progress.')
    quality = {}
    text, _, _, images, maps = pdf_loader.extract_text(source, quality_report=quality)
    assert quality['blocked_pages'] == []
    scenario_id = scenario_library.save_scenario(source, title='Barrier', filename='barrier.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=quality)
    return scenario_id, text, maps[1], calls, source, quality, images


def test_import_library_source_identity_and_private_certificate(monkeypatch, tmp_path):
    import json

    from app import scenario_library
    scenario_id, text, graph, calls, _, quality, _ = published_barrier(monkeypatch, tmp_path)
    assert len(calls) == 4  # No extra provider request for source topology.
    assert graph['source_topology'][0]['availability'] == 'blocked'
    assert scenario_library.load_context(scenario_id)['scene_maps'] == {'1': graph}
    root = tmp_path / scenario_id
    public = json.loads((root / 'parse_quality.json').read_text())
    assert 'source_topology' not in public['pages'][0]['map_analysis']
    assert 'source_evidence' not in json.dumps(public['pages'][0]['map_analysis'])
    assert quality['pages'][0]['map_analysis']['source_topology_certificate']['condition_metadata_sha256']
    (root / 'scenario.txt').write_text(text + '\nChanged canonical source.')
    context = scenario_library.load_context(scenario_id)
    assert context['scene_maps'] == {}
    assert 'Changed canonical source.' in context['text']


def test_authoritative_barrier_transition_is_durable_and_failure_is_retryable(monkeypatch, tmp_path):
    import pytest

    from app import map_routes, scene_map
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path)
    state = GroupState(group_id='barrier-transition', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
                       scene_maps={'1': graph})
    save_state(state)
    route = graph['source_topology'][0]
    origin, target = route['from'], route['to']
    initial = scene_map.resolve_move({'1': graph}, '1', origin, 'N', 'right')
    assert initial['ok'] is False and initial['blocked'] is True
    assert '障礙' in initial['interaction']
    with pytest.raises(PermissionError):
        map_routes.commit_outcome(state.group_id, 'player', '1', route['id'], 'opened')
    failed = map_routes.commit_outcome(state.group_id, 'kp', '1', route['id'], 'failed', consequence='noise')
    assert failed == {'availability': 'blocked', 'retryable': True, 'attempts': 1}
    reloaded = load_state(state.group_id)
    assert reloaded.map_route_states[route['id']]['consequence'] == 'noise'
    assert map_routes.available_routes(reloaded, '1') == frozenset()
    assert not scene_map.resolve_source_route(graph, origin, target)['ok']
    second = map_routes.commit_outcome(state.group_id, 'kp', '1', route['id'], 'failed')
    assert second['retryable'] and second['availability'] == 'blocked'
    opened = map_routes.commit_outcome(state.group_id, 'kp', '1', route['id'], 'opened')
    assert opened['availability'] == 'available'
    reloaded = load_state(state.group_id)
    allowed = map_routes.available_routes(reloaded, '1')
    assert allowed == frozenset({route['id']})
    assert reloaded.map_route_states[route['id']]['world_state'][route['condition']['key']] is True
    movement = scene_map.resolve_move({'1': graph}, '1', origin, 'N', 'right', available_routes=allowed)
    assert movement['ok'] and movement['room']['id'] == target
    # Runtime availability must not mutate certified source topology.
    assert reloaded.scene_maps['1'] == graph
    assert graph['source_topology'][0]['availability'] == 'blocked'
    reloaded.timeline_id = 'different-timeline'
    assert not map_routes.available_routes(reloaded, '1')


def test_hidden_overlay_is_rebuilt_on_resume_without_new_provider_requests(monkeypatch, tmp_path):
    from app import pdf_ingestion_drafts as drafts
    from app import pdf_loader
    _, text, graph, calls, source, report, images = published_barrier(monkeypatch, tmp_path / 'library')
    lease = drafts.reserve('hidden-resume', source, 'barrier.pdf')
    draft = drafts.checkpoint(lease, report, (text, [], False, images, {1: graph}))
    cached = copy.deepcopy(drafts.resume_pages(draft, report['extraction_identity']))
    # Even an old/tampered source overlay is not trusted as gameplay evidence.
    cached[1]['map']['source_topology'][0]['availability'] = 'available'
    final = {}
    result = pdf_loader.extract_text(source, quality_report=final, resume_pages=cached)
    assert final['pages'][0]['resumed'] is True
    assert len(calls) == 4
    assert result[4][1] == graph
    assert pdf_map_analysis.verified_graph(result[4][1], final['pages'][0]['map_analysis'], images[1], canonical_source=result[0])


def test_secret_door_discovery_and_public_where(monkeypatch, tmp_path):
    import asyncio

    import pytest

    from app import map_routes
    from app.commands.handlers import map_handler
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text='A secret door connects Basement to Corbitt hiding place to the east.')
    route = graph['source_topology'][0]
    state = GroupState(group_id='secret-discovery', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph}, current_map_page={'player': '1'}, current_room_id={'player': route['from']})
    save_state(state)
    responses = []
    async def reply(text):
        responses.append(text)
    asyncio.run(map_handler.handle_map_command(state.group_id, 'player', reply, None, ['/coc', 'where']))
    assert 'Corbitt' not in responses[-1] and 'secret' not in responses[-1]
    assert '沒有記錄到出口' in responses[-1]
    with pytest.raises(ValueError, match='discovery'):
        map_routes.commit_outcome(state.group_id, 'kp', '1', route['id'], 'opened')
    assert asyncio.run(map_handler.handle_map_command(state.group_id, 'player', reply, None,
        ['/coc', 'route', '1', route['id'], 'discovered'])) is False
    assert not map_routes.available_routes(load_state(state.group_id), '1')
    assert asyncio.run(map_handler.handle_map_command(state.group_id, 'kp', reply, None,
        ['/coc', 'route', '1', route['id'], 'discovered'])) is True
    asyncio.run(map_handler.handle_map_command(state.group_id, 'player', reply, None, ['/coc', 'where']))
    assert 'Corbitt hiding place' in responses[-1]
    assert route['id'] in map_routes.available_routes(load_state(state.group_id), '1')


def test_beacon_visual_door_and_stairs_still_work(certified_map_result):
    graph = {'entry_room_id': 'service', 'rooms': [
        {'id': 'service', 'name': 'Service Room', 'exits': [{'to': 'lamp', 'compass': 'U', 'label': 'stairs'}]},
        {'id': 'lamp', 'name': 'Lamp Room', 'exits': [{'to': 'gallery', 'compass': 'E', 'label': 'door'}]},
        {'id': 'gallery', 'name': 'Lantern Gallery', 'exits': []}]}
    result = certified_map_result('', graph, image=b'beacon')
    result = pdf_map_analysis.certify_source_topology(result.graph, result.analysis,
        '--- 第 16 頁 ---\nService Room, Lamp Room and Lantern Gallery are visible locations.')
    service, lamp, gallery = [r['id'] for r in result.graph['rooms']]
    assert not result.graph.get('source_topology')
    assert scene_map.resolve_move({'16': result.graph}, '16', service, 'N', 'up')['room']['id'] == lamp
    assert scene_map.resolve_move({'16': result.graph}, '16', lamp, 'N', 'right')['room']['id'] == gallery
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, b'beacon')


def test_private_challenger_prose_cannot_authorize_hidden_route(monkeypatch):
    import hashlib

    from app import pdf_loader
    calls = install_wall_map_provider(monkeypatch)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    source = source_pdf('The visible rooms share a solid wall.')
    report = {}
    first = pdf_loader.extract_text(source, quality_report=report)
    row = copy.deepcopy(report['pages'][0])
    row['candidates']['vision'] = SOURCE  # Private historical prose, not selected source.
    row['candidates']['layout'] = SOURCE
    cached = {1: {'pdf_sha256': hashlib.sha256(source).hexdigest(),
        'pipeline_version': pdf_loader.PIPELINE_VERSION, 'renderer_version': pdf_loader.RENDERER_VERSION,
        'extraction_identity': report['extraction_identity'], 'selected_text': row['selected_text'],
        'selected_sha256': row['selected_sha256'], 'report': row, 'image': first[3][1], 'map': first[4][1]}}
    final = {}
    result = pdf_loader.extract_text(source, quality_report=final, resume_pages=cached)
    assert final['pages'][0]['resumed']
    assert final['pages'][0]['candidates']['vision'] == SOURCE
    assert len(calls) == 4
    assert not result[4][1].get('source_topology')
    assert 'hidden passage' not in result[0]


def test_unknown_hidden_endpoints_quarantine_only_map_not_source(monkeypatch, tmp_path):
    from app import pdf_loader, scenario_library
    install_wall_map_provider(monkeypatch)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    source = source_pdf('A hidden passage connects Basement to Unknown Room.')
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(source, quality_report=report)
    assert maps == {}
    assert report['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert report['blocked_pages'] == report['hard_block_pages'] == []
    assert report['soft_review_pages'] == [1]
    assert report['pages'][0]['map_analysis']['status'] == 'MAP_GRAPH_INCOMPLETE'
    assert report['pages'][0]['map_analysis']['source_topology_diagnostics'][0]['code'] == 'unresolved_hidden_endpoints'
    scenario_id = scenario_library.save_scenario(source, title='Source safe', filename='map.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=report)
    context = scenario_library.load_context(scenario_id)
    assert context['text'] and context['scene_maps'] == {}


def test_explicit_two_way_barrier_opens_both_directions(monkeypatch, tmp_path):
    from app import map_routes
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path,
        route_text='A two-way breakable wall connects Basement to Corbitt hiding place to the east; it is necessary for progress.')
    routes = graph['source_topology']
    assert len(routes) == 2
    assert routes[0]['from'] == routes[1]['to'] and routes[0]['to'] == routes[1]['from']
    assert [r['compass'] for r in routes] == ['E', 'W']
    state = GroupState(group_id='two-way-barrier', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph})
    save_state(state)
    map_routes.commit_outcome(state.group_id, 'kp', '1', routes[0]['id'], 'opened')
    allowed = map_routes.available_routes(load_state(state.group_id), '1')
    assert allowed == frozenset(r['id'] for r in routes)
    assert scene_map.resolve_move({'1': graph}, '1', routes[1]['from'], 'N', 'left', available_routes=allowed)['ok']


def test_condition_metadata_tampering_invalidates_certificate(certified_map_result):
    original = visual_result(certified_map_result)
    source = '--- 第 7 頁 ---\nIf the seal is removed, a conditional route connects Basement to Corbitt hiding place.'
    result = pdf_map_analysis.certify_source_topology(original.graph, original.analysis, source)
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=source)
    route = result.graph['source_topology'][0]
    assert route['availability'] == 'blocked'
    assert route['condition']['source_text'] == 'the seal is removed'
    result.graph['source_topology'][0]['condition']['source_text'] = 'Extreme STR required'
    assert not pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=source)


def test_vision_wall_and_source_route_kinds_are_rejected():
    from app import pdf_map_evidence
    inventory, errors = pdf_map_evidence.merge_inventory([], [
        {'label': name, 'id_hint': name, 'floor_or_section': 'basement', 'visible': True, 'evidence': 'Visible room'}
        for name in ('Basement', 'Hiding place')])
    assert not errors
    for kind, basis in [('door', 'wall'), ('breakable_wall', 'door'), ('secret_door', 'door')]:
        graph, errors = pdf_map_evidence.build_graph(inventory, {
            'entry': {'status': 'resolved', 'room_id': inventory[0]['id'], 'evidence': 'Visible entry'},
            'edges': [{'id': 'wall', 'from': inventory[0]['id'], 'to': inventory[1]['id'],
                       'type': kind, 'visual_basis': basis, 'compass': 'E', 'evidence': 'Shared solid wall'}],
            'missing_locations': []})
        assert any(error['code'] == 'unsupported_edge' for error in errors)
        assert graph['rooms'][0]['exits'] == []


def test_router_named_movement_stays_put_until_route_is_authoritatively_opened(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock

    from app import map_routes
    from app.commands import router
    from app.models import Character, GroupState
    from app.repositories.group_state import load_state, save_state
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path)
    route = graph['source_topology'][0]
    target_name = scene_map.get_room(graph, route['to'])['name']
    state = GroupState(group_id='conditional-router', active=True, game_started=True, kp_assistant_user_id='kp',
        scenario_library_id=scenario_id, scene_maps={'1': graph},
        current_map_page={'player': '1'}, current_room_id={'player': route['from']})
    state.characters['player'] = Character(name='Player', owner_id='player')
    save_state(state)
    # Stub the subsequent AI turn, not the real map transaction or persistent state.
    turn = AsyncMock(return_value=('牆還沒有被突破。', [], []))
    monkeypatch.setattr(router.supervisor, 'run_turn', turn)
    async def move():
        await router.handle_text_message(state.group_id, 'player', AsyncMock(return_value='Player'),
            AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), '我去' + target_name)
    asyncio.run(move())
    assert load_state(state.group_id).current_room_id['player'] == route['from']
    assert turn.call_args.kwargs['resolved_location']['movement_blocked'] is True
    map_routes.commit_outcome(state.group_id, 'kp', '1', route['id'], 'opened')
    asyncio.run(move())
    assert load_state(state.group_id).current_room_id['player'] == route['to']
    assert turn.call_args.kwargs['resolved_location']['room_name'] == target_name


def test_barrier_kinds_and_hidden_barrier_defaults(certified_map_result):
    original = visual_result(certified_map_result)
    for wording, kind in [('blocked passage', 'blocked_passage'), ('sealed door', 'sealed_door'),
                          ('collapsible barrier', 'collapsible_barrier'), ('hidden breakable wall', 'breakable_wall')]:
        source = '--- 第 7 頁 ---\nA ' + wording + ' connects Basement to Corbitt hiding place.'
        result = pdf_map_analysis.certify_source_topology(original.graph, original.analysis, source)
        route = result.graph['source_topology'][0]
        assert route['type'] == kind and route['availability'] == 'blocked'
        assert route['visibility'] == ('hidden' if wording.startswith('hidden ') else 'visible')
        assert pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=source)


def test_narrated_breakthrough_does_not_open_a_route(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock

    from app import map_routes
    from app.commands import router
    from app.models import Character, GroupState
    from app.repositories.group_state import load_state, save_state
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path)
    route = graph['source_topology'][0]
    state = GroupState(group_id='narration-not-state', active=True, game_started=True,
        scenario_library_id=scenario_id, scene_maps={'1': graph},
        current_map_page={'player': '1'}, current_room_id={'player': route['from']})
    state.characters['player'] = Character(name='Player', owner_id='player')
    save_state(state)
    turn = AsyncMock(return_value=('牆已經破了。', [], []))
    monkeypatch.setattr(router.supervisor, 'run_turn', turn)
    asyncio.run(router.handle_text_message(state.group_id, 'player', AsyncMock(return_value='Player'),
        AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), '我要破牆'))
    reloaded = load_state(state.group_id)
    assert turn.call_args.kwargs['resolved_location'] is None
    assert reloaded.current_room_id['player'] == route['from']
    assert reloaded.map_route_states == {}
    assert not map_routes.available_routes(reloaded, '1')


def test_source_sealed_door_blocks_matching_visual_exit(certified_map_result):
    graph = {'entry_room_id': 'hall', 'rooms': [
        {'id': 'hall', 'name': 'Hall', 'exits': [{'to': 'cellar', 'compass': 'E', 'label': 'door'}]},
        {'id': 'cellar', 'name': 'Cellar', 'exits': []}]}
    visual = certified_map_result('', graph)
    source = '--- 第 1 頁 ---\nA sealed door connects Hall to Cellar to the east.'
    merged = pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis, source)
    route = merged.graph['source_topology'][0]
    assert not scene_map.resolve_move({'1': merged.graph}, '1', route['from'], 'N', 'right')['ok']
    assert not scene_map.resolve_source_route(merged.graph, route['from'], route['to'])['ok']
    assert scene_map.visible_exits(merged.graph, route['from']) == []
    allowed = frozenset({route['id']})
    assert scene_map.resolve_move({'1': merged.graph}, '1', route['from'], 'N', 'right', available_routes=allowed)['ok']
    assert scene_map.resolve_source_route(merged.graph, route['from'], route['to'], available_routes=allowed)['ok']


def test_alternate_secret_route_does_not_disable_an_ordinary_visible_door(certified_map_result):
    graph = {'entry_room_id': 'hall', 'rooms': [
        {'id': 'hall', 'name': 'Hall', 'exits': [{'to': 'cellar', 'compass': 'E', 'label': 'door'}]},
        {'id': 'cellar', 'name': 'Cellar', 'exits': []}]}
    visual = certified_map_result('', graph)
    source = '--- 第 1 頁 ---\nA secret door connects Hall to Cellar to the north.'
    merged = pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis, source)
    route = merged.graph['source_topology'][0]
    assert scene_map.resolve_move({'1': merged.graph}, '1', route['from'], 'N', 'right')['ok']
    assert not scene_map.resolve_move({'1': merged.graph}, '1', route['from'], 'N', 'forward')['ok']
    assert len(scene_map.visible_exits(merged.graph, route['from'])) == 1


def test_matching_visual_door_stays_blocked_after_failed_authoritative_attempt(monkeypatch, tmp_path):
    from app import map_routes
    from app.models import GroupState
    from app.repositories.group_state import load_state, save_state
    scenario_id, _, graph, _, _, _, _ = published_barrier(monkeypatch, tmp_path, visual_door=True,
        route_text='A sealed door connects Basement to Corbitt hiding place to the east.')
    route = graph['source_topology'][0]
    assert graph['rooms'][0]['exits']  # Ordinary visible door existed before source merge.
    state = GroupState(group_id='sealed-door-visual', kp_assistant_user_id='kp', scenario_library_id=scenario_id,
        scene_maps={'1': graph})
    save_state(state)
    for outcome in (None, 'failed', 'opened'):
        if outcome:
            map_routes.commit_outcome(state.group_id, 'kp', '1', route['id'], outcome)
        allowed = map_routes.available_routes(load_state(state.group_id), '1')
        expected = outcome == 'opened'
        assert scene_map.resolve_move({'1': graph}, '1', route['from'], 'N', 'right', available_routes=allowed)['ok'] is expected
        assert scene_map.resolve_source_route(graph, route['from'], route['to'], available_routes=allowed)['ok'] is expected
        assert bool(scene_map.visible_exits(graph, route['from'], available_routes=allowed)) is expected
