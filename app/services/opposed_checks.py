"""Persisted scenario-specific opposed checks; no model-supplied dice results."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app import dice

FIELDS = ('opponent_skill', 'opponent_value', 'tie_winner', 'source', 'on_win', 'on_loss')
SCHEMA = {
    'type': 'object',
    'description': '劇本指定的對抗檢定。對手由程式擲一次並保存，不可先用 npc_skill_check 或自行填骰果。',
    'properties': {
        'opponent_skill': {'type': 'string'},
        'opponent_value': {'type': 'integer', 'minimum': 0, 'maximum': 100},
        'tie_winner': {'type': 'string', 'enum': ['player', 'opponent', 'neither'],
                       'description': '依適用規則及目前行動方決定平手勝方；不能省略或猜測。'},
        'source': {'type': 'string', 'description': '本次適用的劇本條件與規則引用，不是別種相似動作。'},
        'on_win': {'type': 'string', 'description': '勝出後適用的後果；數值及物品仍須工具結算。'},
        'on_loss': {'type': 'string', 'description': '敗北後適用的後果；不得當成已套用的傷害。'},
    },
    'required': list(FIELDS),
    'additionalProperties': False,
}


def contract(value: Any) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError('對抗檢定須提供完整規則，不接受模型提供骰果。')
    result = deepcopy(value)
    for field in ('opponent_skill', 'source', 'on_win', 'on_loss'):
        if not isinstance(result[field], str) or not result[field].strip() or len(result[field]) > 400:
            raise ValueError('對抗檢定缺少有效的能力、來源或勝敗後果。')
    if type(result['opponent_value']) is not int or not 0 <= result['opponent_value'] <= 100:
        raise ValueError('對抗能力數值無效。')
    if result['tie_winner'] not in ('player', 'opponent', 'neither'):
        raise ValueError('對抗檢定必須指定平手規則。')
    return result


def request_part(receipt: dict | None) -> dict | None:
    return {key: receipt[key] for key in FIELDS} if receipt else None


def roll_opponent(request: dict | None) -> dict | None:
    if request is None:
        return None
    roll = dice.skill_check(request['opponent_value'])
    return {**deepcopy(request), 'opponent_roll': roll.roll, 'opponent_tier': roll.tier}


def resolve(receipt: dict | None, player_tier: str) -> dict | None:
    if receipt is None:
        return None
    player, opponent = dice.TIER_RANK[player_tier], dice.TIER_RANK[receipt['opponent_tier']]
    if max(player, opponent) < dice.TIER_RANK['regular']:
        winner = 'neither'
    elif player == opponent:
        winner = receipt['tie_winner']
    else:
        winner = 'player' if player > opponent else 'opponent'
    return {**deepcopy(receipt), 'player_tier': player_tier, 'winner': winner,
            'applicable_consequence': receipt['on_win'] if winner == 'player' else receipt['on_loss'] if winner == 'opponent' else ''}


def public_text(outcome: dict | None) -> str:
    if not outcome:
        return ''
    return {'player': '對抗結果：你勝出。', 'opponent': '對抗結果：對手勝出。',
            'neither': '對抗結果：雙方均未勝出，尚未達成目的。'}[outcome['winner']]
