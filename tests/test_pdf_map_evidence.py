"""Vision supplies image evidence; deterministic code owns graph construction."""

def location(label, section='ground', hint=''):
    return {'label': label, 'floor_or_section': section, 'id_hint': hint,
            'visible': True, 'kind': 'location', 'evidence': 'Exact printed label'}


def test_inventory_preserves_floors_elevation_and_original_duplicate_labels():
    from app import pdf_map_evidence as maps

    rows = [location('Hallway', 'ground'), location('HALLWAY', 'ground'),
            location('Hallway', 'upper'), location('Lamp Room', 'side elevation'),
            {**location('Bed'), 'kind': 'furniture'}]
    inventory, errors = maps.merge_inventory([], rows)
    assert errors == []
    assert len(inventory) == 3
    assert len({room['id'] for room in inventory}) == 3
    assert inventory[0]['original_labels'] == ['Hallway', 'HALLWAY']
    assert inventory[1]['floor_or_section'] == 'upper'
    assert inventory[2]['label'] == 'Lamp Room'
    assert all(room['label'] != 'Bed' for room in inventory)


def test_deterministic_builder_accepts_traversal_and_rejects_walls():
    from app import pdf_map_evidence as maps

    inventory, _ = maps.merge_inventory([], [location('Entrance', hint='a'), location('Upper', 'upper', 'b')])
    for basis, compass in [('door', 'east'), ('stairs', 'U'), ('open passage', 'N')]:
        connectivity, _ = maps.normalize_connectivity({'entry': {'status': 'resolved', 'room_id': 'a', 'evidence': 'Visible entrance'},
            'edges': [{'from': 'a', 'to': 'b', 'type': basis, 'visual_basis': basis, 'compass': compass, 'evidence': 'Visible route'}]})
        graph, errors = maps.build_graph(inventory, connectivity)
        assert errors == []
        assert graph['rooms'][0]['exits'][0]['to'] == inventory[1]['id']
    for basis in ['wall', 'adjacency', 'shared wall', 'same floor proximity']:
        connectivity['edges'][0]['visual_basis'] = basis
        graph, errors = maps.build_graph(inventory, connectivity)
        assert graph['rooms'][0]['exits'] == []
        assert any(error['code'] == 'unsupported_edge' for error in errors)


def test_targeted_patch_fixes_dangling_target_without_regenerating_verified_edges():
    from app import pdf_map_evidence as maps

    inventory, _ = maps.merge_inventory([], [location('Entrance', hint='a'), location('Hall', hint='b')])
    good = {'id': 'keep', 'from': 'a', 'to': 'b', 'type': 'door', 'visual_basis': 'door', 'compass': 'E', 'evidence': 'Door shown'}
    bad = {**good, 'id': 'stairs', 'to': 'lamp', 'type': 'stairs', 'visual_basis': 'stairs', 'compass': 'U'}
    connectivity = {'entry': {'status': 'resolved', 'room_id': 'a', 'evidence': 'Front entrance'}, 'edges': [good, bad]}
    errors = [{'code': 'dangling_exit', 'subject': 'stairs'}, {'code': 'missing_location', 'subject': 'Lamp Room'}]
    patch = {'add_locations': [location('Lamp Room', 'upper', 'lamp')], 'remove_edges': [],
             'add_edges': [], 'replace_edges': [{'edge_id': 'stairs', 'edge': {**bad, 'evidence': 'Visible stairs reach Lamp Room'},
                                               'reason': 'Target visible in upper elevation'}], 'entry_update': None}
    rooms, repaired, rejected = maps.apply_patch(inventory, connectivity, patch, errors,
        verified_edges={'keep'}, verified_entry=True)
    assert rejected == []
    assert repaired['edges'][0] == good
    graph, checks = maps.build_graph(rooms, repaired)
    assert checks == []
    assert len(graph['rooms']) == 3
    assert all(edge['to'] in {room['id'] for room in graph['rooms']} for room in graph['rooms'] for edge in room['exits'])


