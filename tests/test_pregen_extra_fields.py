import unittest

from app import pregen_extractor


class PregenExtraFieldTests(unittest.TestCase):
    def test_llm_specific_fields_are_preserved(self):
        pregen = {
            "name": "Ada",
            "str_": 50,
            "scenario_specific_relationship": "燈塔管理員",
            "extra_fields": {"信念": "真相優先"},
        }
        pregen_extractor._preserve_extra_fields(pregen)
        self.assertEqual(pregen["extra_fields"], {"信念": "真相優先", "scenario_specific_relationship": "燈塔管理員"})

    def test_manual_role_sheet_keeps_custom_sections_and_character_copy(self):
        parsed = pregen_extractor.parse_role_sheet_text(
            "【角色資料】\n姓名：Ada\n職業：研究員\n住所：海邊小屋\n"
            "【屬性】\n力量 STR：50\n體質 CON：50\n"
            "【技能】\n圖書館使用：40\n"
            "【特殊備註】\n只能在月光下閱讀。"
        )
        assert parsed is not None
        self.assertIn("住所", parsed["notes"])
        self.assertEqual(parsed["extra_fields"]["特殊備註"], "只能在月光下閱讀。")
        character = pregen_extractor.pregen_to_character(parsed, "user-1")
        self.assertEqual(character.extra_fields["特殊備註"], "只能在月光下閱讀。")


if __name__ == "__main__":
    unittest.main()


def test_open_prose_blank_fields_and_weapon_conditions_survive_import():
    prose = '相信承諾比金錢重要：即使欠款 $10，也不出賣朋友。\n但遇到危險時會猶豫，並非無條件勇敢。'
    text = ('【角色資料】\n姓名：Example\n職業：作家\n年齡：52（原卡）\n'
            '【屬性】\n力量 STR：60\n敏捷 DEX：55\n'
            '【思想與信念】\n' + prose + '\n【重要之人】\n\n'
            '【珍藏物品】\n舊相片，背後有手寫的地名。\n'
            '【財務原文備註】\n$10 on hand；幣別未在此欄註明。\n'
            '【武器】\n手槍\n傷害：1D10\n彈容量：6\n限制：只有特定條件下能使用。\n')
    parsed = pregen_extractor.parse_role_sheet_text(text)
    assert parsed is not None
    assert parsed['extra_fields']['思想與信念'] == prose
    assert parsed['extra_fields']['重要之人'] == ''
    assert '重要地點' not in parsed['extra_fields']
    assert parsed['key_connection'] == ''
    assert '傷害：1D10' in parsed['extra_fields']['武器原始描述']
    assert '特定條件' in parsed['extra_fields']['武器原始描述']
    char = pregen_extractor.pregen_to_character(parsed, 'owner')
    assert char.extra_fields['思想與信念'] == prose
    assert char.extra_fields['年齡'] == '52（原卡）'
    assert '$10 on hand' in char.extra_fields['財務原文備註']
    assert 'cash_balances' not in parsed


def test_only_explicit_starred_connection_sets_mechanical_connection():
    parsed = pregen_extractor.parse_role_sheet_text(
        '【角色資料】\n姓名：Example\n【屬性】\n力量 STR：50\n'
        '【珍藏物品】\n懷錶\n【關鍵背景連結】\n故鄉的家人'
    )
    assert parsed['key_connection'] == '故鄉的家人'
    assert parsed['extra_fields']['珍藏物品'] == '懷錶'


def test_approximate_age_is_prose_and_never_adjusts_attributes_twice():
    parsed = pregen_extractor.parse_role_sheet_text(
        '【角色資料】\n姓名：Example\n年紀：約三十歲\n【屬性】\n力量 STR：60\n教育 EDU：70'
    )
    char = pregen_extractor.pregen_to_character(parsed, 'owner')
    assert char.extra_fields['年齡'] == '約三十歲'
    assert char.str_ == 60 and char.edu == 70
