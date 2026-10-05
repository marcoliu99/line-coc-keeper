"""A character reads as one name wherever a player sees it (``CHARACTER_DISPLAY_ALIASES``)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app import config, presentation
from app.services import turn_delivery, turn_fallback

ALIASES = {"The Tough Guy": "硬漢", "Nosy Neighbor": "鄰居"}


class CharacterAliasTests(unittest.TestCase):
    def test_the_default_changes_nothing(self):
        self.assertEqual(config.CHARACTER_DISPLAY_ALIASES, {})
        line = "The Tough Guy 的背包已確認包含「染血紙片」。"
        self.assertEqual(presentation.player_text(line), line)

    def test_a_system_line_uses_the_name_the_narration_uses(self):
        outcome = turn_delivery.observe_tool(
            "add_carried_item", {"ok": True, "investigator": "The Tough Guy", "carried_items": ["染血紙片"]}, 1,
            {"item": "染血紙片"})
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            self.assertEqual(presentation.player_text(outcome.public_text), "硬漢 的背包已確認包含「染血紙片」。")
            self.assertEqual(presentation.player_text("Nosy Neighbor 的檢定已建立。"), "鄰居 的檢定已建立。")

    def test_only_whole_names_are_replaced(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            for text in ("The Tough Guys", "xThe Tough Guy", "The Tough Guy2"):
                self.assertEqual(presentation.player_text(text), text)

    def test_the_longest_name_wins_and_the_mapping_is_idempotent(self):
        aliases = {"Marco": "馬可", "Marco Polo": "馬可波羅"}
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", aliases):
            once = presentation.player_text("Marco Polo 與 Marco")
            self.assertEqual(once, "馬可波羅 與 馬可")
            self.assertEqual(presentation.player_text(once), once)

    def test_a_replacement_is_never_searched_again(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"Ann": "Bob", "Bob": "Cy"}):
            self.assertEqual(presentation.player_text("Ann 與 Bob"), "Bob 與 Cy")

    def test_a_chinese_name_inside_a_longer_one_is_kept_by_listing_the_longer_name(self):
        """Chinese has no word boundary: the longer name has to be configured to be told apart."""
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"馬可": "Marco"}):
            self.assertEqual(presentation.player_text("馬可波羅與馬可"), "Marco波羅與Marco")
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"馬可": "Marco", "馬可波羅": "馬可波羅"}):
            self.assertEqual(presentation.player_text("馬可波羅與馬可"), "馬可波羅與Marco")

    def test_an_ascii_name_next_to_chinese_text_still_matches(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            self.assertEqual(presentation.player_text("The Tough Guy沿門邊走進房內"), "硬漢沿門邊走進房內")

    def test_an_alias_is_inserted_literally(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"Ann": r"安\1\g<0>"}):
            self.assertEqual(presentation.player_text("Ann 到了"), r"安\1\g<0> 到了")

    def test_the_stored_name_is_untouched(self):
        """Only what a player reads is mapped; ids, logs and saved state keep the registered name."""
        result = {"ok": True, "investigator": "The Tough Guy", "carried_items": []}
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            presentation.player_text("The Tough Guy")
        self.assertEqual(result["investigator"], "The Tough Guy")

    def test_a_fallback_message_is_mapped_like_any_other_line(self):
        text = f"The Tough Guy 這次行動尚未完整處理。{turn_fallback.guidance('executor_no_action')}"
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            self.assertTrue(presentation.player_text(text).startswith("硬漢 這次行動"))


class AliasSettingTests(unittest.TestCase):
    def read(self, value: str | None):
        env = {} if value is None else {"CHARACTER_DISPLAY_ALIASES": value}
        with patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop("CHARACTER_DISPLAY_ALIASES", None)
            before = list(config.INVALID_LOG_SETTINGS)
            try:
                return config._env_str_map("CHARACTER_DISPLAY_ALIASES"), list(config.INVALID_LOG_SETTINGS[len(before):])
            finally:
                config.INVALID_LOG_SETTINGS[:] = before

    def test_unset_and_empty_are_empty_and_not_reported(self):
        self.assertEqual(self.read(None), ({}, []))
        self.assertEqual(self.read("  "), ({}, []))

    def test_a_json_object_of_text_is_read(self):
        self.assertEqual(self.read('{"The Tough Guy": "硬漢"}'), ({"The Tough Guy": "硬漢"}, []))

    def test_anything_else_is_ignored_and_reported(self):
        for bad in ("not json", "[1, 2]", '{"a": 1}', '{"": "x"}', '"text"'):
            with self.subTest(value=bad):
                value, reported = self.read(bad)
                self.assertEqual(value, {})
                self.assertEqual([name for name, _kind, _fallback in reported], ["CHARACTER_DISPLAY_ALIASES"])


class FallbackWordingTests(unittest.TestCase):
    def test_no_action_does_not_put_the_blame_on_the_player(self):
        text = turn_fallback.guidance("executor_no_action")
        self.assertIn("劇本裡沒有足夠的內容", text)
        self.assertIn("沒有替它編造", text)
        self.assertIn("補上對象與方式", text)  # a vague action is still told how to retry
        self.assertNotIn("找到可以執行的處理", text)


if __name__ == "__main__":
    unittest.main()
