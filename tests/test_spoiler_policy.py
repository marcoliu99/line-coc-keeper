import asyncio
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import combat, discord_bot, keeper, spoiler_policy
from app.commands.handlers import character as character_handler
from app.commands.handlers import system as system_handler
from app.models import Character, GroupState


class ReplyCollector:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


async def _noop(*args) -> None:
    return None


class SpoilerProtectionSwitchTests(unittest.TestCase):
    """§3.4/§10.1: SPOILER_PROTECTION_ENABLED gates the "劇情揭露節奏" class."""

    def test_sanitize_public_text_passes_through_when_disabled(self):
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            result = spoiler_policy.sanitize_public_text("秘密真相：兇手是管家", ["秘密真相：兇手是管家"])
        self.assertTrue(result.is_safe)

    def test_sanitize_public_text_blocks_matched_term_when_enabled(self):
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            result = spoiler_policy.sanitize_public_text(
                "你看到牆上寫著：秘密真相是管家", ["秘密真相是管家"]
            )
        self.assertFalse(result.is_safe)
        self.assertEqual(result.fallback_text, spoiler_policy.NEUTRAL_FALLBACK_TEXT)

    def test_sanitize_public_text_allows_safe_text_when_enabled(self):
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            result = spoiler_policy.sanitize_public_text("你走進了圖書館", ["秘密真相是管家"])
        self.assertTrue(result.is_safe)

    def test_sanitize_public_text_fails_closed_on_exception(self):
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            # A non-iterable protected_terms triggers an exception inside the
            # guard — it must fail closed (blocked), not silently pass.
            result = spoiler_policy.sanitize_public_text("任何文字", None)  # type: ignore[arg-type]
        self.assertFalse(result.is_safe)
        self.assertEqual(result.fallback_text, spoiler_policy.NEUTRAL_FALLBACK_TEXT)

    def test_redact_public_pregen_strips_notes_and_extra_fields_when_enabled(self):
        pregen = {
            "name": "Ada",
            "occupation": "偵探",
            "str_": 60,
            "skills": {"圖書館使用": 70},
            "key_connection": "已故的搭檔",
            "notes": "其實是兇手的同夥",
            "extra_fields": {"秘密": "劇情關鍵轉折"},
            "claimed_by": "user-123",
        }
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            redacted = spoiler_policy.redact_public_pregen(pregen)
        self.assertNotIn("notes", redacted)
        self.assertNotIn("extra_fields", redacted)
        self.assertNotIn("claimed_by", redacted)
        self.assertTrue(redacted["claimed"])
        self.assertEqual(redacted["name"], "Ada")
        self.assertEqual(redacted["key_connection"], "已故的搭檔")

    def test_redact_public_pregen_returns_full_data_when_disabled(self):
        pregen = {"name": "Ada", "notes": "劇情秘密", "extra_fields": {"x": "y"}}
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            redacted = spoiler_policy.redact_public_pregen(pregen)
        self.assertEqual(redacted, pregen)

    def test_redact_public_scenario_index_hides_numbers_when_enabled(self):
        index = {
            "npcs": [{"name": "神秘管家", "hp": 15, "abilities": ["隱藏刀刃"]}],
            "locations": [{"name": "地下室", "notes": "藏著屍體"}],
        }
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            safe = spoiler_policy.redact_public_scenario_index(index)
        self.assertEqual(safe["npcs"], [{"name": "神秘管家"}])
        self.assertNotIn("hp", safe["npcs"][0])
        self.assertEqual(safe["locations"], [{"name": "地下室"}])

    def test_redact_public_scenario_index_returns_full_data_when_disabled(self):
        index = {"npcs": [{"name": "神秘管家", "hp": 15}], "locations": []}
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            safe = spoiler_policy.redact_public_scenario_index(index)
        self.assertEqual(safe, index)

    def test_static_prompt_omits_spoiler_rules_when_disabled(self):
        state = GroupState("group-spoiler-off")
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            prompt = keeper._build_static_prompt(state)
        self.assertNotIn("條件式旁白", prompt)
        self.assertNotIn("你手上的「劇本內容」是只有你知道的機密資料", prompt)

    def test_static_prompt_includes_spoiler_rules_when_enabled(self):
        state = GroupState("group-spoiler-on")
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            prompt = keeper._build_static_prompt(state)
        self.assertIn("條件式旁白", prompt)
        self.assertIn("你手上的「劇本內容」是只有你知道的機密資料", prompt)

    def test_static_prompt_keeps_secret_goal_rule_when_spoiler_off_but_privacy_on(self):
        """Regression test for a code-review finding: the two switches must
        stay independent. Disabling SPOILER_PROTECTION_ENABLED alone must not
        also strip the private-info/secret-goal prompt rules — those are
        privacy isolation's job (§2.3 mechanisms #1/#2), and with the output
        guard also off in this configuration there's no second line of
        defense if the prompt stopped saying not to leak them.

        Uses full sentence fragments unique to each rule, not bare words like
        "秘密目標" or "send_private_info" — both also appear in unrelated,
        always-on bullets elsewhere in the prompt (the "★ 關鍵背景連結" bullet
        and the TOOLS schema respectively), so a bare substring check there
        would pass regardless of which switch is on."""
        state = GroupState("group-independent-switches")
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False), \
                patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", True):
            prompt = keeper._build_static_prompt(state)
        self.assertIn("只有你知道、只屬於那位玩家的私人動機", prompt)
        self.assertIn("反推出私人資訊", prompt)
        self.assertNotIn("條件式旁白", prompt)

    def test_static_prompt_drops_secret_goal_rule_when_privacy_off(self):
        state = GroupState("group-privacy-off")
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True), \
                patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
            prompt = keeper._build_static_prompt(state)
        self.assertNotIn("只有你知道、只屬於那位玩家的私人動機", prompt)
        self.assertNotIn("反推出私人資訊", prompt)
        self.assertIn("條件式旁白", prompt)

    def test_combat_damage_filter_hides_enemy_fields_when_enabled(self):
        result = {"ok": True, "side": "enemy", "hp": 3, "armor_absorbed": 2, "final_damage": 5}
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True), \
                patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", True):
            filtered = keeper._filter_public_combat_damage_result(result, "player")
        self.assertNotIn("hp", filtered)
        self.assertNotIn("armor_absorbed", filtered)

    def test_combat_damage_filter_passes_through_when_privacy_disabled(self):
        result = {"ok": True, "side": "enemy", "hp": 3, "armor_absorbed": 2, "final_damage": 5}
        with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
            filtered = keeper._filter_public_combat_damage_result(result, "player")
        self.assertEqual(filtered, result)


