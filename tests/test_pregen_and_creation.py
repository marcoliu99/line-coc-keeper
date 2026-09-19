import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))

from app import creation, pregen_extractor
from app.models import BASE_SKILLS, CreationSession


class PregenLuckRollTests(unittest.TestCase):
    """Regression tests for pregen_to_character rolling its own LUCK instead
    of reading it from the scenario PDF's extracted pregen sheet."""

    def _pregen(self, **overrides):
        base = {
            "name": "Test Pregen", "occupation": "私家偵探",
            "str_": 50, "con": 50, "siz": 50, "dex": 50, "app": 50, "int_": 50, "pow_": 50, "edu": 50,
            "luck": 65,
        }
        base.update(overrides)
        return base

    def test_luck_is_rolled_not_read_from_pdf_value(self):
        with patch("app.models.random.randint", return_value=4):
            char = pregen_extractor.pregen_to_character(self._pregen(luck=65), owner_id="p1")
        # 3D6*5 with every die forced to 4 -> (4+4+4)*5 = 60, not the pdf's 65.
        self.assertEqual(char.luck, 60)
        self.assertNotEqual(char.luck, 65)

    def test_luck_still_rolls_when_pdf_has_no_luck_field(self):
        pregen = self._pregen()
        del pregen["luck"]
        char = pregen_extractor.pregen_to_character(pregen, owner_id="p1")
        self.assertTrue(15 <= char.luck <= 90)  # 3D6*5 range
        self.assertEqual(char.luck % 5, 0)

    def test_two_claims_of_the_same_pregen_roll_independently(self):
        pregen = self._pregen(luck=65)
        rolls = iter([1, 1, 1, 6, 6, 6])
        with patch("app.models.random.randint", side_effect=lambda a, b: next(rolls)):
            first = pregen_extractor.pregen_to_character(pregen, owner_id="p1")
            second = pregen_extractor.pregen_to_character(pregen, owner_id="p2")
        self.assertEqual(first.luck, 15)
        self.assertEqual(second.luck, 90)
        self.assertNotEqual(first.luck, second.luck)
        # The shared library entry itself is untouched by either claim.
        self.assertEqual(pregen["luck"], 65)

    def test_other_eight_attributes_still_use_extracted_or_default_values(self):
        pregen = self._pregen(str_=70)
        del pregen["con"]  # missing -> falls back to 50, unaffected by this change
        char = pregen_extractor.pregen_to_character(pregen, owner_id="p1")
        self.assertEqual(char.str_, 70)
        self.assertEqual(char.con, 50)


class PregenPreviewLuckLabelTests(unittest.TestCase):
    """The /coc pregens preview text must not show LUCK as if it were a fixed
    stat, since /coc usepregen always rerolls it."""

    def test_preview_labels_luck_as_reroll_when_pdf_value_present(self):
        from app import commands
        text = commands._pregen_full_sheet_text({"name": "A", "occupation": "醫生", "str_": 50, "luck": 65}, 1)
        self.assertIn("卡面 LUCK 65", text)
        self.assertIn("將重新骰定", text)
        self.assertNotIn("LUCK 65\n", text)  # not shown as a plain fixed stat

    def test_preview_labels_luck_as_pending_when_pdf_has_no_value(self):
        from app import commands
        text = commands._pregen_full_sheet_text({"name": "A", "occupation": "醫生", "str_": 50}, 1)
        self.assertIn("LUCK 將於取用時骰定", text)


