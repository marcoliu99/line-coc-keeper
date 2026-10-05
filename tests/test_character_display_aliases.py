"""A character reads as one name wherever a player sees it (``CHARACTER_DISPLAY_ALIASES``)."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import config, presentation
from app.commands.handlers import character
from app.discord_transport import delivery, help_ui
from app.models import Character, GroupState
from app.services import turn_delivery, turn_fallback

ALIASES = {"The Tough Guy": "硬漢", "Nosy Neighbor": "鄰居"}


class CharacterAliasTests(unittest.TestCase):
    def test_the_default_changes_nothing(self):
        self.assertEqual(config.CHARACTER_DISPLAY_ALIASES, {})
        line = "The Tough Guy 的背包已確認包含「染血紙片」。"
        self.assertEqual(presentation.character_aliases(line), line)

    def test_a_system_line_uses_the_name_the_narration_uses(self):
        outcome = turn_delivery.observe_tool(
            "add_carried_item", {"ok": True, "investigator": "The Tough Guy", "carried_items": ["染血紙片"]}, 1,
            {"item": "染血紙片"})
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            self.assertEqual(presentation.character_aliases(outcome.public_text), "硬漢 的背包已確認包含「染血紙片」。")
            self.assertEqual(presentation.character_aliases("Nosy Neighbor 的檢定已建立。"), "鄰居 的檢定已建立。")

    def test_only_whole_names_are_replaced(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            for text in ("The Tough Guys", "xThe Tough Guy", "The Tough Guy2"):
                self.assertEqual(presentation.character_aliases(text), text)

    def test_the_longest_name_wins_and_the_mapping_is_idempotent(self):
        aliases = {"Marco": "馬可", "Marco Polo": "馬可波羅"}
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", aliases):
            once = presentation.character_aliases("Marco Polo 與 Marco")
            self.assertEqual(once, "馬可波羅 與 馬可")
            self.assertEqual(presentation.character_aliases(once), once)

    def test_a_replacement_is_never_searched_again(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"Ann": "Bob", "Bob": "Cy"}):
            self.assertEqual(presentation.character_aliases("Ann 與 Bob"), "Bob 與 Cy")

    def test_a_chinese_name_inside_a_longer_one_is_kept_by_listing_the_longer_name(self):
        """Chinese has no word boundary: the longer name has to be configured to be told apart."""
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"馬可": "Marco"}):
            self.assertEqual(presentation.character_aliases("馬可波羅與馬可"), "Marco波羅與Marco")
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"馬可": "Marco", "馬可波羅": "馬可波羅"}):
            self.assertEqual(presentation.character_aliases("馬可波羅與馬可"), "馬可波羅與Marco")

    def test_an_ascii_name_next_to_chinese_text_still_matches(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            self.assertEqual(presentation.character_aliases("The Tough Guy沿門邊走進房內"), "硬漢沿門邊走進房內")

    def test_a_command_the_player_is_told_to_type_is_left_alone(self):
        """A character called "coc" or "check" must not turn "/coc check" into something the router rejects."""
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"coc": "庫克", "check": "偵探"}):
            self.assertEqual(presentation.character_aliases("請輸入 /coc check 後再試"), "請輸入 /coc check 後再試")
            self.assertEqual(presentation.character_aliases("coc 與 check 都來了"), "庫克 與 偵探 都來了")
            self.assertEqual(presentation.character_aliases("/COC  check"), "/COC  check")

    def test_words_deeper_in_a_command_are_kept_too(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"status": "狀態", "roll": "擲", "occ": "職", "coc": "庫克"}):
            for command in ("/coc create status", "/coc luck roll", "/coc alloc occ|int 技能名 點數", "/coc combat status"):
                with self.subTest(command=command):
                    self.assertEqual(presentation.character_aliases(command), command)
            self.assertEqual(presentation.character_aliases("status 與 roll"), "狀態 與 擲")

    def test_the_command_words_come_from_the_help_pages(self):
        words = presentation.command_words()
        self.assertLessEqual({"coc", "check", "luck", "roll", "status", "occ", "switch"}, words)
        self.assertNotIn("硬漢", words)

    def test_the_name_argument_of_a_command_is_still_mapped(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            self.assertEqual(presentation.character_aliases("/coc switch The Tough Guy"), "/coc switch 硬漢")

    def test_an_alias_is_inserted_literally(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"Ann": r"安\1\g<0>"}):
            self.assertEqual(presentation.character_aliases("Ann 到了"), r"安\1\g<0> 到了")

    def test_player_text_leaves_names_to_the_transport(self):
        """The reply pipeline's last step runs before the log is saved, so it must not rename anything."""
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES):
            self.assertEqual(presentation.player_text("The Tough Guy 的檢定已建立。"), "The Tough Guy 的檢定已建立。")