class PrivacyIsolationSwitchTests(unittest.TestCase):
    """§3.4/§10.1: PRIVACY_ISOLATION_ENABLED gates the "資料歸屬" class."""

    def test_sheet_text_excludes_secret_goal_when_enabled(self):
        char = Character(name="Ada", owner_id="user-1", secret_goal="其實是兇手的同夥")
        with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", True):
            text = char.sheet_text()
        self.assertNotIn("其實是兇手的同夥", text)

    def test_sheet_text_includes_secret_goal_when_disabled(self):
        char = Character(name="Ada", owner_id="user-1", secret_goal="其實是兇手的同夥")
        with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
            text = char.sheet_text()
        self.assertIn("其實是兇手的同夥", text)

    def test_combat_status_hides_enemy_hp_when_enabled(self):
        state = GroupState("group-combat-1")
        combat.start_combat(state)
        combat.add_npc(state, "怪物", hp=20, dex=50)
        with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", True):
            text = combat.status_text(state, include_private=False)
        self.assertIn("HP 未公開", text)

    def test_combat_status_reveals_enemy_hp_when_disabled(self):
        state = GroupState("group-combat-2")
        combat.start_combat(state)
        combat.add_npc(state, "怪物", hp=20, dex=50)
        with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
            text = combat.status_text(state, include_private=False)
        self.assertNotIn("HP 未公開", text)

    def test_scenario_image_visibility_gated_by_privacy_isolation_switch(self):
        """§3.4 mechanism #3: search_scenario_images/show_scenario_image route
        their public/kp_only check through spoiler_policy.filter_public_record
        (see app/keeper.py's _scenario_allowed_chapter_ids callers) — confirm
        the switch actually reaches that code path, not just
        filter_public_record in isolation."""
        with tempfile.TemporaryDirectory() as temp:
            original_library_dir = keeper.scenario_library.SCENARIO_LIBRARY_DIR
            keeper.scenario_library.SCENARIO_LIBRARY_DIR = Path(temp)
            try:
                text = (
                    "--- 第 1 頁 ---\n調查員：陳墨\nSTR 65 DEX 75 SAN 55\n"
                    "--- 第 2 頁 ---\n[圖片內容描述：一張地圖]\n"
                )
                scenario_id = keeper.scenario_library.save_scenario(
                    b"%PDF-1.4 fake", title="Privacy Switch Test", filename="t.pdf", preview=text[:200],
                    text=text, indexes={}, pregens=[], page_maps={2: {"id": "map-2"}},
                    page_images={1: b"page-1", 2: b"page-2"},
                )
                state = GroupState(group_id="g-privacy-switch")
                state.scenario_library_id = scenario_id
                state.context_chapter_ids = ["chapter-01"]

                # search_scenario_images/show_scenario_image operate solely on
                # the `state` argument passed below — no load_state/save_state
                # mock needed (they never call either).
                with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
                    player_search = keeper._execute_tool(
                        state, "search_scenario_images", {}, [], [], speaker_role="player"
                    )
                    self.assertEqual(
                        sorted(a["type"] for a in player_search["assets"]), ["character_sheet", "map"]
                    )

                    player_show_sheet = keeper._execute_tool(
                        state, "show_scenario_image", {"page_number": 1}, [], [], speaker_role="player"
                    )
                    self.assertTrue(player_show_sheet["ok"])
            finally:
                keeper.scenario_library.SCENARIO_LIBRARY_DIR = original_library_dir