class TranslateSkillNamesCanonicalizationTests(unittest.TestCase):
    """Regression tests for _translate_skill_names bypassing the static
    SKILL_ALIASES table (only consulting the dynamic self-learned
    dictionary), which let extraction produce two separate entries for
    what's really the same BASE_SKILLS skill."""

    def test_alias_pairs_already_in_skill_aliases_collapse_to_one_entry(self):
        raw = {"鬥毆": 70, "格鬥（鬥毆）": 70, "手槍": 40, "話術": 60, "電器維修": 10, "估價": 5}
        result = pregen_extractor._translate_skill_names(raw)
        self.assertEqual(result, {
            "格鬥（鬥毆）": 70, "射擊（手槍）": 40, "快速交談": 60,
            "電氣維修": 10, "鑑定": 5,
        })

    def test_newly_added_aliases_collapse_to_one_entry(self):
        raw = {"求生": 10, "生存": 10, "鎖匠": 1, "開鎖": 1, "自然世界": 10, "自然學": 10, "喬裝": 60}
        result = pregen_extractor._translate_skill_names(raw)
        self.assertEqual(result, {"生存": 10, "開鎖": 1, "自然學": 10, "偽裝": 60})

    def test_conflicting_values_for_the_same_aliased_skill_keep_the_higher_one(self):
        # Same underlying skill (Intimidate), inconsistently transcribed at
        # two points in the source text with two different values -- must
        # not silently pick whichever happened to iterate last.
        raw_high_first = {"恐嚇": 60, "威嚇": 35}
        raw_low_first = {"威嚇": 35, "恐嚇": 60}
        self.assertEqual(pregen_extractor._translate_skill_names(raw_high_first), {"恐嚇": 60})
        self.assertEqual(pregen_extractor._translate_skill_names(raw_low_first), {"恐嚇": 60})

    def test_unresolvable_homebrew_skill_name_passes_through_unchanged(self):
        raw = {"深潛者辨識": 15}
        self.assertEqual(pregen_extractor._translate_skill_names(raw), {"深潛者辨識": 15})

    def test_real_scenario_duplicate_pattern_deduplicates_correctly(self):
        """End-to-end reproduction of the exact reported symptom: a real
        extracted skill list with ~11 alias pairs collapses to one entry
        each, and every canonical BASE_SKILLS name ends up present."""
        raw = {
            "鬥毆": 70, "格鬥（鬥毆）": 70, "話術": 60, "快速交談": 60, "偽裝": 60,
            "手槍": 40, "射擊（手槍）": 40, "步槍／霰彈槍": 25, "射擊（步槍/霰彈槍）": 25,
            "電器維修": 10, "電氣維修": 10, "自然世界": 10, "自然學": 10,
            "求生": 10, "生存": 10, "鎖匠": 1, "開鎖": 1, "估價": 5, "鑑定": 5,
            "駕駛／飛行": 1, "駕駛": 1,
        }
        result = pregen_extractor._translate_skill_names(raw)
        for canonical in ("格鬥（鬥毆）", "快速交談", "射擊（手槍）", "射擊（步槍/霰彈槍）",
                          "電氣維修", "自然學", "生存", "開鎖", "鑑定", "駕駛"):
            with self.subTest(canonical=canonical):
                self.assertIn(canonical, result)
        # No leftover raw/alias spellings sitting alongside their canonical form.
        for alias in ("鬥毆", "話術", "步槍／霰彈槍", "電器維修", "自然世界", "求生", "鎖匠", "估價", "駕駛／飛行"):
            with self.subTest(alias=alias):
                self.assertNotIn(alias, result)


class CreationAllocateCanonicalizationTests(unittest.TestCase):
    """Regression tests for /coc alloc silently opening a duplicate,
    unresolvable skill entry when the player types a common shorthand
    instead of the official BASE_SKILLS name."""

    def _session(self) -> CreationSession:
        return CreationSession(
            name="Tester", owner_id="p1", skills=dict(BASE_SKILLS),
            occ_points_total=200, occ_points_remaining=200,
            interest_points_total=100, interest_points_remaining=100,
        )

    def test_shorthand_skill_name_is_canonicalized_before_storing(self):
        session = self._session()
        result = creation.allocate(session, "occ", "手槍", 30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["skill"], "射擊（手槍）")
        # Added on top of the official base rate (20), not a fresh "手槍" entry
        # starting from the old hardcoded 20 default.
        self.assertEqual(session.skills["射擊（手槍）"], BASE_SKILLS["射擊（手槍）"] + 30)
        self.assertNotIn("手槍", session.skills)

    def test_allocating_twice_with_different_spellings_stacks_on_one_entry(self):
        session = self._session()
        creation.allocate(session, "occ", "手槍", 20)
        result = creation.allocate(session, "occ", "射擊（手槍）", 10)
        self.assertTrue(result["ok"])
        self.assertEqual(session.skills["射擊（手槍）"], BASE_SKILLS["射擊（手槍）"] + 30)
        self.assertEqual(len([k for k in session.skills if "手槍" in k]), 1)

    def test_unknown_homebrew_skill_name_passes_through_unchanged(self):
        session = self._session()
        result = creation.allocate(session, "int", "深潛者辨識", 15)
        self.assertTrue(result["ok"])
        self.assertEqual(result["skill"], "深潛者辨識")
        self.assertEqual(session.skills["深潛者辨識"], 20 + 15)


if __name__ == "__main__":
    unittest.main()