class FakeChannel:
    def __init__(self) -> None:
        self.send = AsyncMock(return_value=SimpleNamespace(id=7))


def interaction(done: bool = False):
    return SimpleNamespace(
        followup=SimpleNamespace(send=AsyncMock()),
        response=SimpleNamespace(is_done=lambda: done, send_message=AsyncMock(), edit_message=AsyncMock()),
    )


class TransportAliasTests(unittest.IsolatedAsyncioTestCase):
    """Every text a player reads is written with the table's name at the one place that sends it."""

    def setUp(self):
        patcher = patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_public_reply(self):
        channel = FakeChannel()
        await delivery.make_reply(channel)("The Tough Guy 的背包已確認包含「染血紙片」。")
        channel.send.assert_awaited_once_with("硬漢 的背包已確認包含「染血紙片」。")

    async def test_a_reply_is_chunked_after_the_names_are_mapped(self):
        channel = FakeChannel()
        await delivery.make_reply(channel)("The Tough Guy。" * 140)  # 1960 characters, 420 after mapping
        self.assertEqual([call.args[0] for call in channel.send.await_args_list], ["硬漢。" * 140])

    async def test_the_text_log_keeps_the_registered_name(self):
        """The log is for the operator, who matches it against the saved game."""
        channel = FakeChannel()
        with patch.object(delivery, "_logger") as logger, patch.object(config, "LOG_TEXT_ENABLED", True):
            await delivery.make_reply(channel)("The Tough Guy 到了")
        logger.info.assert_called_once_with("discord_reply text=%r", "The Tough Guy 到了")

    async def test_an_interaction_reply(self):
        inter = interaction()
        await delivery.make_interaction_reply(inter)("Nosy Neighbor 的檢定已建立。")
        inter.followup.send.assert_awaited_once_with("鄰居 的檢定已建立。")

    async def test_the_first_and_later_interaction_messages(self):
        for done, target in ((False, "response"), (True, "followup")):
            with self.subTest(done=done):
                inter = interaction(done)
                await delivery.send_interaction_message(inter, "The Tough Guy 不是這個操作的使用者", ephemeral=True)
                sender = getattr(inter, target).send_message if target == "response" else inter.followup.send
                sender.assert_awaited_once_with("硬漢 不是這個操作的使用者", ephemeral=True)

    async def test_an_edited_interaction_message(self):
        inter = interaction()
        await delivery.edit_interaction_message(inter, "輪到 The Tough Guy")
        inter.response.edit_message.assert_awaited_once_with(content="輪到 硬漢", view=None)

    async def test_a_direct_public_message(self):
        channel = FakeChannel()
        await delivery.send_direct_message(channel, "The Tough Guy，請選擇")
        channel.send.assert_awaited_once_with("硬漢，請選擇")

    async def test_a_private_message(self):
        user = SimpleNamespace(send=AsyncMock())
        with patch.object(delivery.gateway, "client", SimpleNamespace(get_user=lambda _id: user)):
            await delivery.send_dm("1", "The Tough Guy 的手卡")
        user.send.assert_awaited_once_with("硬漢 的手卡")

    async def test_nothing_changes_without_aliases(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {}):
            channel = FakeChannel()
            await delivery.make_reply(channel)("The Tough Guy 到了")
        channel.send.assert_awaited_once_with("The Tough Guy 到了")

    async def test_the_receipt_holds_what_the_player_read(self):
        channel = FakeChannel()
        channel.id = 5
        state = SimpleNamespace(group_id="g")
        saved = []
        with patch.object(delivery, "load_group_state", return_value=state), \
                patch("app.services.narrative_corrections.record_message", lambda st, mid, text: saved.append((mid, text))):
            await delivery.make_reply(channel)("The Tough Guy 到了")
        self.assertEqual(saved, [("7", "硬漢 到了")])