def test_wall_patch_removes_only_failing_edge_and_preserves_verified_edge():
    from app import pdf_map_evidence as maps
    rooms, _ = maps.merge_inventory([], [location('Hall', hint='hall'), location('Cellar', hint='cellar'), location('Hiding Place', hint='hide')])
    good = {'id': 'door', 'from': 'hall', 'to': 'cellar', 'type': 'door', 'visual_basis': 'door', 'compass': 'D', 'evidence': 'Visible cellar door'}
    wall = {'id': 'wall', 'from': 'cellar', 'to': 'hide', 'type': 'door', 'visual_basis': 'wall', 'compass': 'E', 'evidence': 'Solid shared wall'}
    connectivity = {'entry': {'status': 'resolved', 'room_id': 'hall', 'evidence': 'Visible entrance'}, 'edges': [good, wall], 'missing_locations': []}
    _, errors = maps.build_graph(rooms, connectivity)
    patch = {'add_locations': [], 'remove_edges': ['wall'], 'add_edges': [], 'replace_edges': [], 'entry_update': None}
    _, repaired, rejected = maps.apply_patch(rooms, connectivity, patch, errors, verified_edges={'door'}, verified_entry=True)
    assert not rejected
    assert repaired['edges'] == [good]
    patch['remove_edges'].append('door')
    unchanged_rooms, unchanged, rejected = maps.apply_patch(rooms, connectivity, patch, errors, verified_edges={'door'}, verified_entry=True)
    assert rejected and unchanged == connectivity and unchanged_rooms == rooms


def test_patch_cannot_add_unseen_room_or_modify_verified_evidence_without_proof():
    from app import pdf_map_evidence as maps
    rooms, _ = maps.merge_inventory([], [location('Hall', hint='hall'), location('Cellar', hint='cellar')])
    edge = {'id': 'door', 'from': 'hall', 'to': 'cellar', 'type': 'door', 'visual_basis': 'door', 'compass': 'E', 'evidence': 'Visible door'}
    connectivity = {'entry': {'status': 'resolved', 'room_id': 'hall', 'evidence': 'Visible entrance'}, 'edges': [edge], 'missing_locations': []}
    errors = [{'code': 'unsupported_edge', 'subject': 'door'}]
    patch = {'add_locations': [location('Invented tunnel', hint='tunnel')], 'remove_edges': [], 'add_edges': [], 'replace_edges': [], 'entry_update': None}
    assert maps.apply_patch(rooms, connectivity, patch, errors, verified_edges={'door'}, verified_entry=True)[2]
    patch['add_locations'] = []
    patch['remove_edges'] = ['door']
    assert maps.apply_patch(rooms, connectivity, patch, errors, verified_edges={'door'}, verified_entry=True)[2][0]['code'] == 'verified_evidence_change'
    patch['remove_edges'] = [{'edge_id': 'door', 'evidence': 'New image closeup shows wall', 'reason': 'Old door evidence disproven'}]
    assert not maps.apply_patch(rooms, connectivity, patch, errors, verified_edges={'door'}, verified_entry=True)[2]


def test_duplicate_ids_bad_compass_and_unknown_room_cannot_build_exits():
    from app import pdf_map_evidence as maps
    rooms, _ = maps.merge_inventory([], [location('Hall', hint='hall'), location('Upper', 'upper', 'upper')])
    entry = {'status': 'resolved', 'room_id': 'hall', 'evidence': 'Visible front door'}
    edge = {'id': 'stairs', 'from': 'hall', 'to': 'upper', 'type': 'stairs', 'visual_basis': 'stairs', 'compass': 'invalid', 'evidence': 'Visible stairs'}
    connectivity, checks = maps.normalize_connectivity({'entry': entry, 'edges': [edge, edge], 'missing_locations': []})
    assert checks[0]['code'] == 'malformed_connectivity'
    graph, errors = maps.build_graph(rooms, {'entry': entry, 'edges': [edge]})
    assert graph['rooms'][0]['exits'] == []
    assert errors[0]['code'] == 'invalid_compass'
    patch = {'add_locations': [], 'remove_edges': [], 'add_edges': [],
             'replace_edges': [{'edge_id': 'stairs', 'edge': {**edge, 'compass': 'U', 'to': 'unknown'}, 'reason': 'Changed target'}], 'entry_update': None}
    assert any(e['code'] == 'dangling_exit' for e in maps.apply_patch(rooms, connectivity, patch, errors, verified_edges=set(), verified_entry=True)[2])


def test_provider_cannot_smuggle_rooms_into_connectivity():
    from app import pdf_map_evidence as maps
    _, errors = maps.normalize_connectivity({'entry': {}, 'edges': [], 'rooms': [{'id': 'invented'}]})
    assert errors[0]['code'] == 'malformed_connectivity'
    rooms, errors = maps.merge_inventory([], [{**location('Hall'), 'kind': []}, {**location('Hall'), 'label': ''}])
    assert not rooms and len(errors) == 2
