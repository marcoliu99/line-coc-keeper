"""Regression tests for a source-linked, single-use scenario opposed check."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from app import keeper, legacy_commands, scenario_authoring
from app.agents.supervisor import _unchanged_pending_reply
from app.agents.tool_gateway import _describe_tool_call
from app.domain.models import MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.scenario_references import link_records
from app.services import opposed_checks, turn_context
from app.services.prompt_config import build_resolved_check_outcome_block

REQUEST = {
    'opponent_skill': 'POW', 'opponent_value': 90, 'tie_winner': 'opponent',
    'source': 'Having Hold of the Knife, p. 11',
    'on_win': 'The player holds the flying knife.',
    'on_loss': 'The knife escapes; resolve its attack separately.',
}


def _state() -> GroupState:
    state = GroupState(group_id='scenario-check-test')
    state.characters['u1'] = Character(name='Marco', owner_id='u1', skills={'格鬥（鬥毆）': 70}, luck=0)
    state.active = True
    return state


def test_opposed_contract_rejects_model_result_and_out_of_range_stat():
    with pytest.raises(ValueError):
        opposed_checks.contract({**REQUEST, 'opponent_roll': 9})
    with pytest.raises(ValueError):
        opposed_checks.contract({**REQUEST, 'opponent_value': 999})


@pytest.mark.parametrize(('player_tier', 'opponent_tier', 'winner'), [
    ('regular', 'hard', 'opponent'),
    ('hard', 'regular', 'player'),
    ('hard', 'hard', 'opponent'),
    ('fail', 'fail', 'neither'),
])
def test_opposed_result_uses_tiers_and_tie_rule(player_tier, opponent_tier, winner):
    receipt = {**REQUEST, 'opponent_roll': 30, 'opponent_tier': opponent_tier}
    assert opposed_checks.resolve(receipt, player_tier)['winner'] == winner


def test_manual_check_persists_one_opponent_roll_and_its_final_outcome():
    state = _state()
    saved = {}

    def load_state(_group_id):
        return GroupState.from_dict(saved['state'].to_dict())

    def save_state(new_state, **_kwargs):
        saved['state'] = GroupState.from_dict(new_state.to_dict())

    saved['state'] = GroupState.from_dict(state.to_dict())
    tool_input = {'investigator': 'Marco', 'skill': '格鬥（鬥毆）', 'opposed': REQUEST,
                  'action_basis': 'Flying knife; grab it, p. 11', '_player_action': '抓住飛來的刀'}
    with patch.object(keeper, 'load_state', load_state), patch.object(keeper, 'save_state', save_state), \
         patch.object(legacy_commands, 'load_state', load_state), patch.object(legacy_commands, 'save_state', save_state), \
         patch('app.dice.roll_percentile_with_dice_pool', side_effect=[30, 40]) as dice_roll:
        result = keeper._execute_tool(state, 'skill_check', tool_input, [], [], speaker_role='player')
        assert result['ok'] and not result.get('resolved')
        first_receipt = deepcopy(saved['state'].pending_checks['u1']['opposed'])
        assert first_receipt['opponent_roll'] == 30
        duplicate = keeper._execute_tool(state, 'skill_check', tool_input, [], [], speaker_role='player')
        assert duplicate['ok'] and duplicate['opposed_pending'] is True
        assert 'opposed' not in duplicate and 'opposed' not in result
        assert saved['state'].pending_checks['u1']['opposed'] == first_receipt
        assert dice_roll.call_count == 1
        resolved = legacy_commands._resolve_check_deterministically('scenario-check-test', 'u1', '/coc check')

    assert dice_roll.call_count == 2
    assert resolved.should_finalize
    assert resolved.resolved_event['opposed_outcome']['winner'] == 'opponent'
    assert resolved.resolved_event['player_declaration'] == '抓住飛來的刀'
    assert '對抗勝方=opponent' in resolved.resolved_event['outcome']
    assert not saved['state'].pending_checks


def test_autoroll_success_field_reflects_opposed_loss():
    state = _state()
    state.autoroll_checks = True
    saved = {}

    def load_state(_group_id):
        return GroupState.from_dict(saved['state'].to_dict())

    def save_state(new_state, **_kwargs):
        saved['state'] = GroupState.from_dict(new_state.to_dict())

    saved['state'] = GroupState.from_dict(state.to_dict())
    with patch.object(keeper, 'load_state', load_state), patch.object(keeper, 'save_state', save_state), \
         patch('app.dice.roll_percentile_with_dice_pool', side_effect=[30, 40]):
        result = keeper._execute_tool(state, 'skill_check', {
            'investigator': 'Marco', 'skill': '格鬥（鬥毆）', 'opposed': REQUEST,
            'action_basis': 'Flying knife; grab it, p. 11', '_player_action': '抓住飛來的刀',
        }, [], [], speaker_role='player')
    assert result['player_check_success'] is True
    assert result['opposed_outcome']['winner'] == 'opponent'
    assert result['success'] is False
    assert 'opponent_roll' not in str(result)
    assert 'opponent_value' not in str(result)


@pytest.mark.parametrize(('choice', 'winner'), [('skip', 'opponent'), ('extreme', 'player')])
def test_luck_resolution_reuses_opponent_receipt(choice, winner):
    state = _state()
    state.characters['u1'].luck = 50
    saved = {}

    def load_state(_group_id):
        return GroupState.from_dict(saved['state'].to_dict())

    def save_state(new_state, **_kwargs):
        saved['state'] = GroupState.from_dict(new_state.to_dict())

    saved['state'] = GroupState.from_dict(state.to_dict())
    with patch.object(keeper, 'load_state', load_state), patch.object(keeper, 'save_state', save_state), \
         patch.object(legacy_commands, 'load_state', load_state), patch.object(legacy_commands, 'save_state', save_state), \
         patch('app.dice.roll_percentile_with_dice_pool', side_effect=[30, 40]) as dice_roll:
        keeper._execute_tool(state, 'skill_check', {
            'investigator': 'Marco', 'skill': '格鬥（鬥毆）', 'opposed': REQUEST,
            'action_basis': 'Flying knife; grab it, p. 11', '_player_action': '抓住飛來的刀',
        }, [], [], speaker_role='player')
        pending = legacy_commands._resolve_check_deterministically('scenario-check-test', 'u1', '/coc check')
        assert not pending.should_finalize
        assert saved['state'].pending_luck_decisions['u1']['opposed']['opponent_roll'] == 30
        final = legacy_commands._resolve_luck_decision_deterministically('scenario-check-test', 'u1', choice)

    assert dice_roll.call_count == 2
    assert final.should_finalize
    assert final.resolved_event['opposed_outcome']['winner'] == winner


def test_named_reference_derives_required_link_without_changing_source():
    records = [
        {'id': 'r12', 'source_excerpt': 'The knife rises (see Having Hold of the Knife).',
         'related_record_ids': [], 'dependencies': []},
        {'id': 'r14', 'source_excerpt': 'Having Hold of the Knife\nUse opposed Brawl vs POW.',
         'related_record_ids': [], 'dependencies': []},
    ]
    before = deepcopy(records)
    linked, diagnostics = link_records(records)
    assert not diagnostics
    assert linked[0]['related_record_ids'] == ['r14']
    assert linked[0]['dependencies'][0]['kind'] == 'required_for_adjudication'
    assert linked[0]['source_excerpt'] == before[0]['source_excerpt']
    assert records == before


def test_ambiguous_heading_requires_explicit_target():
    records = [
        {'id': 'r1', 'source_excerpt': 'Refer (see Knife).', 'dependencies': []},
        {'id': 'r2', 'source_excerpt': 'Knife\nFirst rule.', 'dependencies': []},
        {'id': 'r3', 'source_excerpt': 'Knife\nSecond rule.', 'dependencies': []},
    ]
    _, diagnostics = link_records(records)
    assert diagnostics[0]['code'] == 'ambiguous_named_reference'
    records[0]['dependencies'] = [{'record_id': 'r3', 'kind': 'background', 'source_quote': 'see Knife'}]
    linked, diagnostics = link_records(records)
    assert linked[0]['dependencies'][0]['kind'] == 'required_for_adjudication'
    assert not any(item['code'] == 'ambiguous_named_reference' for item in diagnostics)


def test_private_opposed_receipt_never_enters_narrator_handoff():
    state = _state()
    state.pending_checks['u1'] = {
        'type': 'skill', 'check_id': 'check-1', 'action_basis': 'Private source page 11',
        'opposed': {**REQUEST, 'opponent_roll': 30, 'opponent_tier': 'hard'},
    }
    private_authority = turn_context.authority_block(state)
    narrator_authority = turn_context.authority_block(state, include_private_checks=False)
    assert 'opponent_roll' in private_authority
    assert 'opponent_roll' not in narrator_authority
    assert 'Private source page 11' not in narrator_authority
    narrator_prompt = keeper._build_dynamic_prompt(state, 'u1', include_private_checks=False)
    assert 'opponent_roll' not in narrator_prompt
    assert 'Private source page 11' not in narrator_prompt
    result = {'ok': True, 'pending': True, 'opposed': state.pending_checks['u1']['opposed'],
              'action_basis': 'Private source page 11'}
    fact = _describe_tool_call('skill_check', result)
    assert 'opponent_roll' not in fact and 'Private source page 11' not in fact
    resolved_fact = _describe_tool_call('skill_check', {
        'ok': True, 'resolved': True,
        'opposed_outcome': {'winner': 'opponent', 'applicable_consequence': 'The knife escapes'},
    })
    assert 'opposed_winner=opponent' in resolved_fact
    assert 'The knife escapes' not in resolved_fact
    resolved = build_resolved_check_outcome_block({
        'investigator': 'Marco', 'skill': '格鬥（鬥毆）', 'roll': 40,
        'player_declaration': '抓住飛來的刀', 'action_basis': 'Private source page 11',
        'opposed_outcome': opposed_checks.resolve(state.pending_checks['u1']['opposed'], 'regular'),
    })
    assert 'opponent_roll' not in resolved and 'Private source page 11' not in resolved
    assert '"winner": "opponent"' in resolved


def _authoring_registry(text: str) -> dict:
    return {'units': [{'id': 'u1', 'source_id': 'source-u1', 'span': [0, len(text)],
                       'page': 1, 'source_pages': [1], 'chapter_id': 'c1', 'text': text}]}


def test_unresolved_named_reference_blocks_complete_authoring():
    source = 'The knife moves (see Missing Section).'
    record = scenario_authoring.blank_record('u1', 1)
    record.update(name='Knife', kp_text='刀開始移動，參見缺失的章節。', uncertainty='')
    registry = _authoring_registry(source)
    scenario_authoring.compile_records([record], registry, {'u1'}, complete=False)
    with pytest.raises(scenario_authoring.Diagnostics) as caught:
        scenario_authoring.compile_records([record], registry, {'u1'}, complete=True)
    assert any(issue['code'] == 'UNRESOLVED_DEPENDENCY' for issue in caught.value.issues)


def test_source_verified_external_rule_reference_can_be_classified():
    source = 'Resolve it (see Fighting Maneuvers).'
    record = scenario_authoring.blank_record('u1', 1)
    record.update(name='Fight', kp_text='依格鬥動作規則處理。', uncertainty='',
                  external_references=[{'source_quote': '(see Fighting Maneuvers)',
                                        'kind': 'external_rulebook',
                                        'reason': 'Core rulebook section outside this scenario'}])
    compiled = scenario_authoring.compile_records([record], _authoring_registry(source), {'u1'}, complete=True)
    assert compiled[0]['cross_reference_diagnostics'][0]['code'] == 'classified_external_reference'
    record['external_references'][0]['source_quote'] = '(see Missing Section)'
    with pytest.raises(scenario_authoring.Diagnostics) as caught:
        scenario_authoring.compile_records([record], _authoring_registry(source), {'u1'}, complete=True)
    assert {'INVALID_EXTERNAL_REFERENCE', 'UNRESOLVED_DEPENDENCY'} <= {i['code'] for i in caught.value.issues}


def test_exact_explicit_dependency_resolves_reference_without_detectable_heading():
    source = 'The knife moves (see Knife Reaction).'
    target = 'The wielder may use Brawl against POW.'
    registry = _authoring_registry(source)
    registry['units'].append({'id': 'u2', 'source_id': 'source-u2', 'span': [0, len(target)],
                              'page': 2, 'source_pages': [2], 'chapter_id': 'c1', 'text': target})
    first = scenario_authoring.blank_record('u1', 1)
    first.update(name='Knife', kp_text='刀開始移動。', uncertainty='',
                 dependencies=[{'record_id': 'r2', 'kind': 'required_for_adjudication',
                                'condition': '', 'source_quote': '(see Knife Reaction)'}])
    second = scenario_authoring.blank_record('u2', 2)
    second.update(name='Reaction', kp_text='持有者可使用格鬥對抗意志。', uncertainty='')
    compiled = scenario_authoring.compile_records([first, second], registry, {'u1', 'u2'}, complete=True)
    assert compiled[0]['related_record_ids'] == ['r2']
    assert not any(item['code'] == 'unresolved_named_reference'
                   for item in compiled[0]['cross_reference_diagnostics'])


def test_unchanged_pending_check_uses_reminder_only_for_same_validated_wait():
    state = _state()
    state.timeline_id = 'timeline-1'
    pending = {'type': 'skill', 'check_id': 'check-1', 'timeline_id': state.timeline_id}
    state.pending_checks['u1'] = pending
    result = MechanicResult(
        success=True, action_type='skill_check', narrative_facts=[], state_delta=StateDelta(),
        check_status={'tool_event_count': 0, 'state_changed': False},
        turn_resolution=TurnResolution(disposition='await_check', actor_character_id='legacy-user:u1',
                                       waiting_for='legacy-user:u1', check_id='check-1',
                                       validation_code='validated'),
    )
    assert '檢定仍在等待' in _unchanged_pending_reply(state, 'u1', result, {'u1': deepcopy(pending)}, {}, {})
    result.check_status['tool_event_count'] = 1
    assert not _unchanged_pending_reply(state, 'u1', result, {'u1': deepcopy(pending)}, {}, {})
    result.check_status['tool_event_count'] = 0
    result.turn_resolution.check_id = 'another-check'
    assert not _unchanged_pending_reply(state, 'u1', result, {'u1': deepcopy(pending)}, {}, {})
