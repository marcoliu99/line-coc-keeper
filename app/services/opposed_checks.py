"""Persisted scenario-specific opposed checks; no model-supplied dice results."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app import dice

FIELDS = ('opponent_skill', 'opponent_value', 'tie_winner', 'source', 'on_win', 'on_loss')
MAX_TEXT = 400
NOT_FOR_PLAIN_CHECKS = '只有劇本明寫對手與其數值的對抗檢定才填 opposed；一般技能檢定（圖書館使用、心理學、快速交談……）整個 opposed 不要填。'
SCHEMA = {
    'type': 'object',
    'description': ('劇本指定的對抗檢定：只有劇本明寫對手和對手的數值（例如「對抗 Dooley 的說服 40」）才填。'
                    '一般技能檢定（圖書館使用、心理學、快速交談……）整個 opposed 都不要填。'
                    '對手由程式擲一次並保存，不可先用 npc_skill_check 或自行填骰果。'),
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
    if not isinstance(value, dict):
        raise ValueError(f'opposed 要是物件，欄位為 {"、".join(FIELDS)}。{NOT_FOR_PLAIN_CHECKS}')  # noqa: TRY004
    missing, extra = [f for f in FIELDS if f not in value], [f for f in value if f not in FIELDS]
    if missing or extra:
        parts = [f'缺少 {"、".join(missing)}' if missing else '', f'不接受 {"、".join(extra)}（對手的骰果由程式擲）' if extra else '']
        raise ValueError(f'對抗檢定的 opposed {"；".join(p for p in parts if p)}。{NOT_FOR_PLAIN_CHECKS}')
    result = deepcopy(value)
    for field in ('opponent_skill', 'source', 'on_win', 'on_loss'):
        text = result[field]
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f'對抗檢定的 {field} 是空的。{NOT_FOR_PLAIN_CHECKS}')
        if len(text) > MAX_TEXT:
            raise ValueError(f'對抗檢定的 {field} 太長（{len(text)} 字，上限 {MAX_TEXT} 字），請只寫這次適用的條件。{NOT_FOR_PLAIN_CHECKS}')
    if type(result['opponent_value']) is not int or not 0 <= result['opponent_value'] <= 100:
        raise ValueError('對抗能力數值無效。')
    if result['tie_winner'] not in ('player', 'opponent', 'neither'):
        raise ValueError('對抗檢定必須指定平手規則。')
    return result


def request_part(receipt: dict | None) -> dict | None:
    return {key: receipt[key] for key in FIELDS} if receipt else None


def roll_opponent(request: dict | None, dice_port: Any = None) -> dict | None:
    """Draw the opponent's roll once. ``dice_port`` is any object with ``skill_check``
    (a check's dice port); it defaults to the real dice."""
    if request is None:
        return None
    roll = (dice_port or dice).skill_check(request['opponent_value'])
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


def public_outcome(outcome: dict | None) -> dict | None:
    """Only the adjudicated branch may cross into narration/tool summaries."""
    if not outcome:
        return None
    return {'winner': outcome['winner'], 'applicable_consequence': outcome['applicable_consequence']}