class ProtectedTermCollectionTests(unittest.TestCase):
    def test_collect_protected_terms_gathers_secret_goals_and_kp_only_content(self):
        state = GroupState("group-terms")
        state.characters["user-1"] = Character(
            name="Ada", owner_id="user-1", secret_goal="其實是兇手的同夥"
        )
        state.established_facts.append({"text": "幕後黑手是市長", "visibility": "kp_only"})
        state.known_clues.append({"text": "地下室有密道", "visibility": "kp_only"})
        state.known_clues.append({"text": "公開的線索", "visibility": "public"})
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            terms = spoiler_policy.collect_protected_terms(state)
        self.assertIn("其實是兇手的同夥", terms)
        self.assertIn("幕後黑手是市長", terms)
        self.assertIn("地下室有密道", terms)
        self.assertNotIn("公開的線索", terms)

    def test_collect_protected_terms_empty_when_disabled(self):
        state = GroupState("group-terms-off")
        state.established_facts.append({"text": "幕後黑手是市長", "visibility": "kp_only"})
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            terms = spoiler_policy.collect_protected_terms(state)
        self.assertEqual(terms, [])

    def test_collect_protected_terms_drops_short_common_words(self):
        """Code-review finding: an unqualified short kp_only fact/clue like
        "地下室" or "市長" would otherwise block every later, ordinary mention
        of that word for the rest of the session."""
        state = GroupState("group-terms-short")
        state.established_facts.append({"text": "市長", "visibility": "kp_only"})
        state.known_clues.append({"text": "地下室", "visibility": "kp_only"})
        state.known_clues.append({"text": "地下室有一道密門通往下水道", "visibility": "kp_only"})
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            terms = spoiler_policy.collect_protected_terms(state)
        self.assertNotIn("市長", terms)
        self.assertNotIn("地下室", terms)
        self.assertIn("地下室有一道密門通往下水道", terms)

    def test_collect_protected_terms_excludes_secrets_later_disclosed_publicly(self):
        """Code-review finding: a KP who wants to disclose a previously
        kp_only fact/clue has no reveal command — the only path today is
        recording the exact same wording again with visibility="public".
        This must actually stop the old kp_only entry from still blocking
        replies, or that workaround is useless."""
        state = GroupState("group-terms-disclosed")
        state.established_facts.append({"text": "幕後黑手其實是市長本人", "visibility": "kp_only"})
        state.established_facts.append({"text": "幕後黑手其實是市長本人", "visibility": "public"})
        state.known_clues.append({"text": "地下室藏著一本日記", "visibility": "kp_only"})
        with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            terms = spoiler_policy.collect_protected_terms(state)
        self.assertNotIn("幕後黑手其實是市長本人", terms)
        self.assertIn("地下室藏著一本日記", terms)


