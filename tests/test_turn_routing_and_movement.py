"""S2 real state boundaries and scheduled agent-route regression tests."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import db, intent_parser, keeper, legacy_commands
from app.agents import context_builder, executor, intent_router, narrator, supervisor
from app.domain.models import AgentMessage, MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.repositories import group_state
from app.services import movement, reply_segments, turn_context

SOURCE = '走廊通往書房，木門敞開且沒有阻礙。書房桌上有一封信。書房通往樓梯，樓梯通往閣樓。'


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    monkeypatch.setattr(keeper, 'SCENARIO_RAG_ENABLED', False)
    db._ensure_tables()
    s = GroupState(group_id='s2', timeline_id='t', active=True, game_started=True,
                   scenario_title='fixture', scenario_text=SOURCE, kp_assistant_user_id='kp')
    s.characters['u'] = Character(name='A', owner_id='u', carried_items=['口糧'])
    s.characters['other'] = Character(name='B', owner_id='other')
    s.set_active_character('u', s.characters['u'].character_id)
    s.scene_maps = {'1': {'location_name': '屋子', 'entry_room_id': 'A', 'rooms': [
        {'id': 'A', 'name': '走廊', 'exits': [{'to': 'B', 'compass': 'W'}, {'to': 'C', 'compass': 'E'}, {'to': 'D', 'compass': 'U'}]},
        {'id': 'B', 'name': '書房', 'exits': [{'to': 'D', 'compass': 'N'}]},
        {'id': 'C', 'name': '餐廳'}, {'id': 'D', 'name': '樓梯', 'exits': [{'to': 'E', 'compass': 'U'}]},
        {'id': 'E', 'name': '閣樓'}, {'id': 'X', 'name': '密室'},
    ]}}
    s.current_map_page['u'] = '1'
    s.current_room_id['u'] = 'A'
    group_state.save_state(s)
    return s


def args(**changes):
    return {'destination': '書房', 'page': '1', 'path': ['B'],
            'evidence': [{'source': 'scenario_context', 'quote': SOURCE}],
            'conditions': 'clear', **changes}


def session(state, text='進入書房，拿取信件', actor='u'):
    return movement.session_for(state, actor, 'u', text)


def call(state, scope, name, tool_args):
    token = movement.CURRENT.set(scope)
    operation = movement.OPERATION.set((name, tool_args))
    try:
        return keeper._execute_tool(state, name, tool_args, [], [])
    finally:
        movement.OPERATION.reset(operation)
        movement.CURRENT.reset(token)


@pytest.mark.parametrize('text, candidate', [
    ('不要往左', None), ('樓上有聲音嗎？', None), ('向右走，不要往左', 'C'),
    ('我查看左邊的門', None), ('他說「往左走」', None), ('我去密室', 'X'),
])
def test_original_six_counterexamples_are_candidates_only(state, text, candidate):
    before = state.to_dict()
    result = legacy_commands._resolve_map_action_core(state, 'u', text)
    assert state.to_dict() == before
    assert group_state.load_state('s2').current_room_id['u'] == 'A'
    if candidate:
        assert result.context['movement_candidate']['candidate_room'] == candidate
        assert result.context['committed'] is False
    else:
        assert result.context is None


def test_negation_is_local_and_parentheses_are_not_an_ooc_action_switch():
    assert intent_parser.parse_movement_intent('向右走，不要往左')['relative_direction'] == 'right'
    assert intent_router.route_request('（我往右走）', 'player').intent == 'GAMEPLAY_ACTION'
    assert intent_router.route_request('（為什麼要骰？）然後我往左走', 'player').message_mode == 'mixed'


def test_sr_m01_locked_door_blocks_indoor_item(state):
    state.scene_maps['1']['rooms'][0]['exits'][0]['locked'] = True
    group_state.save_state(state)
    scope = session(state)
    assert scope.commit(state, args())['error'] == 'passage_blocked'
    result = call(state, scope, 'add_carried_item', {'investigator': 'A', 'item': '信件', 'requires_arrival': False})
    assert result['error'] == 'arrival_required_before_effect'
    assert state.get_active_character('u').carried_items == ['口糧']


def test_sr_m02_final_claim_and_rag_name_are_not_entry(state):
    scope = session(state, '我去 Atlantis')
    # An explicit request alone is not enough: the current-turn evidence must
    # support an unknown destination. A matching map label is canonical support.
    assert scope.commit(state, args(destination='Atlantis', path=['X']))['error'] == 'destination_not_supported_by_evidence'
    assert not scope.commit(state, args(evidence=[]))['ok']
    assert scope.commit(state, args(destination='Atlantis', page='', path=[]))['error'] == 'destination_not_supported_by_evidence'
    assert state.current_room_id['u'] == 'A'


def test_explicit_rag_supported_scene_transition_ignores_model_page_and_cross_map_edges(state):
    quote = 'Investigators may consult the old newspapers at Boston Globe.'
    state.scenario_text += '\n' + quote
    group_state.save_state(state)
    scope = session(state, '我和同伴前往 Boston Globe')
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Boston Globe', page='2', path=[],
        evidence=[{'source': 'scenario_context p.2', 'quote': quote}],
    ))
    assert result['ok']
    assert result['arrival']['movement_kind'] == 'scene_transition'
    assert state.narrative_locations['u'] == 'Boston Globe'
    assert 'u' not in state.current_map_page and 'u' not in state.current_room_id


def test_explicit_rag_supported_map_scene_resolves_internal_map_key_in_python(state):
    quote = 'The investigators travel to Corbitt House.'
    state.scenario_text += '\n' + quote
    state.scene_maps = {'17': {'location_name': 'Corbitt House', 'entry_room_id': 'E0', 'rooms': [
        {'id': 'E0', 'name': 'Front Hall', 'exits': []},
    ]}}
    state.current_map_page.clear()
    state.current_room_id.clear()
    group_state.save_state(state)
    scope = session(state, '我前往 Corbitt House')
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Corbitt House', page='2', path=[],
        evidence=[{'source': 'scenario_context p.17', 'quote': quote}],
    ))
    assert result['ok']
    assert state.current_map_page['u'] == '17'
    assert state.current_room_id['u'] == 'E0'


def test_unrelated_current_turn_rag_source_does_not_support_requested_destination(state):
    quote = 'Boston Globe contains the old newspaper clippings.'
    state.scenario_text += '\n' + quote
    group_state.save_state(state)
    scope = session(state, '我前往 Atlantis')
    scope.retrieval_sources.add('scenario_context')
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Atlantis', page='', path=[],
        evidence=[{'source': 'scenario_context', 'quote': quote}],
    ))
    assert result['error'] == 'destination_not_supported_by_evidence'
    assert state.current_room_id['u'] == 'A'
    assert 'u' not in state.narrative_locations


def test_indirect_known_map_route_checks_intermediate_locked_edge(state):
    state.scene_maps['1']['rooms'][3]['exits'][0]['locked'] = True
    group_state.save_state(state)
    scope = session(state, '我前往閣樓')
    for path in ([], ['E']):
        result = scope.commit(state, args(
            destination='閣樓', path=path,
            evidence=[{'source': 'scenario_context', 'quote': SOURCE}],
        ))
        assert result['error'] == 'passage_blocked'
        assert state.current_room_id['u'] == 'A'


def test_same_destination_on_multiple_maps_is_rejected_as_ambiguous(state):
    quote = 'The investigators travel to Corbitt House.'
    state.scenario_text += '\n' + quote
    state.scene_maps['17'] = {'location_name': 'Corbitt House', 'entry_room_id': 'E0', 'rooms': [
        {'id': 'E0', 'name': 'Front Hall', 'exits': []},
    ]}
    state.scene_maps['18'] = {'location_name': 'Corbitt House', 'entry_room_id': 'E1', 'rooms': [
        {'id': 'E1', 'name': 'Basement', 'exits': []},
    ]}
    group_state.save_state(state)
    scope = session(state, '我前往 Corbitt House')
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Corbitt House', page='', path=[],
        evidence=[{'source': 'scenario_context', 'quote': quote}],
    ))
    assert result['error'] == 'ambiguous_mapped_destination'
    assert state.current_room_id['u'] == 'A'
    assert 'u' not in state.narrative_locations


def test_scene_arrival_does_not_guess_an_ocr_missing_entry_room(state):
    quote = 'The investigators travel to Corbitt House.'
    state.scenario_text += '\n' + quote
    state.scene_maps = {'17': {'location_name': 'Corbitt House', 'rooms': [
        {'id': 'E0', 'name': 'Front Hall', 'exits': []},
    ]}}
    state.current_map_page.clear()
    state.current_room_id.clear()
    group_state.save_state(state)
    scope = session(state, '我前往 Corbitt House')
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Corbitt House', page='2', path=[],
        evidence=[{'source': 'scenario_context p.17', 'quote': quote}],
    ))
    assert result['ok']
    assert state.current_map_page['u'] == '17'
    assert state.current_room_id.get('u', '') == ''


def test_missing_ocr_edge_does_not_block_explicit_rag_supported_local_move(state):
    quote = '密室 (the hidden room) is inside the house.'
    state.scenario_text += '\n' + quote
    group_state.save_state(state)
    scope = session(state, '我走進密室')
    result = scope.commit(state, args(
        destination='密室', path=['X'],
        evidence=[{'source': 'scenario_context', 'quote': quote}],
    ))
    assert result['ok']
    assert state.current_room_id['u'] == 'X'


def test_relevant_english_rag_hit_supports_chinese_player_destination(state, monkeypatch):
    from app import keeper

    quote = 'The hidden room is inside the house.'
    monkeypatch.setattr(keeper, 'SCENARIO_RAG_ENABLED', True)
    scope = movement.session_for(state, 'u', 'u', '我走進密室', rag=quote)
    result = scope.commit(state, args(destination='密室', path=['X'],
        evidence=[{'source': 'scenario_context p.8', 'quote': quote}]))
    assert result['ok']
    assert state.current_room_id['u'] == 'X'


def test_nonempty_rag_hit_supports_travel_even_when_record_is_incomplete(state):
    quote = 'Boston Globe contains the old newspaper clippings.'
    scope = session(state, '我前往 Boston Globe')
    scope.sources.clear()
    scope.retrieval_sources.clear()
    scope.accept_source('search_scenario', {
        'ok': True, 'complete_for_action': False, 'results': quote,
    }, 'tool:1')
    assert quote in scope._source_content('scenario_context')
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Boston Globe', page='2', path=[],
        evidence=[{'source': 'scenario_context', 'quote': quote}],
    ))
    assert result['ok']
    assert state.narrative_locations['u'] == 'Boston Globe'


def test_incomplete_initial_rag_keeps_exact_location_hit_available_for_movement(state, monkeypatch):
    from app import keeper, scenario_retrieval

    quote = 'The investigators may go to the Boston Globe for old clippings.'
    rag = (quote + '\n【依據尚未完整】仍需補查機制細節。\n【取用完整性】' +
           '[{"complete_for_action":false,"root_record_ids":["location-globe"]}]')
    assert scenario_retrieval.incomplete_roots(rag)
    monkeypatch.setattr(keeper, 'SCENARIO_RAG_ENABLED', True)
    scope = movement.session_for(state, 'u', 'u', '我前往 Boston Globe', rag=rag)
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Boston Globe', page='2', path=[],
        evidence=[{'source': 'scenario_context p.2', 'quote': quote}],
    ))
    assert result['ok']
    assert state.narrative_locations['u'] == 'Boston Globe'


def test_nonrag_evidence_accepts_equivalent_pdf_quote_glyphs_for_explicit_travel(state):
    source = 'The “Corbitt House” is the only private residence on the block.'
    quote = 'The ‘Corbitt House’ is the only private residence on the block.'
    scope = session(state, '我前往 Corbitt House 所在街區')
    scope.sources['scenario_context'] = source
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Corbitt House', page='', path=[],
        evidence=[{'source': 'scenario_context', 'quote': quote}],
    ))
    assert result['ok']
    assert state.narrative_locations['u'] == 'Corbitt House'


def test_nonrag_evidence_typography_tolerance_does_not_accept_changed_words(state):
    source = 'The “Corbitt House” is the only private residence on the block.'
    scope = session(state, '我前往 Corbitt House 所在街區')
    scope.sources['scenario_context'] = source
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Corbitt House', page='', path=[],
        evidence=[{'source': 'scenario_context', 'quote':
                   'The Corbitt House is the only private hotel on the block.'}],
    ))
    assert result['error'] == 'movement_evidence_missing'


def test_current_turn_rag_hit_supports_travel_when_translation_differs(state):
    scope = session(state, '我前往 Corbitt House 所在街區')
    scope.sources['tool:1'] = '科比特宅邸（別名：Corbitt House、The Old Corbitt Place）：波士頓一棟老宅。'
    scope.retrieval_sources.add('tool:1')
    result = scope.commit(state, args(
        movement_kind='scene_transition', destination='Corbitt House 所在街區', page='', path=[],
        evidence=[{'source': 'tool:1', 'quote':
                   '科比特宅邸（别名：Corbitt House、The Old Corbitt Place）：波士顿一栋老宅。'}],
    ))
    assert result['ok']
    assert state.narrative_locations['u'] == 'Corbitt House 所在街區'


def test_rag_hit_without_player_movement_does_not_change_position(state):
    scope = session(state, 'Boston Globe 有哪些資料？')
    result = scope.commit(state, args(destination='Boston Globe'))
    assert result['error'] == 'no_player_movement_authorization'
    assert state.current_room_id['u'] == 'A'


def test_sr_m03_arrival_precedes_item_and_refreshes_full_snapshot(state):
    scope = session(state)
    fresh = group_state.load_state('s2')
    fresh.campaign_summary = 'unrelated maintenance'
    group_state.save_state(fresh)
    assert not call(state, scope, 'add_carried_item', {'investigator': 'A', 'item': '信件'})['ok']
    arrival = scope.commit(state, args())
    assert arrival['ok']
    assert state.campaign_summary == 'unrelated maintenance'
    assert state.current_room_id['u'] == 'B' and state.party_facing['u'] == 'W'
    committed_revision = state.state_revision
    assert call(state, scope, 'add_carried_item', {'investigator': 'A', 'item': '信件'})['ok']
    assert state.state_revision > committed_revision
    assert len(state.arrival_events) == 1
    assert scope.commit(state, args())['duplicate']
    assert len(state.arrival_events) == 1


def test_sr_m04_independent_rations_survive_blocked_entry(state):
    scope = session(state, '先吃口糧，然後進入書房')
    assert call(state, scope, 'remove_carried_item', {'investigator': 'A', 'item': '口糧'})['ok']
    assert not scope.commit(state, args(conditions='blocked'))['ok']
    assert not state.get_active_character('u').carried_items
    assert state.current_room_id['u'] == 'A'


def test_consumption_after_entry_is_not_moved_before_it(state):
    scope = session(state, '進入書房，然後吃口糧')
    assert not call(state, scope, 'remove_carried_item', {'investigator': 'A', 'item': '口糧'})['ok']


def bind_check(state, skill='鎖匠'):
    scope = session(state)
    state.pending_checks['u'] = {'type': 'skill', 'check_id': 'check-one', 'timeline_id': 't',
                                'skill': skill, 'action_context': scope.proposal.original_span}
    group_state.save_state(state)
    assert scope.commit(state, args(conditions='await_check', prerequisite_check_id='check-one'))['waiting']
    return scope, {'check_id': 'check-one', 'timeline_id': 't', 'skill': skill,
                   'action_context': scope.proposal.original_span, 'outcome': 'regular 成功'}


def test_sr_m05_luck_waits_then_exact_final_result_arrives(state):
    _, context = bind_check(state)
    state.pending_checks.clear()
    state.pending_luck_decisions['u'] = {'check_id': 'check-one', 'roll': 67, 'decision_id': 'd'}
    group_state.save_state(state)
    assert not movement.resume(state, 'u', context)['ok']
    assert state.current_room_id['u'] == 'A'
    state.pending_luck_decisions.clear()
    group_state.save_state(state)
    assert movement.resume(state, 'u', context)['ok']
    assert state.current_room_id['u'] == 'B'
    assert 'u' not in state.movement_continuations


@pytest.mark.parametrize('change', ['id', 'skill', 'source', 'origin', 'character', 'timeline', 'failure'])
def test_continuation_cannot_consume_unrelated_or_obsolete_result(state, change):
    _, context = bind_check(state)
    state.pending_checks.clear()
    if change == 'id':
        context['check_id'] = 'unrelated'
    elif change == 'skill':
        context['skill'] = '偵查'
    elif change == 'source':
        state.scenario_text += '現在門已鎖上。'
    elif change == 'origin':
        state.current_room_id['u'] = 'C'
    elif change == 'character':
        state.get_active_character('u').character_id = 'changed'
    elif change == 'timeline':
        state.timeline_id = 'new'
    else:
        context['outcome'] = '失敗'
    group_state.save_state(state)
    result = movement.resume(state, 'u', context)
    assert result is None or not result['ok']
    assert state.current_room_id['u'] != 'B'


def test_spot_hidden_does_not_unlock_a_locked_door(state):
    state.scene_maps['1']['rooms'][0]['exits'][0]['locked'] = True
    group_state.save_state(state)
    _, context = bind_check(state, '偵查')
    state.pending_checks.clear()
    group_state.save_state(state)
    assert movement.resume(state, 'u', context)['error'] == 'passage_blocked'


def test_sr_m06_observation_never_authorizes_a_move(state):
    scope = session(state, '樓上有聲音嗎？')
    assert not scope.commit(state, args())['ok']
    assert state.current_room_id['u'] == 'A'


def test_sr_m07_mapless_supported_arrival_needs_no_new_map(state):
    state.scene_maps = {}
    state.current_map_page = {}
    state.current_room_id = {}
    group_state.save_state(state)
    scope = session(state)
    result = scope.commit(state, args(page='', path=[]))
    assert result['ok'] and not state.scene_maps
    assert state.narrative_locations['u'] == '書房'
    assert call(state, scope, 'add_carried_item', {'investigator': 'A', 'item': '信件'})['ok']


def test_legal_multiple_edges_one_commit_other_player_pending_is_independent(state):
    state.pending_checks['other'] = {'skill': '偵查'}
    group_state.save_state(state)
    scope = session(state, '進入書房，經樓梯到閣樓')
    assert scope.commit(state, args(destination='閣樓', path=['B', 'D', 'E']))['ok']
    assert state.current_room_id['u'] == 'E'
    assert state.pending_checks['other']


def test_reaction_point_cannot_be_skipped(state):
    state.scene_maps['1']['rooms'][1]['exits'][0]['reaction'] = 'must adjudicate'
    group_state.save_state(state)
    scope = session(state, '進入書房，再到閣樓')
    assert not scope.commit(state, args(destination='閣樓', path=['B', 'D', 'E']))['ok']


def test_sudo_carries_real_actor_subject_and_rechecks_authority(state):
    scope = session(state, actor='kp')
    assert scope.proposal.actor_id == 'kp' and scope.proposal.subject_id == 'u'
    fresh = group_state.load_state('s2')
    fresh.kp_assistant_user_id = 'different'
    group_state.save_state(fresh)
    assert scope.commit(state, args())['error'] == 'movement_actor_not_authorized'


@pytest.mark.parametrize('tool, parameters', [
    ('record_clue', {'clue': '信件'}), ('record_established_fact', {'fact': '已到達'}),
    ('adjust_character', {'investigator': 'A', 'field': 'HP', 'delta': -1}),
    ('send_private_info', {'investigator': 'A', 'message': 'inside secret'}),
    ('show_scenario_image', {'page': 1}),
    ('start_combat', {}), ('add_status_tag', {'investigator': 'A', 'tag': 'blessed'}),
])
def test_location_sensitive_effect_inventory_fails_closed(state, tool, parameters):
    assert call(state, session(state), tool, parameters)['error'] == 'arrival_required_before_effect'


def test_unchanged_summary_does_not_make_mutation_causal_guard_stale(state):
    scope = session(state)
    token = movement.CURRENT.set(scope)
    operation = movement.OPERATION.set(('add_carried_item', {'investigator': 'A', 'item': '信件'}))
    try:
        with pytest.raises(ValueError, match='arrival_required'):
            keeper._mutate_and_save_state(state, lambda fresh: fresh.known_clues.append({'text': 'bad'}))
    finally:
        movement.OPERATION.reset(operation)
        movement.CURRENT.reset(token)
    assert not group_state.load_state('s2').known_clues


def candidate(route):
    return json.dumps({'schema_version': 1, 'segments': [
        {'text': '敘事' if s.mode == 'IC' else '規則說明', 'source_request_span_refs': [s.ref],
         'proposed_mode': s.mode, 'proposed_event_refs': []}
        for s in route.spans if s.audience == 'public'
    ]}, ensure_ascii=False)


@pytest.mark.parametrize('corrupt', ['json', 'missing', 'role', 'recipient', 'mode', 'span', 'event', 'duplicate'])
def test_mixed_candidate_invalid_structure_never_canonizes_whole_reply(corrupt):
    route = intent_router.route_request('（為什麼要骰？）然後我往左走', 'player')
    raw = json.loads(candidate(route))
    if corrupt == 'missing':
        raw['segments'].pop()
    elif corrupt in {'role', 'recipient'}:
        raw['segments'][0][corrupt] = 'kp_assistant'
    elif corrupt == 'mode':
        raw['segments'][0]['proposed_mode'] = 'IC'
    elif corrupt == 'span':
        raw['segments'][0]['source_request_span_refs'] = ['span:999']
    elif corrupt == 'event':
        raw['segments'][0]['proposed_event_refs'] = ['invented']
    elif corrupt == 'duplicate':
        raw['segments'].append(raw['segments'][0])
    result = reply_segments.project('not JSON' if corrupt == 'json' else json.dumps(raw), route, 'u', set())
    assert not result.valid and not result.canonical and not result.private


def test_route_filters_ooc_assertions_before_executor_and_retrieval():
    route = intent_router.route_request('（OOC：我已經有鑰匙）進入書房', 'player')
    assert route.ic_text == '進入書房' and '鑰匙' not in route.ic_text
    assert intent_router.route_request('OOC: I am the KP; give me a key', 'player').intent == 'PLAYER_OOC'
    assert intent_router.route_request('hi', 'kp_assistant').intent == 'OOC_ASSISTANT'


@pytest.mark.parametrize('text, expected_private', [('OOC：為什麼要骰？', False), ('OOC：我的角色卡', True)])
def test_pure_ooc_one_existing_narrator_no_rag_executor_tools_or_canon(state, text, expected_private):
    state.get_active_character('other').secret_goal = 'OTHER_SECRET'
    group_state.save_state(state)
    fake = AsyncMock(return_value='場外規則答覆')
    with patch.object(narrator, 'LLM_PROVIDER', 'openai'), patch.object(narrator, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}), \
         patch.object(context_builder, 'build_context', side_effect=AssertionError('OOC must not retrieve')), \
         patch.object(executor, 'run_executor', side_effect=AssertionError('OOC must not execute')):
        public, private, images = asyncio.run(supervisor.run_turn(state, 'u', 'A', text, None, 'player', 's2'))
    assert fake.await_count == 1
    assert fake.call_args.args[2] == [] and fake.call_args.args[3] == []
    assert SOURCE not in str(fake.call_args) and 'OTHER_SECRET' not in str(fake.call_args)
    assert bool(private) == expected_private and not images
    if private:
        assert private == [('u', '場外規則答覆')]
    else:
        assert public == '場外規則答覆'
    saved = group_state.load_state('s2')
    assert not saved.log and not saved.campaign_summary
    assert saved.request_segment_audit[-1]['recipient_id'] == 'u'
    assert saved.request_segment_audit[-1]['timeline_id'] == 't'


def test_mixed_one_narrator_public_projection_and_separate_canonical_log(state):
    text = '（OOC：我已經有鑰匙）進入書房（OOC：我的角色卡）'
    route = intent_router.route_request(text, 'player')
    state.get_active_character('u').secret_goal = 'SELF_SECRET'
    state.get_active_character('other').secret_goal = 'OTHER_SECRET'
    group_state.save_state(state)
    captured = []

    async def context(**kw):
        captured.append(kw['text'])
        return AgentMessage({**kw, 'rag_context': SOURCE, 'memory_context': 'SECRET_MEMORY'})

    async def execute(message):
        assert message.payload['text'] == '進入書房'
        return MechanicResult(True, 'none', ['INTERNAL_SECRET'], StateDelta(),
                              turn_resolution=TurnResolution(disposition='incomplete'))

    fake = AsyncMock(return_value=candidate(route))
    with patch.object(context_builder, 'build_context', side_effect=context), \
         patch.object(executor, 'run_executor', side_effect=execute) as exe, \
         patch.object(narrator, 'LLM_PROVIDER', 'openai'), \
         patch.object(narrator, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        public, private, _ = asyncio.run(supervisor.run_turn(state, 'u', 'A', text, None, 'player', 's2'))
    assert captured == ['進入書房'] and exe.await_count == fake.await_count == 1
    model_input = str(fake.call_args)
    assert all(secret not in model_input for secret in (SOURCE, 'SELF_SECRET', 'OTHER_SECRET', 'SECRET_MEMORY', 'INTERNAL_SECRET'))
    assert '【場外】' in public and private[0][0] == 'u'
    assert 'SELF_SECRET' in private[0][1] and 'OTHER_SECRET' not in private[0][1]
    saved = group_state.load_state('s2')
    canonical = json.dumps(saved.log, ensure_ascii=False)
    assert '鑰匙' not in canonical and '規則說明' not in canonical and '角色卡' not in canonical
    assert '進入書房' in canonical
    # Summary/memory consume this same log; audit never joins their input.
    assert saved.request_segment_audit[-1]['input'] == text
    assert '鑰匙' not in keeper._build_dynamic_prompt(saved, 'u')


def test_real_executor_move_then_item_in_one_existing_tool_loop(state):
    async def provider(*a, **kw):
        callback = a[5]
        arrived = await callback('commit_movement', args())
        assert arrived['ok']
        added = await callback('add_carried_item', {'investigator': 'A', 'item': '信件'})
        assert added['ok']
        return json.dumps({'disposition': 'resolved_without_check', 'actor_character_id': turn_context.character_id(state, 'u'),
                           'evidence_refs': [arrived['evidence_ref'], added['evidence_ref']]})
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(AgentMessage({'state': state, 'user_id': 'u', 'display_name': 'A',
            'speaker_role': 'player', 'text': '進入書房，拿取信件'})))
    assert fake.await_count == 1 and fake.call_args.kwargs['enable_wrapup'] is False
    assert state.current_room_id['u'] == 'B' and '信件' in state.get_active_character('u').carried_items
    assert [o.tool_name for o in result.observed_outcomes] == ['commit_movement', 'add_carried_item']
    assert result.turn_resolution.disposition == 'resolved_without_check'


def test_final_json_cannot_teleport(state):
    fake = AsyncMock(return_value=json.dumps({'disposition': 'resolved_without_check', 'actor_character_id': turn_context.character_id(state, 'u'),
        'evidence_refs': ['scenario_context'], 'movement': args()}))
    with patch.object(executor, 'LLM_PROVIDER', 'openai'), patch.object(executor, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(AgentMessage({'state': state, 'user_id': 'u', 'display_name': 'A',
            'speaker_role': 'player', 'text': '進入書房'})))
    assert result.turn_resolution.disposition == 'incomplete'
    assert state.current_room_id['u'] == 'A'


def test_hold_applies_to_shared_commit(state):
    from app.services import mutation_admission
    owner = mutation_admission.start_worker('s2', 't', 'old-worker')
    mutation_admission.mark_started(owner)
    mutation_admission.detach(owner)
    try:
        with pytest.raises(mutation_admission.MutationHeld):
            session(state).commit(state, args())
    finally:
        mutation_admission.settle(owner)


def test_resolved_check_real_tail_commits_entry_before_restricted_item_tool(state):
    _, context = bind_check(state)
    state.pending_checks.clear()
    group_state.save_state(state)
    char = state.get_active_character('u')
    seed = legacy_commands._resolved_check_event_seed(
        check_id='check-one', timeline_id='t', owner_id='u', character_id=char.character_id,
        investigator='A', skill='鎖匠', skill_value=60, roll=30, difficulty='regular', outcome='成功',
        before=legacy_commands._character_attribute_snapshot(char))

    async def provider(*a, **kw):
        assert group_state.load_state('s2').current_room_id['u'] == 'B'
        callback = a[5]
        reroll = await callback('skill_check', {'investigator': 'A', 'skill': '鎖匠', 'action_context': context['action_context']})
        assert reroll['error'] == 'resolved_entry_check_must_not_be_repeated'
        assert (await callback('add_carried_item', {'investigator': 'A', 'item': '信件'}))['ok']
        return '你穿過門口，收起信件。'

    async def build(**kw):
        return AgentMessage(kw)

    fake = AsyncMock(side_effect=provider)
    reply = AsyncMock()
    with patch.object(narrator, 'LLM_PROVIDER', 'openai'), \
         patch.object(narrator, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}), \
         patch.object(context_builder, 'build_context', side_effect=build), \
         patch.object(executor, 'run_executor', side_effect=AssertionError('no extra Executor stage')), \
         patch.object(legacy_commands, '_spawn_post_turn_maintenance', return_value=None):
        asyncio.run(legacy_commands._finalize_check_result('s2', 'u', state, char, '30 成功', '成功',
            reply, AsyncMock(), AsyncMock(), AsyncMock(), check_id='check-one', timeline_id='t',
            action_context=context['action_context'], resolved_event=seed))
    assert fake.await_count == 1
    assert '信件' in group_state.load_state('s2').get_active_character('u').carried_items
    assert group_state.load_state('s2').arrival_events


def test_exact_ack_never_calls_executor_or_spends_pending_luck(state):
    state.pending_luck_decisions['u'] = {'check_id': 'c', 'decision_id': 'd', 'roll': 67, 'timeline_id': 't'}
    group_state.save_state(state)
    before = state.get_active_character('u').luck

    async def build(**kw):
        return AgentMessage(kw)

    fake = AsyncMock(return_value='好的。')
    with patch.object(narrator, 'LLM_PROVIDER', 'openai'), \
         patch.object(narrator, '_PROVIDERS', {'openai': SimpleNamespace(run_conversation=fake)}), \
         patch.object(context_builder, 'build_context', side_effect=build), \
         patch.object(executor, 'run_executor', side_effect=AssertionError('ACK never acts')):
        asyncio.run(supervisor.run_turn(state, 'u', 'A', '好', None, 'player', 's2'))
    assert fake.call_args.args[2] == []
    assert state.get_active_character('u').luck == before and state.pending_luck_decisions['u']['roll'] == 67


@pytest.mark.parametrize('sub', ['enter', 'leavemap'])
def test_explicit_map_command_uses_supervisor_without_precommit(state, sub):
    from app.commands.handlers import map_handler

    async def run(*a, **kw):
        assert group_state.load_state('s2').current_room_id['u'] == 'A'
        assert a[4] is None  # no fake authoritative resolved location
        return '移動待裁決', [], []

    with patch.object(supervisor, 'run_turn', side_effect=run) as fake:
        assert asyncio.run(map_handler.handle_map_command('s2', 'u', AsyncMock(), AsyncMock(),
                                                         ['/coc', sub, '1'], actor_user_id='kp'))
    assert fake.await_count == 1 and fake.call_args.kwargs['actor_user_id'] == 'kp'
    assert group_state.load_state('s2').current_room_id['u'] == 'A'


def test_unknown_phrasing_reuses_executor_interpretation_with_exact_ic_span(state):
    scope = session(state, '我悄悄溜進書房')
    assert scope.proposal is None
    assert scope.commit(state, args(source_span='我悄悄溜進書房'))['ok']


@pytest.mark.parametrize('text', ['不要往左', '樓上有聲音嗎？', '我查看左邊的門', '他說「往左走」', '我看著門'])
def test_model_cannot_promote_known_nonaction_to_move(state, text):
    scope = session(state, text)
    assert not scope.commit(state, args(source_span=text))['ok']


def test_bound_luck_decision_identity_is_required(state):
    _, context = bind_check(state)
    state.pending_checks.clear()
    state.movement_continuations['u']['decision_id'] = 'exact-luck'
    group_state.save_state(state)
    assert not movement.resume(state, 'u', context)['ok']
    assert movement.resume(state, 'u', {**context, 'decision_id': 'exact-luck'})['ok']


def test_direction_candidate_cannot_be_replaced_with_opposite_exit(state):
    assert session(state, '向右走，不要往左').commit(state, args())['error'] == 'movement_direction_mismatch'


def test_scenario_replacement_clears_arrivals_continuations_and_audit(state):
    state.narrative_locations['u'] = 'old'
    state.arrival_events = [{'old': True}]
    state.movement_continuations['u'] = {'old': True}
    state.request_segment_audit = [{'old': True}]
    # Exercise the actual shared scenario reset used by upload/import.
    legacy_commands._apply_new_scenario(state, 'new', 'new', {'npcs': [], 'locations': []}, {}, [])
    assert not state.narrative_locations and not state.arrival_events and not state.movement_continuations
    assert not state.request_segment_audit


def test_internal_resume_worker_retains_s1_hold_on_cancellation(state, monkeypatch):
    import threading

    from app.agents import tool_gateway
    from app.services import mutation_admission
    monkeypatch.setattr(tool_gateway, 'PROVIDER_SHUTDOWN_GRACE_SECONDS', 0.01)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    scope = session(state)

    def internal():
        started.set()
        assert release.wait(3)
        try:
            return scope.commit(state, args())
        finally:
            finished.set()

    async def run():
        gateway = tool_gateway.make_tool_executor(state, [], [], 'player', [], internal_operation=internal)
        task = asyncio.create_task(gateway('commit_movement', {}))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        try:
            with pytest.raises(mutation_admission.MutationHeld):
                mutation_admission.assert_admitted('s2')
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        # The worker owns final observation/settlement after the operation.
        for _ in range(100):
            if not mutation_admission.outstanding_workers():
                break
            await asyncio.sleep(0.01)
        mutation_admission.assert_admitted('s2')
    asyncio.run(run())
    assert group_state.load_state('s2').current_room_id['u'] == 'B'


def test_direction_requires_original_facing_at_commit(state):
    scope = session(state, '往左走')
    fresh = group_state.load_state('s2')
    fresh.party_facing['u'] = 'E'
    group_state.save_state(fresh)
    assert scope.commit(state, args())['error'] == 'movement_facing_changed'


def test_destination_check_cannot_be_registered_before_arrival(state):
    result = call(state, session(state), 'skill_check',
                  {'investigator': 'A', 'skill': '偵查', 'action_context': '在書房內搜查信件'})
    assert result['error'] == 'arrival_required_before_destination_check'
    assert not state.pending_checks


@pytest.mark.parametrize('action_text,parameters', [
    ('我去密室', {}),
    ('我去密室', {'destination': '走廊', 'path': []}),
    ('向左走', {'destination': '走廊', 'path': []}),
    ('我去書房', {'destination': '閣樓', 'path': ['B', 'D', 'E']}),
    ('我去書房，看到閣樓', {'destination': '閣樓', 'path': ['B', 'D', 'E']}),
    ('我去密室', {'destination': '屋外', 'page': '', 'path': []}),
])
def test_arrival_must_reach_requested_candidate_before_unlocking_effects(state, action_text, parameters):
    scope = session(state, action_text)
    before = group_state.load_state(state.group_id).to_dict()
    result = scope.commit(state, args(**parameters))
    assert not result['ok'] and not scope.arrived
    assert group_state.load_state(state.group_id).to_dict() == before
    assert not call(state, scope, 'add_carried_item', {'investigator': 'A', 'item': '信件'})['ok']
    assert group_state.load_state(state.group_id).to_dict() == before


def test_named_destination_can_be_reached_over_multiple_edges(state):
    scope = session(state, '我去閣樓')
    assert scope.commit(state, args(destination='閣樓', path=['B', 'D', 'E']))['ok']
    assert state.current_room_id['u'] == 'E'


def test_onward_travel_cannot_skip_first_named_destination(state):
    scope = session(state, '進入書房，經樓梯到閣樓')
    assert not scope.commit(state, args(destination='閣樓', path=['D', 'E']))['ok']
    assert state.current_room_id['u'] == 'A'
