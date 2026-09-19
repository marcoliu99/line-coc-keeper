import logging
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app import checkpoints
from app import keeper, scene_digest
from app.commands.handlers.system import _replace_scene_maps_preserving_locations
from app.models import Character, GroupState
from app.repositories import group_state


class StatePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_path = root / "state.db"
        self.backup_dir = root / "backups"
        with patch.object(db, "DB_PATH", self.db_path):
            db._ensure_tables()
        self.patches = [
            patch.object(db, "DB_PATH", self.db_path),
            patch.object(db, "BACKUP_DIR", self.backup_dir),
            patch.object(group_state.db, "DB_PATH", self.db_path),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_save_increments_revision_and_emits_log(self):
        state = GroupState("discord-group-1")
        with self.assertLogs("app.repositories.group_state", level=logging.INFO) as captured:
            group_state.save_state(state)
        self.assertEqual(state.state_revision, 1)
        loaded = group_state.load_state(state.group_id)
        self.assertEqual(loaded.state_revision, 1)
        self.assertIn("state_save_success", "\n".join(captured.output))

    def test_stale_state_save_is_rejected_instead_of_overwriting_newer_state(self):
        state = GroupState("discord-group-conflict")
        group_state.save_state(state)
        stale = group_state.load_state(state.group_id)
        latest = group_state.load_state(state.group_id)
        latest.scenario_title = "newer"
        group_state.save_state(latest)
        stale.scenario_title = "stale"

        with self.assertRaisesRegex(RuntimeError, "state revision conflict"):
            group_state.save_state(stale)
        self.assertEqual(group_state.load_state(state.group_id).scenario_title, "newer")

    def test_maintenance_guard_runs_before_scene_digest(self):
        group_id = "discord-group-maintenance-guard"
        with patch.object(keeper, "_maintenance_in_flight", {group_id}), patch.object(
            keeper, "run_scene_digest_maintenance"
        ) as digest:
            keeper.run_post_turn_maintenance(group_id)
        digest.assert_not_called()

    def test_character_mirrors_are_scoped_by_group(self):
        first = GroupState("group-a")
        first.characters["same-user"] = Character("Ada A", "same-user")
        second = GroupState("group-b")
        second.characters["same-user"] = Character("Ada B", "same-user")

        group_state.save_state(first)
        group_state.save_state(second)

        self.assertEqual(db.get_json("characters", "group-a:same-user")["name"], "Ada A")
        self.assertEqual(db.get_json("characters", "group-b:same-user")["name"], "Ada B")

    def test_scene_digest_keeps_same_named_active_characters_separate(self):
        state = GroupState("group-same-name")
        first = Character("Alex", "u1", character_id="char-1")
        second = Character("Alex", "u2", character_id="char-2")
        state.characters = {"u1": first, "u2": second}
        state.characters_by_id = {first.character_id: first, second.character_id: second}
        state.active_character_id_by_user = {"u1": first.character_id, "u2": second.character_id}

        digest = scene_digest.create_digest(state)

        self.assertEqual(set(digest["public"]["characters"]), {"char-1", "char-2"})

    def test_scenario_map_switch_replaces_maps_and_keeps_only_valid_locations(self):
        state = GroupState("group-map")
        state.scene_maps = {"old": {"rooms": [{"id": "room-a"}]}}
        state.current_map_page = {"u1": "old", "u2": "old"}
        state.current_room_id = {"u1": "room-a", "u2": "missing"}
        state.party_facing = {"u1": "E", "u2": "W"}

        _replace_scene_maps_preserving_locations(
            state,
            {"old": {"rooms": [{"id": "room-a"}]}, "new": {"rooms": [{"id": "room-b"}]}},
        )

        self.assertEqual(set(state.scene_maps), {"old", "new"})
        self.assertEqual(state.current_room_id, {"u1": "room-a"})
        self.assertEqual(state.current_map_page, {"u1": "old"})
        self.assertNotIn("u2", state.party_facing)

    def test_checkpoint_rollback_is_full_snapshot_and_new_timeline(self):
        state = GroupState("discord-group-2", active=False)
        state.characters["u1"] = Character("Ada", "u1", hp=10)
        group_state.save_state(state)
        checkpoint = checkpoints.create_checkpoint(state, label="before fight", event_id="combat-1")
        state.characters["u2"] = Character("Bob", "u2", hp=8)
        state.active = True
        state.characters["u1"].hp = 2
        group_state.save_state(state)

        restored, selected, pre = checkpoints.rollback(state.group_id, checkpoint["checkpoint_id"], actor_id="kp")

        self.assertFalse(restored.active)
        self.assertEqual(restored.characters["u1"].hp, 10)
        self.assertNotEqual(restored.timeline_id, checkpoint["timeline_id"])
        self.assertEqual(pre["reason"], "pre_rollback")
        self.assertEqual(len(checkpoints.list_checkpoints(state.group_id)), 2)
        self.assertIsNone(db.get_json("characters", "u2"))

    def test_auto_checkpoint_event_is_idempotent(self):
        state = GroupState("discord-group-3")
        first = checkpoints.create_checkpoint(state, reason="auto_combat_start", event_id="combat-1")
        second = checkpoints.create_checkpoint(state, reason="auto_combat_start", event_id="combat-1")
        self.assertEqual(first["checkpoint_id"], second["checkpoint_id"])
        self.assertEqual(len(checkpoints.list_checkpoints(state.group_id)), 1)

    def test_checkpoint_clean_accepts_unique_label(self):
        state = GroupState("discord-group-clean-label")
        entry = checkpoints.create_checkpoint(state, label="before fight")
        checkpoints.clean_checkpoint(state.group_id, "before fight")
        self.assertEqual(checkpoints.list_checkpoints(state.group_id), [])
        with self.assertRaises(KeyError):
            checkpoints.clean_checkpoint(state.group_id, entry["checkpoint_id"])

    def test_backup_is_readable_and_uses_final_name(self):
        state = GroupState("discord-group-4")
        group_state.save_state(state)
        backup = db.backup_now("manual")
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        self.assertEqual(list(self.backup_dir.glob("*.tmp")), [])
        with sqlite3.connect(backup) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(result, "ok")

    def test_stale_backup_lock_is_reclaimed(self):
        state = GroupState("discord-group-stale-lock")
        group_state.save_state(state)
        self.backup_dir.mkdir()
        lock = self.backup_dir / "backup.lock"
        lock.write_text("pid=99999999\n", encoding="ascii")
        old = time.time() - 3600
        os.utime(lock, (old, old))
        self.assertIsNotNone(db.backup_now("manual"))

    def test_keeper_combat_tool_creates_auto_checkpoint(self):
        state = GroupState("discord-group-combat-tool")
        group_state.save_state(state)
        result = keeper._execute_tool(state, "start_combat", {}, [], [])
        self.assertTrue(result["ok"])
        entries = checkpoints.list_checkpoints(state.group_id)
        self.assertEqual([entry["reason"] for entry in entries], ["auto_combat_start"])

    def test_fact_metadata_and_successful_item_removal_are_persisted(self):
        state = GroupState("discord-group-5")
        state.characters["u1"] = Character("Ada", "u1", carried_items=["鑰匙"])
        group_state.save_state(state)
        self.assertTrue(keeper._execute_tool(state, "record_clue", {"clue": "門鎖曾被撬過", "visibility": "public"}, [], [] )["ok"])
        self.assertTrue(keeper._execute_tool(state, "remove_carried_item", {"investigator": "Ada", "item": "鑰匙"}, [], [] )["ok"])
        loaded = group_state.load_state(state.group_id)
        self.assertEqual(loaded.known_clues[0]["visibility"], "public")
        self.assertEqual(loaded.consumed_or_removed_items[0]["character_id"], "u1")
        self.assertEqual(loaded.characters["u1"].carried_items, [])

    def test_latest_digest_filters_timeline(self):
        state = GroupState("discord-group-6")
        state.characters["u1"] = Character("Ada", "u1")
        group_state.save_state(state)
        first = scene_digest.create_digest(state, scene_label="old")
        state.current_map_page["u1"] = "12"
        state.current_room_id["u1"] = "library"
        digest = scene_digest.create_digest(state, scene_label="with-location")
        self.assertEqual(digest["public"]["locations"]["u1"]["room_id"], "library")
        self.assertIn("recent_checkpoints", digest)
        state.timeline_id = "timeline-new"
        state.state_revision += 1
        second = scene_digest.create_digest(state, scene_label="new")
        self.assertEqual(scene_digest.latest_digest(state.group_id, "timeline-new")["digest_id"], second["digest_id"])
        self.assertEqual(scene_digest.get_digest(state.group_id, first["digest_id"])["scene_label"], "old")

    def test_digest_resets_watermark_after_log_trim(self):
        state = GroupState("discord-group-digest-trim")
        state.log = [{"role": "user", "content": str(i)} for i in range(20)]
        group_state.save_state(state)
        first = scene_digest.create_digest(state, scene_label="same")

        state.log = state.log[-3:]
        group_state.save_state(state)
        keeper.run_scene_digest_maintenance(state.group_id)

        entries = scene_digest.list_digests(state.group_id)
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry["log_length"] for entry in entries}, {20, 3})

    def test_rollback_rebuilds_library_page_images(self):
        state = GroupState("discord-group-image-rollback")
        state.scenario_library_id = "old-scenario"
        state.active_chapter_id = "chapter-1"
        group_state.save_state(state)
        checkpoint = checkpoints.create_checkpoint(state, label="old scenario")

        with patch.object(group_state, "DATA_DIR", Path(self.temp.name) / "images"):
            group_state.save_page_image(state.group_id, 9, b"new")

            def copy_images(_scenario_id, _pages, save_image):
                save_image(7, b"old")

            with patch("app.scenario_library.load_context", return_value={"page_numbers": [7]}), \
                    patch("app.scenario_library.copy_context_images", side_effect=copy_images):
                checkpoints.rollback(state.group_id, checkpoint["checkpoint_id"], actor_id="kp")

            self.assertEqual(group_state.load_page_image(state.group_id, 7), b"old")
            self.assertIsNone(group_state.load_page_image(state.group_id, 9))


if __name__ == "__main__":
    unittest.main()
