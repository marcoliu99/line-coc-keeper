import sys
import os
import tempfile
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))

from app import creation, db, legacy_commands, pregen_extractor
from app.models import BASE_SKILLS, CreationSession


class PregenLuckRollTests(unittest.TestCase):
    def _pregen(self, **overrides):
        data = {
            "name": "Test Pregen", "occupation": "私家偵探",
            "str_": 50, "con": 50, "siz": 50, "dex": 50,
            "app": 50, "int_": 50, "pow_": 50, "edu": 50, "luck": 65,
        }
        data.update(overrides)
        return data

    def test_luck_is_rolled_instead_of_reading_pdf_value(self):
        with patch("app.models.random.randint", return_value=4):
            character = pregen_extractor.pregen_to_character(self._pregen(), owner_id="p1")
        self.assertEqual(character.luck, 60)
        self.assertNotEqual(character.luck, 65)

    def test_luck_rolls_when_pdf_has_no_luck_field(self):
        pregen = self._pregen()
        del pregen["luck"]
        character = pregen_extractor.pregen_to_character(pregen, owner_id="p1")
        self.assertTrue(15 <= character.luck <= 90)
        self.assertEqual(character.luck % 5, 0)

    def test_claims_are_independent_and_do_not_mutate_shared_pregen(self):
        pregen = self._pregen()
        rolls = iter([1, 1, 1, 6, 6, 6])
        with patch("app.models.random.randint", side_effect=lambda _a, _b: next(rolls)):
            first = pregen_extractor.pregen_to_character(pregen, owner_id="p1")
            second = pregen_extractor.pregen_to_character(pregen, owner_id="p2")
        self.assertEqual(first.luck, 15)
        self.assertEqual(second.luck, 90)
        self.assertEqual(pregen["luck"], 65)


class PregenPreviewTests(unittest.TestCase):
    def test_preview_labels_luck_and_caps_skills(self):
        pregen = {
            "name": "A", "occupation": "醫生", "str_": 50, "luck": 65,
            "skills": {f"技能{i}": 100 - i for i in range(20)},
        }
        text = legacy_commands._pregen_full_sheet_text(pregen, 1)
        self.assertIn("卡面 LUCK 65", text)
        self.assertIn("將重新骰定", text)
        self.assertNotIn("LUCK 65\n", text)
        skill_line = next(line for line in text.splitlines() if line.startswith("主要技能："))
        shown = skill_line.removeprefix("主要技能：").split("、")
        self.assertEqual(len(shown), 12)
        self.assertEqual(shown[0], "技能0 100%")

    def test_preview_labels_missing_luck(self):
        text = legacy_commands._pregen_full_sheet_text(
            {"name": "A", "occupation": "醫生", "str_": 50}, 1
        )
        self.assertIn("LUCK 將於取用時骰定", text)


class SkillCanonicalizationTests(unittest.TestCase):
    def test_extracted_aliases_collapse_and_keep_higher_numeric_value(self):
        result = pregen_extractor._translate_skill_names({
            "鬥毆": 70, "格鬥（鬥毆）": 70, "手槍": 40,
            "恐嚇": 60, "威嚇": 35, "求生": 10,
        })
        self.assertEqual(result["格鬥（鬥毆）"], 70)
        self.assertEqual(result["射擊（手槍）"], 40)
        self.assertEqual(result["恐嚇"], 60)
        self.assertEqual(result["生存"], 10)
        self.assertNotIn("鬥毆", result)
        self.assertNotIn("威嚇", result)

    def test_unknown_homebrew_skill_is_preserved(self):
        self.assertEqual(
            pregen_extractor._translate_skill_names({"深潛者辨識": 15}),
            {"深潛者辨識": 15},
        )


class CreationAllocateTests(unittest.TestCase):
    def _session(self):
        return CreationSession(
            name="Tester", owner_id="p1", skills=dict(BASE_SKILLS),
            occ_points_total=200, occ_points_remaining=200,
            interest_points_total=100, interest_points_remaining=100,
        )

    def test_alias_allocations_stack_on_canonical_skill(self):
        session = self._session()
        self.assertTrue(creation.allocate(session, "occ", "手槍", 20)["ok"])
        result = creation.allocate(session, "occ", "射擊（手槍）", 10)
        self.assertTrue(result["ok"])
        self.assertEqual(session.skills["射擊（手槍）"], BASE_SKILLS["射擊（手槍）"] + 30)
        self.assertNotIn("手槍", session.skills)

    def test_homebrew_skill_passes_through(self):
        session = self._session()
        result = creation.allocate(session, "int", "深潛者辨識", 15)
        self.assertTrue(result["ok"])
        self.assertEqual(session.skills["深潛者辨識"], 35)


class MigrateSkillNamesTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)
        self.original_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        db._ensure_tables()

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_migrate_cleans_group_state_and_character_mirror(self):
        from scripts.migrate_skill_names import migrate

        db.set_json("group_states", "g1", {
            "group_id": "g1",
            "characters": {"u1": {"skills": {"鬥毆": 70, "格鬥（鬥毆）": 70, "威嚇": 35, "恐嚇": 60}}},
            "pregens": [{"skills": {"手槍": 40, "求生": 10}}],
        })
        db.set_json("characters", "g1:u1", {"conversation_id": "g1", "sheet": {"skills": {"手槍": 40}}})

        migrate()

        group = db.get_json("group_states", "g1")
        self.assertEqual(group["characters"]["u1"]["skills"], {"格鬥（鬥毆）": 70, "恐嚇": 60})
        self.assertEqual(group["pregens"][0]["skills"], {"射擊（手槍）": 40, "生存": 10})
        mirror = db.get_json("characters", "g1:u1")
        self.assertEqual(mirror["sheet"]["skills"], {"射擊（手槍）": 40})

    def test_migrate_is_idempotent_for_clean_data(self):
        from scripts.migrate_skill_names import migrate

        clean = {"group_id": "g2", "characters": {"u1": {"skills": {"偵查": 50}}}, "pregens": []}
        db.set_json("group_states", "g2", clean)
        migrate()
        self.assertEqual(db.get_json("group_states", "g2"), clean)


if __name__ == "__main__":
    unittest.main()