class PregenAndIndexCommandTests(unittest.TestCase):
    """§10.2: /coc pregen and /coc index behavior under the switch, exercised
    through the actual live handlers (app/commands/handlers/character.py and
    system.py — not app/legacy_commands.py's `_handle_coc_command`, which
    this task discovered is unreachable dead code; see final report)."""

    def _pregen_state(self) -> GroupState:
        state = GroupState("group-pregen-cmd")
        state.pregens = [{
            "name": "Ada",
            "occupation": "偵探",
            "str_": 60,
            "skills": {"圖書館使用": 70},
            "notes": "其實是兇手的同夥",
            "extra_fields": {"秘密": "劇情關鍵轉折"},
            "claimed_by": "",
        }]
        return state

    def test_coc_pregen_redacts_notes_when_enabled(self):
        state = self._pregen_state()
        reply = ReplyCollector()
        with patch.object(character_handler, "load_state", return_value=state), \
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            asyncio.run(character_handler.handle_character_command(
                "group-pregen-cmd", "user-1", reply, _noop, ["/coc", "pregen", "1"]
            ))
        self.assertNotIn("其實是兇手的同夥", reply.messages[0])

    def test_coc_pregen_shows_notes_when_disabled(self):
        state = self._pregen_state()
        reply = ReplyCollector()
        with patch.object(character_handler, "load_state", return_value=state), \
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            asyncio.run(character_handler.handle_character_command(
                "group-pregen-cmd", "user-1", reply, _noop, ["/coc", "pregen", "1"]
            ))
        self.assertIn("其實是兇手的同夥", reply.messages[0])

    def _index_state(self) -> GroupState:
        state = GroupState("group-index-cmd")
        state.scenario_text = "劇本內文……"
        return state

    def test_coc_index_hides_hp_from_non_kp_when_enabled(self):
        state = self._index_state()
        reply = ReplyCollector()
        extracted = {"npcs": [{"name": "神秘管家", "hp": 15}], "locations": []}
        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler, "save_state", lambda *a, **k: None), \
                patch.object(system_handler.scenario_index, "extract_scenario_index", return_value=extracted), \
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            asyncio.run(system_handler.handle_system_command(
                "group-index-cmd", "player-1", reply, _noop, _noop, _noop, ["/coc", "index"]
            ))
        self.assertIn("神秘管家", reply.messages[0])
        self.assertNotIn("HP 15", reply.messages[0])

    def test_coc_index_shows_hp_to_kp_assistant_when_enabled(self):
        state = self._index_state()
        state.kp_assistant_user_id = "kp-1"
        reply = ReplyCollector()
        extracted = {"npcs": [{"name": "神秘管家", "hp": 15}], "locations": []}
        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler, "save_state", lambda *a, **k: None), \
                patch.object(system_handler.scenario_index, "extract_scenario_index", return_value=extracted), \
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            asyncio.run(system_handler.handle_system_command(
                "group-index-cmd", "kp-1", reply, _noop, _noop, _noop, ["/coc", "index"]
            ))
        self.assertIn("HP 15", reply.messages[0])

    def test_coc_index_shows_hp_to_everyone_when_disabled(self):
        state = self._index_state()
        reply = ReplyCollector()
        extracted = {"npcs": [{"name": "神秘管家", "hp": 15}], "locations": []}
        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler, "save_state", lambda *a, **k: None), \
                patch.object(system_handler.scenario_index, "extract_scenario_index", return_value=extracted), \
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            asyncio.run(system_handler.handle_system_command(
                "group-index-cmd", "player-1", reply, _noop, _noop, _noop, ["/coc", "index"]
            ))
        self.assertIn("HP 15", reply.messages[0])


