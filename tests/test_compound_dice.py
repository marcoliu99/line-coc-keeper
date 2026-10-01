"""Observable bounded dice and damage-bonus contracts."""
from unittest.mock import patch

import pytest

from app.dice import (
    calculate_impaling_damage,
    max_expression_value,
    roll_expression,
    roll_weapon_damage,
)


def test_compound_result_preserves_existing_contract():
    with patch('app.dice.random.randint', side_effect=[6, 2, 3]):
        result = roll_expression('2d6 + d4 - 2')
    assert result.expression == '2d6 + d4 - 2'
    assert result.rolls == [6, 2, 3]
    assert result.modifier == -2
    assert result.total == 9
    assert result.describe() == '2d6 + d4 - 2 = [6+2+3]-2 = 9'


@pytest.mark.parametrize(('expression', 'maximum'), [
    ('2d6+1d4-2', 14), ('1d6-1d4', 5), ('-2d6+3', 1),
    ('+1d4', 4), ('-2', -2), ('0', 0), ('100d1000', 100000),
])
def test_maximum_uses_bounded_grammar(expression, maximum):
    assert max_expression_value(expression) == maximum


@pytest.mark.parametrize('expression', [
    '', ' ', '0d6', '101d6', '60d6+41d4', '1d1', '1d1001',
    '1d6++2', '1d6*2', '1d6/2', '__import__("os")', 'DB', 'half DB',
    '(1d6)', '1 0d6', '1 d6', '1000001', '1+' * 32 + '1', '1' * 257,
])
def test_invalid_input_rejected_without_rng(expression):
    with patch('app.dice.random.randint') as rng:
        with pytest.raises(ValueError):
            roll_expression(expression)
        with pytest.raises(ValueError):
            max_expression_value(expression)
        rng.assert_not_called()


@pytest.mark.parametrize(('db', 'policy', 'contribution'), [
    ('-1', 'full', -1), ('0', 'full', 0), ('-1', 'half', -1),
    ('-2', 'half', -1), ('3', 'half', 1), ('+1d4+1d6', 'half', 3),
    ('1d4', 'none', 0),
])
def test_normal_damage_applies_bonus_policy(db, policy, contribution):
    with patch('app.dice.random.randint', side_effect=[4, 3, 4]) as rng:
        result = roll_weapon_damage('1d6', db, db_policy=policy)
    assert result.damage_bonus_total == contribution
    assert result.total == 4 + contribution
    if policy == 'none' or db in ('-1', '-2', '0', '3'):
        assert rng.call_count == 1
    else:
        assert result.damage_bonus_roll.total == 7


def test_compound_impale_rolls_weapon_only():
    with patch('app.dice.random.randint', side_effect=[3, 2]) as rng:
        result = calculate_impaling_damage('1d6+1d4+2', '1d4+1d6', True, db_policy='half')
    assert result.max_weapon_damage == 12
    assert result.max_damage_bonus == 5
    assert result.reroll.total == 7
    assert result.total == 24
    assert rng.call_count == 2


def test_nonimpale_negative_db_needs_no_rng():
    with patch('app.dice.random.randint') as rng:
        result = calculate_impaling_damage('2d6', '-1', False, db_policy='half')
    assert result.total == 11
    assert result.reroll is None
    rng.assert_not_called()


@pytest.mark.parametrize(('weapon', 'db'), [('1d6', 'invalid'), ('101d6', '0')])
def test_damage_validates_before_any_draw(weapon, db):
    with patch('app.dice.random.randint') as rng:
        with pytest.raises(ValueError):
            roll_weapon_damage(weapon, db)
        rng.assert_not_called()
