"""Keep the later travel verbs available to the restored legacy map parser."""

import pytest

from app import intent_parser


@pytest.mark.parametrize('text', [
    '直奔商店', '奔向大門', '趕往醫院', '趕到碼頭', '衝向門口',
    '衝進地下室', '跑向樓梯', '跑到二樓', '抵達宅邸', '來到地下室',
    '返回客廳', '折返走廊',
])
def test_travel_verb_is_recognized(text):
    assert intent_parser.has_movement_verb(text)


@pytest.mark.parametrize('text', [
    '他右手邊有把刀', '我拿走桌上的刀', '我帶走那本日記',
    '我收走證物', '我偷走鑰匙',
])
def test_carrying_is_not_movement(text):
    assert not intent_parser.has_movement_verb(text)