class DigestCommandTests(unittest.TestCase):
    """§7.3/§10.2: /coc digest shows only the public slice when spoiler
    protection is on, and the full entry (including the KP-only `private`
    block) when it's off."""

    def test_coc_digest_shows_public_slice_when_enabled(self):
        state = GroupState("group-digest-cmd")
        state.kp_assistant_user_id = "kp-1"
        entry = {"public": {"facts": ["公開事實"]}, "private": {"facts": ["kp_only 真相"]}}
        reply = ReplyCollector()
        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler.scene_digest, "latest_digest", return_value=entry), \
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True):
            asyncio.run(system_handler.handle_system_command(
                "group-digest-cmd", "kp-1", reply, _noop, _noop, _noop, ["/coc", "digest"]
            ))
        self.assertIn("公開事實", reply.messages[0])
        self.assertNotIn("kp_only 真相", reply.messages[0])

    def test_coc_digest_shows_full_entry_when_disabled(self):
        state = GroupState("group-digest-cmd-2")
        state.kp_assistant_user_id = "kp-1"
        entry = {"public": {"facts": ["公開事實"]}, "private": {"facts": ["kp_only 真相"]}}
        reply = ReplyCollector()
        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler.scene_digest, "latest_digest", return_value=entry), \
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False):
            asyncio.run(system_handler.handle_system_command(
                "group-digest-cmd-2", "kp-1", reply, _noop, _noop, _noop, ["/coc", "digest"]
            ))
        self.assertIn("kp_only 真相", reply.messages[0])


class StartupPrivacyWarningTests(unittest.TestCase):
    """spec §12 item #6: PRIVACY_ISOLATION_ENABLED=false must not block
    startup, but must surface a loud one-time warning (not just the quiet
    per-call WARNING already logged deep inside spoiler_policy)."""

    def test_warns_when_privacy_isolation_disabled(self):
        with patch.object(discord_bot.config, "PRIVACY_ISOLATION_ENABLED", False), \
                patch.object(discord_bot, "_logger") as mock_logger, \
                patch.object(discord_bot, "observability") as mock_observability:
            discord_bot.warn_if_privacy_isolation_disabled()
        mock_logger.warning.assert_called_once()
        mock_observability.event.assert_called_once_with(
            "privacy.isolation.disabled", level=logging.WARNING, reason="startup_config"
        )

    def test_no_warning_when_privacy_isolation_enabled(self):
        with patch.object(discord_bot.config, "PRIVACY_ISOLATION_ENABLED", True), \
                patch.object(discord_bot, "_logger") as mock_logger, \
                patch.object(discord_bot, "observability") as mock_observability:
            discord_bot.warn_if_privacy_isolation_disabled()
        mock_logger.warning.assert_not_called()
        mock_observability.event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