class TypedAliasTests(unittest.TestCase):
    """A player copies what they were shown, so a command that names a character accepts the alias."""

    def setUp(self):
        patcher = patch.object(config, "CHARACTER_DISPLAY_ALIASES", ALIASES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self):
        state = GroupState(group_id="g", timeline_id="t")
        first = Character(name="The Tough Guy", owner_id="u1", character_id="c1")
        second = Character(name="Nosy Neighbor", owner_id="u1", character_id="c2", active=False)
        state.characters_by_id.update({"c1": first, "c2": second})
        state.characters["u1"] = first
        state.set_active_character("u1", "c1")
        return state

    def test_the_alias_resolves_to_the_registered_name(self):
        known = ["The Tough Guy", "Nosy Neighbor"]
        self.assertEqual(presentation.registered_name("硬漢", known), "The Tough Guy")
        self.assertEqual(presentation.registered_name("The Tough Guy", known), "The Tough Guy")
        self.assertEqual(presentation.registered_name("路人", known), "路人")

    def test_a_registered_name_wins_over_an_alias_and_an_ambiguous_alias_is_not_guessed(self):
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"A": "B", "B": "C"}):
            self.assertEqual(presentation.registered_name("B", ["A", "B"]), "B")
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"A": "X", "B": "X"}):
            self.assertEqual(presentation.registered_name("X", ["A", "B"]), "X")

    def test_switch_accepts_the_name_the_player_was_shown(self):
        state = self.state()
        outcome = character._switch(state, "u1", ["/coc", "switch", "鄰居"])
        self.assertTrue(outcome.ok, outcome.text)
        self.assertEqual(state.get_active_character("u1").name, "Nosy Neighbor")

    def test_retire_names_a_character_the_player_can_type_back(self):
        state = self.state()
        outcome = character._retire(state, "u1", ["/coc", "retire", "硬漢"])
        self.assertTrue(outcome.ok, outcome.text)
        self.assertIn("The Tough Guy", outcome.text)  # delivery writes the alias; the command resolves it back
        self.assertTrue(character._switch(state, "u1", ["/coc", "switch", "硬漢"]).ok)

    def test_setskill_and_setconnection_accept_the_alias(self):
        state = self.state()
        self.assertTrue(character._setskill(state, "u1", ["/coc", "setskill", "硬漢", "偵查", "60"]).ok)
        self.assertTrue(character._setconnection(state, "u1", ["/coc", "setconnection", "硬漢", "舊識"]).ok)
        self.assertEqual(state.get_active_character("u1").skills["偵查"], 60)

    def test_an_alias_with_spaces_is_taken_before_the_other_arguments(self):
        state = self.state()
        with patch.object(config, "CHARACTER_DISPLAY_ALIASES", {"The Tough Guy": "硬 漢"}):
            self.assertTrue(character._setskill(state, "u1", ["/coc", "setskill", "硬", "漢", "偵查", "60"]).ok)
            self.assertEqual(state.get_active_character("u1").skills["偵查"], 60)
            self.assertTrue(character._setconnection(state, "u1", ["/coc", "setconnection", "硬", "漢", "舊", "識"]).ok)
            self.assertEqual(state.get_active_character("u1").key_connection, "舊 識")

    def test_a_registered_name_with_spaces_works_too(self):
        state = self.state()
        self.assertTrue(character._setskill(
            state, "u1", ["/coc", "setskill", "The", "Tough", "Guy", "偵查", "55"]).ok)
        self.assertEqual(state.get_active_character("u1").skills["偵查"], 55)

    def test_without_a_known_name_the_first_argument_is_the_name_as_before(self):
        self.assertEqual(presentation.leading_name(["路人", "偵查", "60"], ["The Tough Guy"], after=2),
                         ("路人", ["偵查", "60"]))

    def test_a_picker_shows_the_alias_and_keeps_the_registered_name_as_its_value(self):
        parent = SimpleNamespace(page=0)
        select = help_ui.HelpOptionSelect(parent, [("The Tough Guy", "The Tough Guy")])
        self.assertEqual([option.label for option in select.options], ["硬漢"])

    def test_a_wrong_name_is_still_refused(self):
        self.assertFalse(character._switch(self.state(), "u1", ["/coc", "switch", "路人"]).ok)


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
        for bad in ("not json", "[1, 2]", '{"a": 1}', '{"": "x"}', '"text"',
                    '{" ": "X"}', '{"The Tough Guy": ""}', '{"The Tough Guy": "  "}', '{"Ann": "Bob", "\\t": "x"}'):
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
