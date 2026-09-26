import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, legacy_commands
from app.commands.handlers import system
from app.models import GroupState
from app.repositories import group_state, manual_pregens


def _card(name="林文", strength=65):
    return {"name": name, "occupation": "記者", "str_": strength,
            "skills": {"圖書館使用": 70}, "source": "manual"}


def _context(scenario_id, pregens=None, source_hash="hash-1"):
    return {
        "manifest": {"title": scenario_id, "content_hash": source_hash},
        "text": "scenario", "active_chapter_id": "chapter-1",
        "context_chapter_ids": ["chapter-1"], "indexes": {"npcs": [], "locations": []},
        "pregens": pregens or [], "scene_maps": {}, "page_numbers": set(),
    }


class ManualPregenPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.db"
        self.patch = patch.object(db, "DB_PATH", self.path)
        self.patch.start()
        db._ensure_tables()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_asset_survives_newgame_and_is_scoped_to_scenario_and_group(self):
        original = GroupState("group-a")
        group_state.save_state(original)
        context = _context("scenario-a", [{"name": "林文", "occupation": "記者",
                                           "secret_goal": "尋找線索", "source": "llm_extracted"}])
        def add(conn):
            manual_pregens.store_upload(conn, "group-a", "scenario-a", _card(), "role_lin.md")
            original.pregens, _ = manual_pregens.install_pool(conn, "group-a", "scenario-a", context)
        group_state.save_state(original, mutate_tx=add)
        self.assertEqual(original.pregens[0]["str_"], 65)
        self.assertEqual(original.pregens[0]["secret_goal"], "尋找線索")

        group_state.save_state(GroupState("group-a"), reason="newgame")
        restored = group_state.load_state("group-a")
        with db.transaction() as conn:
            pool, _ = manual_pregens.install_pool(conn, "group-a", "scenario-a", context)
            other_scenario, _ = manual_pregens.install_pool(conn, "group-a", "scenario-b", _context("scenario-b"))
            other_group, _ = manual_pregens.install_pool(conn, "group-b", "scenario-a", context)
        self.assertEqual(restored.pregens, [])
        self.assertEqual(pool[0]["str_"], 65)
        self.assertNotIn("claimed_by", pool[0])
        self.assertEqual(other_scenario, [])
        self.assertNotIn("str_", other_group[0])

    def test_pending_card_binds_only_once_and_reupload_updates_id(self):
        with db.transaction() as conn:
            asset_id, _ = manual_pregens.store_upload(conn, "g", None, _card(), "role_lin.md")
            manual_pregens.bind_pending(conn, "g", "first")
            updated_id, action = manual_pregens.store_upload(conn, "g", "first", _card(strength=75), "role_lin.md")
            first, _ = manual_pregens.install_pool(conn, "g", "first", _context("first"))
            second, _ = manual_pregens.install_pool(conn, "g", "second", _context("second"), bind_unassigned=True)
        self.assertEqual((updated_id, action), (asset_id, "updated"))
        self.assertEqual(first[0]["str_"], 75)
        self.assertEqual(second, [])
        self.assertEqual(len(manual_pregens.list_assets("g", "first")), 1)

    def test_pending_card_updates_existing_scenario_asset_on_binding(self):
        with db.transaction() as conn:
            old_id, _ = manual_pregens.store_upload(conn, "g", "s", _card(strength=50), "old.md")
            manual_pregens.store_upload(conn, "g", None, _card(strength=75), "new.md")
            manual_pregens.bind_pending(conn, "g", "s")
        assets = manual_pregens.list_assets("g", "s")
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["asset_id"], old_id)
        self.assertEqual(assets[0]["pregen"]["str_"], 75)

    def test_pending_binding_rejects_filename_owned_by_another_character(self):
        with db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            manual_pregens.store_upload(conn, "g", "s", _card("林文"), "role_same.md")
            manual_pregens.store_upload(conn, "g", None, _card("陳雅"), "role_same.md")
            with self.assertRaises(ValueError):
                manual_pregens.bind_pending(conn, "g", "s")
        self.assertEqual(len(manual_pregens.list_assets("g", "s")), 1)
        self.assertEqual(len(manual_pregens.list_assets("g", None)), 1)

    def test_rebuild_keeps_claimed_card_without_unclaimed_duplicate(self):
        claimed = {**_card(), "claimed_by": "player"}
        with db.transaction() as conn:
            manual_pregens.store_upload(conn, "g", "s", _card(), "role_lin.md")
            for context in (
                _context("s"),
                _context("s", [{"name": "林文", "occupation": "記者", "source": "llm_extracted"}], "new-hash"),
            ):
                pool, _ = manual_pregens.install_pool(conn, "g", "s", context, claimed=[claimed])
                self.assertEqual(len(pool), 1)
                self.assertEqual(pool[0]["claimed_by"], "player")

    def test_asset_and_state_rollback_together_on_conflict_or_error(self):
        state = GroupState("g")
        group_state.save_state(state)
        stale = GroupState("g")
        with self.assertRaises(group_state.StateRevisionConflict):
            group_state.save_state(stale, mutate_tx=lambda conn: manual_pregens.store_upload(
                conn, "g", "s", _card(), "role_lin.md"))
        self.assertEqual(manual_pregens.list_assets("g", "s"), [])

        current = group_state.load_state("g")
        def fail_after_asset(conn):
            manual_pregens.store_upload(conn, "g", "s", _card(), "role_lin.md")
            raise RuntimeError("save failed")
        with self.assertRaises(RuntimeError):
            group_state.save_state(current, mutate_tx=fail_after_asset)
        self.assertEqual(manual_pregens.list_assets("g", "s"), [])
        self.assertEqual(group_state.load_state("g").state_revision, 1)

    def test_legacy_merged_snapshot_expires_when_pdf_changes(self):
        merged = {**_card(), "source": "merged", "secret_goal": "old"}
        with db.transaction() as conn:
            manual_pregens.capture_legacy(conn, "g", "s", [merged], "old-hash")
            base = [{"name": "林文", "occupation": "記者", "source": "llm_extracted"}]
            same, stale_same = manual_pregens.install_pool(conn, "g", "s", _context("s", base, source_hash="old-hash"))
            changed, stale_changed = manual_pregens.install_pool(conn, "g", "s", _context("s", source_hash="new-hash"))
        self.assertEqual(same[0]["secret_goal"], "old")
        self.assertFalse(stale_same)
        self.assertEqual(changed, [])
        self.assertTrue(stale_changed)

    def test_scenario_use_restores_saved_card_after_newgame(self):
        async def reply(message):
            replies.append(message)
        state = GroupState("g", kp_assistant_user_id="kp")
        group_state.save_state(state)
        with db.transaction() as conn:
            manual_pregens.store_upload(conn, "g", "s", _card(), "role_lin.md")
        group_state.save_state(GroupState("g"), reason="newgame")
        fresh = group_state.load_state("g")
        fresh.kp_assistant_user_id = "kp"
        group_state.save_state(fresh)
        replies = []
        with patch.object(system.scenario_library, "load_context", return_value=_context("s")), \
                patch.object(system.scenario_library, "copy_context_images"), \
                patch.object(system, "clear_page_images"), \
                patch.object(system.scenario_rag, "schedule_index_prewarm"):
            asyncio.run(system.handle_system_command(
                "g", "kp", reply, None, None, None, ["/coc", "scenario", "use", "s"]
            ))
        self.assertEqual(group_state.load_state("g").pregens[0]["str_"], 65)
        self.assertTrue(replies)

    def test_upload_replaces_asset_and_management_deletes_only_this_group(self):
        state = GroupState("g", kp_assistant_user_id="kp", scenario_library_id="s")
        group_state.save_state(state)
        async def reply(message):
            replies.append(message)
        replies = []
        with patch.object(legacy_commands.pregen_extractor, "parse_role_sheet_text", side_effect=[_card(), _card(strength=80)]), \
                patch.object(legacy_commands.scenario_library, "load_context", return_value=_context("s")):
            asyncio.run(legacy_commands.handle_role_sheet_upload("g", reply, "first", "role_lin.md"))
            asyncio.run(legacy_commands.handle_role_sheet_upload("g", reply, "updated", "role_lin.md"))
        assets = manual_pregens.list_assets("g", "s")
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["pregen"]["str_"], 80)
        self.assertIn(assets[0]["asset_id"], replies[0])
        self.assertIn(assets[0]["asset_id"], replies[1])

        with patch.object(system.scenario_library, "load_context", return_value=_context("s")):
            asyncio.run(system.handle_system_command(
                "g", "other", reply, None, None, None,
                ["/coc", "scenario", "cards", "delete", "s", assets[0]["asset_id"]]
            ))
            self.assertEqual(len(manual_pregens.list_assets("g", "s")), 1)
            asyncio.run(system.handle_system_command(
                "g", "kp", reply, None, None, None,
                ["/coc", "scenario", "cards", "delete", "s", assets[0]["asset_id"]]
            ))
        self.assertEqual(manual_pregens.list_assets("g", "s"), [])
        self.assertEqual(group_state.load_state("g").pregens, [])

    def test_claimed_card_cannot_be_deleted(self):
        state = GroupState("g", kp_assistant_user_id="kp", scenario_library_id="s")
        state.pregens = [{**_card(), "claimed_by": "player"}]
        group_state.save_state(state)
        with db.transaction() as conn:
            asset_id, _ = manual_pregens.store_upload(conn, "g", "s", _card(), "role_lin.md")
        replies = []
        async def reply(message):
            replies.append(message)
        asyncio.run(system.handle_system_command(
            "g", "kp", reply, None, None, None,
            ["/coc", "scenario", "cards", "delete", "s", asset_id]
        ))
        self.assertIn("已被認領", replies[-1])
        self.assertEqual(len(manual_pregens.list_assets("g", "s")), 1)

    def test_newgame_migrates_existing_unclaimed_manual_card(self):
        old = GroupState("g")
        old.pregens = [_card()]
        group_state.save_state(old)
        replies = []
        async def reply(message):
            replies.append(message)
        asyncio.run(system.handle_system_command(
            "g", "anyone", reply, None, None, None, ["/coc", "newgame"]
        ))
        self.assertEqual(group_state.load_state("g").pregens, [])
        self.assertEqual(len(manual_pregens.list_assets("g", "unused")), 0)
        with db.transaction() as conn:
            manual_pregens.bind_pending(conn, "g", "s")
            pool, _ = manual_pregens.install_pool(conn, "g", "s", _context("s"))
        self.assertEqual(pool[0]["str_"], 65)

    def test_reparsed_scenario_rebuilds_from_raw_manual_fields(self):
        with db.transaction() as conn:
            manual_pregens.store_upload(conn, "g", "s", _card(), "role_lin.md")
            old, _ = manual_pregens.install_pool(conn, "g", "s", _context(
                "s", [{"name": "林文", "occupation": "記者", "secret_goal": "舊目標",
                       "source": "llm_extracted"}], "old",
            ))
            current, _ = manual_pregens.install_pool(conn, "g", "s", _context(
                "s", [{"name": "林文", "occupation": "記者", "secret_goal": "新目標",
                       "source": "llm_extracted"}], "new",
            ))
        self.assertEqual(old[0]["secret_goal"], "舊目標")
        self.assertEqual(current[0]["secret_goal"], "新目標")
        self.assertEqual(current[0]["str_"], 65)
