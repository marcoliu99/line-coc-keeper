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
