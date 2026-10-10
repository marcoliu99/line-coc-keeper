"""docs/specs/bug/d100_against_a_characteristic_is_a_check_design_spec.md: a public 1D100 is a check the player
rolls, and an opposed field the Keeper fills wrong says which field."""
from __future__ import annotations

import pytest

from app.checks import service as check_service
from app.keeper_tools import dice as dice_handlers
from app.keeper_tools import registry
from app.models import GroupState
from app.services import opposed_checks

REQUEST = {
    'opponent_skill': '說服', 'opponent_value': 40, 'tie_winner': 'opponent',
    'source': 'Dooley 不想談：「A different investigator may try a Charm, Fast Talk, Persuade…」',
    'on_win': 'Dooley 開口說出 Corbitt House 的事', 'on_loss': 'Dooley 把話題帶開',
}


def _roll(arguments: dict, role: str = 'player') -> dict:
    return dice_handlers.roll_dice(registry.ToolCall(GroupState(group_id='g'), arguments, [], [], role, 'roll_dice'))  # type: ignore[arg-type]


@pytest.mark.parametrize('expression', ['1d100', '1D100', 'd100', ' 1d100 ', '+1d100', '01d100', 'd0100'])
def test_a_public_percentile_roll_is_refused_toward_skill_check(expression):
    result = _roll({'expression': expression})
    assert not result['ok'] and 'skill_check' in result['error'] and 'secret' in result['error']


@pytest.mark.parametrize('arguments', [
    {'expression': '1d100', 'secret': True}, {'expression': '1d6'}, {'expression': '2d100'}, {'expression': '1d100+5'},
    {'expression': '1d100-1d100'},
])
def test_other_rolls_still_roll(arguments):
    assert _roll(arguments)['ok']


def test_a_string_secret_does_not_lift_the_refusal_and_a_non_string_expression_is_still_an_error():
    assert not _roll({'expression': '1d100', 'secret': 'false'})['ok']
    with pytest.raises(ValueError):
        _roll({'expression': 100})


@pytest.mark.parametrize('context', ['ooc_randomizer', 'game_resolution'])
def test_the_kp_assistants_percentile_rolls_and_a_player_turn_cannot_claim_its_context(context):
    assert _roll({'expression': '1d100', 'roll_context': context}, role='kp_assistant')['ok']
    assert not _roll({'expression': '1d100', 'roll_context': context})['ok']


def test_an_empty_opposed_field_is_named():
    with pytest.raises(ValueError, match='on_loss 是空的.*一般技能檢定'):
        opposed_checks.contract({**REQUEST, 'on_loss': '  '})


def test_an_overlong_opposed_field_is_named_with_its_length():
    with pytest.raises(ValueError, match='source 太長（900 字，上限 400 字）.*一般技能檢定'):
        opposed_checks.contract({**REQUEST, 'source': 'x' * 900})


def test_a_missing_or_extra_opposed_field_is_named():
    with pytest.raises(ValueError, match='缺少 on_win、on_loss'):
        opposed_checks.contract({k: v for k, v in REQUEST.items() if k not in ('on_win', 'on_loss')})
    with pytest.raises(ValueError, match='不接受 opponent_roll'):
        opposed_checks.contract({**REQUEST, 'opponent_roll': 9})


class _Result:
    tier, success = 'regular', True


def test_the_outcome_label_says_the_opposed_result_in_chinese():
    label = check_service._outcome_label(_Result(), {**REQUEST, 'winner': 'player'})
    assert label == 'regular 成功；對抗結果：你勝出'
    assert check_service._outcome_label(_Result(), {**REQUEST, 'winner': 'neither'}).endswith('對抗結果：雙方均未勝出，尚未達成目的')
