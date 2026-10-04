import asyncio
import logging
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import checkpoints, db, keeper, scene_digest
from app.commands.handlers import combat as combat_handler
from app.commands.handlers import system as system_handler
from app.models import Character, Combatant, EnemyCombatCard, GroupState, SpecialAbility
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

    def test_state_save_logs_hash_instead_of_raw_group_id(self):
        group_id = "discord-sensitive-group-id"
        with self.assertLogs("app.repositories.group_state", level=logging.INFO) as captured:
            group_state.save_state(GroupState(group_id))
        output = "\n".join(captured.output)
        self.assertIn("state_save_success", output)
        self.assertNotIn(group_id, output)

    def test_legacy_schema_data_uses_explicit_v0_migration(self):
        state = GroupState.from_dict({"group_id": "legacy", "schema_version": 0})
        self.assertEqual(state.schema_version, GroupState.CURRENT_SCHEMA_VERSION)

    def test_tool_recovery_markers_round_trip_and_old_snapshots_default_empty(self):
        legacy = GroupState.from_dict({"group_id": "legacy"})
        self.assertEqual(legacy.tool_recovery_markers, [])
        self.assertFalse(legacy.autoroll_checks)
        state = GroupState("recovery")
        state.tool_recovery_markers.append({"tool_name": "apply_combat_damage", "status": "recovery_required"})
        restored = GroupState.from_dict(state.to_dict())
        self.assertEqual(restored.tool_recovery_markers, state.tool_recovery_markers)

    def test_cancelled_mutation_recovery_marker_is_durable(self):
        state = GroupState("recovery-persist")
        group_state.save_state(state)
        asyncio.run(keeper.record_tool_recovery_marker(
            state, "apply_combat_damage", {"investigator": "Ada", "damage": 4}
        ))

        loaded = group_state.load_state(state.group_id)
        self.assertEqual(len(loaded.tool_recovery_markers), 1)
        marker = loaded.tool_recovery_markers[0]
        self.assertEqual(marker["tool_name"], "apply_combat_damage")
        self.assertEqual(marker["status"], "recovery_required")
        self.assertEqual(len(marker["input_digest"]), 16)

    def test_future_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported GroupState schema_version=999"):
            GroupState.from_dict({"group_id": "future", "schema_version": 999})

    def test_database_table_names_are_runtime_validated(self):
        with self.assertRaisesRegex(ValueError, "unknown table"):
            db.get_json("group_states; DROP TABLE characters", "x")

    def test_checkpoint_success_logs_started_and_success(self):
        state = GroupState("discord-group-checkpoint-started")
        with self.assertLogs("app.checkpoints", level=logging.INFO) as captured:
            checkpoints.create_checkpoint(state)
        output = "\n".join(captured.output)
        self.assertIn("checkpoint_started", output)
        self.assertIn("checkpoint_success", output)

    def test_checkpoint_commands_require_clean_identifier(self):
        async def run_command(parts):
            replies = []

            async def reply(text):
                replies.append(text)

            await system_handler.handle_system_command(
                "discord-group-command-validation", "kp", reply, None, None, None, parts
            )
            return replies

        state = GroupState("discord-group-command-validation", kp_assistant_user_id="kp")
        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler.checkpoints, "create_checkpoint") as create:
            checkpoint_replies = asyncio.run(
                run_command(["/coc", "checkpoint", "clean"])
            )
        self.assertEqual(checkpoint_replies, ["用法：/coc checkpoint clean <ID 或唯一名稱>"])
        create.assert_not_called()

        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler.scene_digest, "latest_digest") as latest:
            digest_replies = asyncio.run(
                run_command(["/coc", "digest", "clean"])
            )
        self.assertEqual(digest_replies, ["用法：/coc digest clean <ID>"])
        latest.assert_not_called()

    def test_scenario_switch_rejects_pending_pregen_luck(self):
        state = GroupState("discord-group-pending-pregen", kp_assistant_user_id="kp")
        state.pending_pregen_luck = {"player": "character-1"}
        replies = []

        async def reply(text):
            replies.append(text)

        group_state.save_state(state)
        with patch.object(
            system_handler.scenario_library, "load_context"
        ) as load_context:
            asyncio.run(system_handler.handle_system_command(
                state.group_id,
                "kp",
                reply,
                None,
                None,
                None,
                ["/coc", "scenario", "use", "new-scenario"],
            ))

        self.assertIn("/coc luck roll", replies[0])
        load_context.assert_not_called()

    def test_scenario_switch_rejects_pending_pdf_flow(self):
        state = GroupState("discord-group-pending-pdf", kp_assistant_user_id="kp")
        state.pending_pdf_upload = {"scenario_id": "old-upload"}
        replies = []

        async def reply(text):
            replies.append(text)

        group_state.save_state(state)
        with patch.object(
            system_handler.scenario_library, "load_context"
        ) as load_context:
            asyncio.run(system_handler.handle_system_command(
                state.group_id, "kp", reply, None, None, None,
                ["/coc", "scenario", "use", "new-scenario"],
            ))

        self.assertIn("待處理的劇本上傳", replies[0])
        load_context.assert_not_called()

    def test_scenario_use_clears_timeline_bound_player_decisions(self):
        state = GroupState("discord-group-scenario-reset", kp_assistant_user_id="kp", timeline_id="timeline-old")
        state.pending_checks["player"] = {"type": "skill", "timeline_id": "timeline-old"}
        state.pending_luck_decisions["player"] = {"timeline_id": "timeline-old"}
        state.deterministic_check_results["old"] = {"timeline_id": "timeline-old"}
        context = {
            "manifest": {"title": "New scenario"},
            "text": "new scenario text",
            "active_chapter_id": "chapter-1",
            "context_chapter_ids": ["chapter-1"],
            "indexes": {"npcs": [], "locations": []},
            "pregens": [],
            "scene_maps": {},
            "page_numbers": [],
        }
        replies = []

        async def reply(text):
            replies.append(text)

        from app import scenario_activation
        group_state.save_state(state)
        with (
            patch.object(system_handler.scenario_library, "load_context", return_value=context),
            patch.object(scenario_activation, "refresh_after_commit", return_value=True),
            patch.object(system_handler.scenario_rag, "schedule_index_prewarm"),
        ):
            asyncio.run(system_handler.handle_system_command(
                state.group_id,
                "kp",
                reply,
                None,
                None,
                None,
                ["/coc", "scenario", "use", "new-scenario"],
            ))

        # The fixture's library item carries no floor plans, so the selection
        # now also reports that — see tests/test_empty_location_index_notice.py.
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0].startswith("KP 已選擇《New scenario》；目前 Context：chapter-1。"))
        latest = group_state.load_state(state.group_id)
        self.assertNotEqual(latest.timeline_id, "timeline-old")
        self.assertEqual(latest.pending_checks, {})
        self.assertEqual(latest.pending_luck_decisions, {})
        self.assertEqual(latest.deterministic_check_results, {})

    def test_pdf_choice_requires_kp(self):
        state = GroupState("discord-group-pdf-auth", kp_assistant_user_id="kp")
        state.pending_pdf_upload = {"scenario_id": "upload"}
        replies = []

        async def reply(text):
            replies.append(text)

        with patch.object(system_handler, "load_state", return_value=state), patch.object(
            system_handler.permissions, "may_manage_scenario_lifecycle", return_value=False
        ), patch.object(
            system_handler, "apply_pdf_upload_choice"
        ) as resolve:
            asyncio.run(system_handler.handle_system_command(
                state.group_id, "player", reply, None, None, None,
                ["/coc", "pdf", "new"],
            ))

        self.assertIn("KP 助手", replies[0])
        resolve.assert_not_called()

    def test_scenario_lifecycle_authorization_can_be_enabled_by_config(self):
        from app.commands import permissions

        state = GroupState("discord-group-lifecycle-toggle", kp_assistant_user_id="kp")
        with patch.object(permissions.config, "SCENARIO_LIFECYCLE_KP_ONLY", True):
            self.assertFalse(permissions.may_manage_scenario_lifecycle(state, "player"))
            self.assertTrue(permissions.may_manage_scenario_lifecycle(state, "kp"))
        with patch.object(permissions.config, "SCENARIO_LIFECYCLE_KP_ONLY", False):
            self.assertTrue(permissions.may_manage_scenario_lifecycle(state, "player"))

    def test_autoroll_defaults_off_and_any_player_can_toggle(self):
        state = GroupState("autoroll-policy", kp_assistant_user_id="kp")
        replies = []

        async def reply(text):
            replies.append(text)

        async def run(parts, user_id):
            replies.clear()
            with patch.object(system_handler, "load_state", return_value=state), patch(
                "app.repositories.state_transaction.commit_snapshot"
            ) as save:
                await system_handler.handle_system_command(
                    state.group_id, user_id, reply, None, None, None, parts
                )
            return list(replies), save

        player_replies, player_save = asyncio.run(run(["/coc", "autoroll", "on"], "player"))
        self.assertIn("已開啟自動擲骰", player_replies[0])
        self.assertTrue(state.autoroll_checks)
        player_save.assert_called_once_with(state)

        kp_replies, kp_save = asyncio.run(run(["/coc", "autoroll", "on"], "kp"))
        self.assertIn("已開啟自動擲骰", kp_replies[0])
        self.assertTrue(state.autoroll_checks)
        kp_save.assert_called_once_with(state)

        keeper_replies, keeper_save = asyncio.run(run(["/coc", "autoroll", "off"], "keeper"))
        self.assertIn("已關閉自動擲骰", keeper_replies[0])
        self.assertFalse(state.autoroll_checks)
        keeper_save.assert_called_once_with(state)

    def test_stale_state_save_is_rejected_instead_of_overwriting_newer_state(self):
        state = GroupState("discord-group-conflict")
        group_state.save_state(state)
        stale = group_state.load_state(state.group_id)
        latest = group_state.load_state(state.group_id)
        latest.scenario_title = "newer"
        group_state.save_state(latest)
        stale.scenario_title = "stale"

        with (
            self.assertLogs("app.repositories.group_state", level=logging.WARNING) as captured,
            self.assertRaisesRegex(RuntimeError, "state revision conflict"),
        ):
            group_state.save_state(stale)
        self.assertIn("state_save_revision_conflict", "\n".join(captured.output))
        self.assertEqual(group_state.load_state(state.group_id).scenario_title, "newer")

    def test_checkpoint_reads_authoritative_latest_state(self):
        state = GroupState("discord-group-checkpoint-freshness")
        group_state.save_state(state)
        stale = group_state.load_state(state.group_id)
        latest = group_state.load_state(state.group_id)
        latest.scenario_title = "newer"
        group_state.save_state(latest)

        checkpoint = checkpoints.create_checkpoint(stale, label="fresh")

        self.assertEqual(checkpoint["state"]["scenario_title"], "newer")
        self.assertEqual(checkpoint["state_revision"], latest.state_revision)

    def test_checkpoint_failure_is_logged(self):
        state = GroupState("discord-group-checkpoint-failure")
        with (
            patch.object(db, "set_json_tx", side_effect=RuntimeError("write failed")),
            self.assertLogs("app.checkpoints", level=logging.ERROR) as captured,
            self.assertRaisesRegex(RuntimeError, "write failed"),
        ):
            checkpoints.create_checkpoint(state)

        self.assertIn("checkpoint_failure", "\n".join(captured.output))

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

        # Unknown historical orphans are preserved for an audited migration;
        # group-prefix similarity alone does not prove ownership.
        db.set_json("characters", "group-a:retired-owner", {"sheet": {"name": "old"}})
        db.set_json("characters", "unscoped-legacy", {"sheet": {"name": "keep"}})
        group_state.save_state(first)

        self.assertEqual(db.get_json("characters", "group-a:same-user")["name"], "Ada A")
        self.assertEqual(db.get_json("characters", "group-b:same-user")["name"], "Ada B")
        self.assertIsNotNone(db.get_json("characters", "group-a:retired-owner"))
        self.assertIsNotNone(db.get_json("characters", "unscoped-legacy"))

    def test_log_only_save_skips_mirror_writes_and_repairs_missing_alias(self):
        state = GroupState("mirror-diff")
        state.characters["u"] = Character("Ada", "u", character_id="char-a")
        group_state.save_state(state)
        with db.transaction() as conn:
            conn.execute("UPDATE characters SET updated_at = 'sentinel'")
        original = db.set_json_tx
        state.log.append({"role": "user", "content": "hello"})
        with patch.object(db, "set_json_tx", wraps=original) as write:
            group_state.save_state(state)
        self.assertEqual([call.args[1] for call in write.call_args_list], ["group_states"])
        with db.transaction() as conn:
            self.assertEqual(conn.execute("SELECT updated_at FROM characters").fetchall(),
                             [("sentinel",), ("sentinel",)])
        db.delete_json("characters", "mirror-diff:char-a")
        with patch.object(db, "set_json_tx", wraps=original) as write:
            group_state.save_state(state)
        self.assertEqual([call.args[2] for call in write.call_args_list],
                         ["mirror-diff:char-a", "mirror-diff"])

    def test_mirror_reads_are_bounded_and_newgame_deletes_exact_old_keys(self):
        state = GroupState("a")
        state.characters["u"] = Character("Ada", "u", character_id="char-a")
        group_state.save_state(state)
        for idx in range(50):
            db.set_json("characters", f"a:neighbor:{idx}", {"conversation_id": "a:neighbor"})
        queries = []
        original = db._connect

        @contextmanager
        def traced():
            with original() as conn:
                conn.set_trace_callback(queries.append)
                yield conn

        with patch.object(db, "_connect", traced):
            group_state.save_state(GroupState("a"), reason="newgame")
        selects = [q for q in queries if q.startswith("SELECT key, data FROM characters WHERE key IN")]
        self.assertEqual(len(selects), 1)
        self.assertIn("WHERE key IN", selects[0])
        self.assertIsNone(db.get_json("characters", "a:u"))
        self.assertIsNone(db.get_json("characters", "a:char-a"))
        self.assertEqual(len(db.list_keys("characters")), 50)

    def test_outer_commit_failure_does_not_publish_revision_or_timeline(self):
        state = GroupState("commit-failure")
        original_timeline = state.timeline_id
        original = db._connect

        @contextmanager
        def failing_commit():
            with original() as conn:
                yield conn
                raise sqlite3.OperationalError("commit failed")

        with patch.object(db, "_connect", failing_commit), self.assertRaisesRegex(sqlite3.OperationalError, "commit failed"):
            group_state.save_state(state)
        self.assertEqual(state.state_revision, 0)
        self.assertEqual(state.timeline_id, original_timeline)
        self.assertIsNone(db.get_json("group_states", state.group_id))

    def test_all_save_failures_logged_without_structured_logging_and_rolled_back(self):
        for failure in ("mirror", "companion", "commit"):
            with self.subTest(failure=failure):
                state = GroupState("private-save-" + failure)
                state.characters["u"] = Character("Ada", "u", character_id="card")
                group_state.save_state(state)
                before = db.get_json("group_states", state.group_id)
                mirrors = db.get_json("characters", state.group_id + ":u")
                state.characters["u"].hp = 1
                error = sqlite3.OperationalError("injected " + failure)
                original_write, original_connect = db.set_json_tx, db._connect

                def write(conn, table, key, value, failure=failure, error=error, original_write=original_write):
                    if failure == "mirror" and table == "characters":
                        raise error
                    return original_write(conn, table, key, value)

                def companion(conn, failure=failure, error=error, original_write=original_write):
                    original_write(conn, "memory_chunks", "companion", {"new": True})
                    if failure == "companion":
                        raise error

                @contextmanager
                def connect(failure=failure, error=error, original_connect=original_connect):
                    with original_connect() as conn:
                        yield conn
                        if failure == "commit":
                            raise error

                with patch.object(group_state.config, "LOG_ENABLED", False), \
                     patch.object(db, "set_json_tx", side_effect=write), \
                     patch.object(db, "_connect", connect), \
                     self.assertLogs("app.repositories.group_state", level=logging.ERROR) as logs, \
                     self.assertRaises(sqlite3.OperationalError) as raised:
                    group_state.save_state(state, reason="review-test", mutate_tx=companion)
                self.assertIs(raised.exception, error)
                record = logs.records[0]
                self.assertIn("state_save_failure", record.getMessage())
                self.assertIn("reason=review-test", record.getMessage())
                self.assertIn("duration_ms=", record.getMessage())
                self.assertNotIn(state.group_id, record.getMessage())
                self.assertIsNotNone(record.exc_info)
                self.assertEqual(state.state_revision, before["state_revision"])
                self.assertEqual(db.get_json("group_states", state.group_id), before)
                self.assertEqual(db.get_json("characters", state.group_id + ":u"), mirrors)
                self.assertIsNone(db.get_json("memory_chunks", "companion"))

    def test_maintenance_commit_failure_never_publishes_receipt(self):
        state = GroupState("maintenance-commit-failure", timeline_id="original")
        state.log = [{"role": "user", "content": "old"}]
        group_state.save_state(state)
        original = db._connect

        @contextmanager
        def failing_commit():
            with original() as conn:
                yield conn
                raise sqlite3.OperationalError("outer commit failed")

        with patch.object(db, "_connect", failing_commit), patch.object(group_state.StateCommit, "apply") as publish, self.assertRaises(sqlite3.OperationalError):
            keeper._persist_memory_maintenance_state(
                state.group_id, "new summary", state.log, timeline_id=state.timeline_id,
                base_summary="", source_revision=state.state_revision,
                idempotency_key="failed-maintenance", embedding=[],
            )
        publish.assert_not_called()
        stored = group_state.load_state(state.group_id)
        self.assertEqual(stored.state_revision, 1)
        self.assertEqual(stored.log, state.log)
        self.assertIsNone(db.get_json("memory_chunks", state.group_id))

    def test_mirror_failure_rolls_back_group_and_all_aliases(self):
        state = GroupState("atomic-mirror")
        state.characters["u"] = Character("Ada", "u", character_id="char-a")
        group_state.save_state(state)
        state.characters["u"].occupation = "new occupation"
        original = db.set_json_tx

        def fail(conn, table, key, value):
            if key == "atomic-mirror:u":
                raise sqlite3.OperationalError("mirror failed")
            return original(conn, table, key, value)

        with patch.object(db, "set_json_tx", side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            group_state.save_state(state)
        self.assertEqual(state.state_revision, 1)
        self.assertEqual(group_state.load_state(state.group_id).state_revision, 1)
        self.assertNotEqual(db.get_json("characters", "atomic-mirror:char-a")["occupation"], "new occupation")

    def test_mirror_projection_preserves_retired_cards_and_owner_alias(self):
        state = GroupState("history")
        active = Character("Active", "u", character_id="active")
        retired = Character("Retired", "u", character_id="retired", active=False)
        state.characters = {"u": active}
        state.characters_by_id = {"active": active, "retired": retired}
        before = state.to_dict()
        projection = group_state.character_mirror_projection(before)
        self.assertEqual(set(projection), {"history:u", "history:active", "history:retired"})
        self.assertEqual(before, state.to_dict())
        group_state.save_state(state)
        self.assertFalse(db.get_json("characters", "history:retired")["sheet"]["active"])

    def test_mirror_key_collision_does_not_overwrite_other_group(self):
        db.set_json("characters", "a:b:c", {"conversation_id": "a:b", "name": "keep"})
        state = GroupState("a")
        state.characters["b:c"] = Character("Ada", "b:c")
        with self.assertRaisesRegex(ValueError, "owned by another"):
            group_state.save_state(state)
        self.assertEqual(db.get_json("characters", "a:b:c")["name"], "keep")
        self.assertIsNone(db.get_json("group_states", "a"))

    def test_scene_digest_keeps_same_named_active_characters_separate(self):
        state = GroupState("group-same-name")
        first = Character("Alex", "u1", character_id="char-1")
        second = Character("Alex", "u2", character_id="char-2")
        state.characters = {"u1": first, "u2": second}
        state.characters_by_id = {first.character_id: first, second.character_id: second}
        state.active_character_id_by_user = {"u1": first.character_id, "u2": second.character_id}

        digest = scene_digest.create_digest(state)

        self.assertEqual(set(digest["public"]["characters"]), {"char-1", "char-2"})

    def test_scene_digest_keeps_same_named_enemy_cards_separate(self):
        state = GroupState("group-same-enemy-name")
        state.combat.active = True
        state.combat.order = [
            Combatant(
                name="Cultist", display_name="Cultist", dex=40, hp=5, hp_max=5,
                combatant_id="enemy:one", enemy_card_id="card-one"
            ),
            Combatant(
                name="Cultist", display_name="Cultist", dex=40, hp=5, hp_max=5,
                combatant_id="enemy:two", enemy_card_id="card-two"
            ),
        ]
        state.combat.enemy_cards = {
            "card-one": EnemyCombatCard("card-one", "Cultist", abilities=[SpecialAbility("one", "First")]),
            "card-two": EnemyCombatCard("card-two", "Cultist", abilities=[SpecialAbility("two", "Second")]),
        }

        digest = scene_digest.create_digest(state)

        self.assertEqual(set(digest["private"]["npc_abilities"]), {"enemy:one", "enemy:two"})


    def test_checkpoint_rollback_is_full_snapshot_and_new_timeline(self):
        state = GroupState("discord-group-2", active=False)
        state.characters["u1"] = Character("Ada", "u1", hp=10)
        group_state.save_state(state)
        checkpoint = checkpoints.create_checkpoint(state, label="before fight", event_id="combat-1")
        state.characters["u2"] = Character("Bob", "u2", hp=8)
        state.active = True
        state.characters["u1"].hp = 2
        group_state.save_state(state)

        restored, _selected, pre = checkpoints.rollback(state.group_id, checkpoint["checkpoint_id"], actor_id="kp")

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
        with self.assertLogs("app.db", level=logging.INFO) as captured:
            backup = db.backup_now("manual")
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        self.assertEqual(list(self.backup_dir.glob("*.tmp")), [])
        output = "\n".join(captured.output)
        self.assertIn("backup_started", output)
        self.assertIn("backup_success", output)
        conn = sqlite3.connect(backup)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(result, "ok")

    def test_backups_created_in_same_second_do_not_overwrite(self):
        state = GroupState("discord-group-backup-unique")
        group_state.save_state(state)

        first = db.backup_now("manual")
        second = db.backup_now("manual")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.assertEqual(len(list(self.backup_dir.glob("*.db"))), 2)

    def test_backup_lock_initialization_failure_is_logged(self):
        with (
            patch.object(db, "_backup_lock", side_effect=OSError("lock directory unavailable")),
            self.assertLogs("app.db", level=logging.ERROR) as captured,
            self.assertRaisesRegex(OSError, "lock directory unavailable"),
        ):
            db.backup_now("manual")

        self.assertIn("backup_failure", "\n".join(captured.output))

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

    def test_add_npc_to_combat_rejects_duplicate_of_a_live_enemy(self):
        # Diagnosed from a live log: the Keeper re-searched a scenario NPC
        # ("柯比特"/Corbitt) mid-turn and called add_npc_to_combat for it
        # twice, producing two independent HP pools for one monster. This
        # tool call must recognize the name is already an active enemy and
        # refuse to create a second one.
        state = GroupState("discord-group-dup-npc")
        group_state.save_state(state)
        first = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [],
        )
        self.assertTrue(first["ok"])
        self.assertNotIn("note", first)

        second = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [],
        )

        self.assertTrue(second["ok"])
        self.assertIn("柯比特", second.get("note", ""))
        enemy_count = sum(1 for c in state.combat.order if c.side == "enemy")
        self.assertEqual(enemy_count, 1)

    def test_add_npc_to_combat_allows_a_second_defeated_monster_of_same_name(self):
        state = GroupState("discord-group-revived-npc")
        group_state.save_state(state)
        keeper._execute_tool(state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [])
        enemy = next(c for c in state.combat.order if c.side == "enemy")
        state.combat.enemy_cards[enemy.enemy_card_id].hp = 0
        enemy.defeated = True
        # _mutate_and_save_state reloads from the DB rather than trusting
        # this in-memory `state` object, so the defeat above must be
        # persisted before the next tool call will see it.
        group_state.save_state(state)

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [],
        )

        self.assertTrue(result["ok"])
        # Still added, but the Keeper is asked whether it is really a new one
        # (docs/specs/bug/defeated_enemy_readd_design_spec.md).
        self.assertIn("先前已在這場戰鬥中被打倒", result["note"])
        self.assertEqual(
            [c.display_name for c in state.combat.order if c.side == "enemy"], ["柯比特", "柯比特 2"],
        )

    def test_add_npc_to_combat_allows_two_different_named_enemies(self):
        state = GroupState("discord-group-two-enemies")
        group_state.save_state(state)
        keeper._execute_tool(state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [])

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "老鼠群", "dex": 60, "hp": 5}, [], [],
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("note", result)
        enemy_count = sum(1 for c in state.combat.order if c.side == "enemy")
        self.assertEqual(enemy_count, 2)

    def test_add_npc_to_combat_rejects_duplicate_under_a_different_scenario_index_alias(self):
        # PR #55 review finding: the original duplicate guard only compared
        # raw combatant name/display_name, so the same indexed NPC added
        # under two non-overlapping aliases (e.g. "柯比特" then "Walter
        # Corbitt") still created a second, independent HP pool - the exact
        # corruption the guard exists to prevent. Fixed by resolving the
        # scenario-index entry (which already tracks aliases) and checking
        # every known alias, not just the exact string passed this call.
        state = GroupState("discord-group-alias-dup")
        state.scenario_npc_index = [
            {"name": "Walter Corbitt", "aliases": ["柯比特"], "hp": 20},
        ]
        group_state.save_state(state)
        first = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [],
        )
        self.assertTrue(first["ok"])
        self.assertNotIn("note", first)

        second = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "Walter Corbitt", "dex": 50, "hp": 20}, [], [],
        )

        self.assertTrue(second["ok"])
        self.assertIn("note", second)
        enemy_count = sum(1 for c in state.combat.order if c.side == "enemy")
        self.assertEqual(enemy_count, 1)

    def test_add_npc_to_combat_does_not_treat_substring_overlapping_names_as_duplicates(self):
        # PR #55 review finding: the original guard's substring matching
        # (inherited from _find_combatant) treated "Cultist" and "Cultist
        # Leader" as the same entity, silently blocking the second, distinct
        # enemy from ever entering combat. find_live_enemy now does exact
        # matching only, so two enemies with overlapping names must both be
        # allowed in.
        state = GroupState("discord-group-substring-overlap")
        group_state.save_state(state)
        keeper._execute_tool(state, "add_npc_to_combat", {"name": "Cultist", "dex": 50, "hp": 10}, [], [])

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "Cultist Leader", "dex": 60, "hp": 20}, [], [],
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("note", result)
        enemy_names = sorted(c.name for c in state.combat.order if c.side == "enemy")
        self.assertEqual(enemy_names, ["Cultist", "Cultist Leader"])

    def test_add_npc_to_combat_alias_resolution_does_not_use_fuzzy_matching(self):
        # Second-round review finding: the duplicate guard's alias expansion
        # originally reused _find_npc_index_entry, which has a difflib fuzzy
        # fallback (ratio >= 0.6) meant for the HP-consistency check, where a
        # wrong guess only mis-prices one number. Reused for duplicate
        # detection, a wrong fuzzy match would pull in an unrelated entry's
        # aliases and use them to wrongly block a genuinely different enemy.
        # "深潛者頭目" is deliberately missing the "（成年頭目）" suffix so it
        # doesn't exactly match either index entry — under the old fuzzy
        # behavior this could resolve to the wrong entry's alias set.
        state = GroupState("discord-group-fuzzy-alias")
        state.scenario_npc_index = [
            {"name": "深潛者（成年頭目）", "aliases": [], "hp": 30},
            {"name": "深潛者（幼體）", "aliases": [], "hp": 8},
        ]
        group_state.save_state(state)
        keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "深潛者（成年頭目）", "dex": 50, "hp": 30}, [], [],
        )

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "深潛者頭目", "dex": 50, "hp": 30}, [], [],
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("note", result)
        enemy_count = sum(1 for c in state.combat.order if c.side == "enemy")
        self.assertEqual(enemy_count, 2)

    def test_add_npc_to_combat_alias_resolution_is_case_and_whitespace_insensitive(self):
        # Third-round review finding: combat.find_npc_index_entry_exact did a
        # literal string match against index names/aliases, unlike
        # combat.find_live_enemy's normalized (lowercased, whitespace-
        # collapsed) comparison. A case/whitespace variant of a registered
        # alias used to fail to resolve the index entry at all, silently
        # skipping alias expansion and letting a duplicate slip through.
        state = GroupState("discord-group-alias-case-insensitive")
        state.scenario_npc_index = [
            {"name": "Walter Corbitt", "aliases": ["柯比特"], "hp": 20},
        ]
        group_state.save_state(state)
        keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [],
        )

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "  walter   corbitt ", "dex": 50, "hp": 20}, [], [],
        )

        self.assertTrue(result["ok"])
        self.assertIn("note", result)
        enemy_count = sum(1 for c in state.combat.order if c.side == "enemy")
        self.assertEqual(enemy_count, 1)

    def test_add_npc_to_combat_tolerates_a_non_string_alias_in_the_scenario_index(self):
        # Fourth-round review finding: scenario_npc_index is populated from
        # an LLM's structured tool-call output with no runtime enforcement
        # that "aliases" items are actually strings. Before this fix, a
        # non-string alias item would raise TypeError from set.update
        # inside combat.find_live_enemy_by_any_alias, failing the whole
        # add_npc_to_combat call instead of just being ignored.
        state = GroupState("discord-group-malformed-alias")
        state.scenario_npc_index = [
            {"name": "柯比特", "aliases": ["Walter Corbitt", {"unexpected": "object"}], "hp": 20},
        ]
        group_state.save_state(state)

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [],
        )

        self.assertTrue(result["ok"])
        enemy_count = sum(1 for c in state.combat.order if c.side == "enemy")
        self.assertEqual(enemy_count, 1)

    def test_add_npc_to_combat_duplicate_rejection_does_not_write_a_no_op_save(self):
        # Second-round review finding: the duplicate-rejection early return
        # didn't use the _StateMutation(value, should_save=False) pattern
        # already established for other genuine no-op tool calls (see
        # add_carried_item/remove_carried_item/add_status_tag/
        # remove_status_tag), so every rejected duplicate call persisted a
        # pointless extra write.
        state = GroupState("discord-group-dup-no-save")
        group_state.save_state(state)
        keeper._execute_tool(state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [])
        revision_before = group_state.load_state(state.group_id).state_revision

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "柯比特", "dex": 50, "hp": 20}, [], [],
        )

        self.assertTrue(result["ok"])
        self.assertIn("note", result)
        revision_after = group_state.load_state(state.group_id).state_revision
        self.assertEqual(revision_after, revision_before)

    def test_combat_addnpc_slash_command_rejects_duplicate_live_enemy(self):
        # Second-round review finding: the duplicate-enemy guard only lived
        # in the Keeper LLM tool handler, not in combat.add_npc itself, so
        # a human operator running /coc combat addnpc twice for the same
        # live enemy bypassed it entirely and reproduced the original bug.
        group_id = "discord-group-slash-dup"
        state = GroupState(group_id)
        group_state.save_state(state)
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        asyncio.run(combat_handler.handle_combat_command(
            group_id, reply, ["/coc", "combat", "addnpc", "柯比特", "50", "20"],
        ))
        asyncio.run(combat_handler.handle_combat_command(
            group_id, reply, ["/coc", "combat", "addnpc", "柯比特", "50", "20"],
        ))

        reloaded = group_state.load_state(group_id)
        enemy_count = sum(1 for c in reloaded.combat.order if c.side == "enemy")
        self.assertEqual(enemy_count, 1)
        self.assertIn("已經在戰鬥中", replies[-1])

    def test_add_npc_to_combat_allows_the_same_species_under_distinct_display_names(self):
        # User-raised scenario: two Deep Ones (魚人) attack simultaneously
        # from different directions - same species/stats, but two distinct
        # individuals, not a duplicate call for the same one. The duplicate
        # guard is exact-name-match, so as long as the Keeper follows the
        # naming instruction added to _build_static_prompt (give each
        # same-species instance in one fight a distinct display name), both
        # must be allowed into combat as separate combatants.
        state = GroupState("discord-group-two-deep-ones")
        group_state.save_state(state)
        keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "魚人（左）", "dex": 40, "hp": 15}, [], [],
        )

        result = keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "魚人（右）", "dex": 40, "hp": 15}, [], [],
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("note", result)
        enemy_names = {c.name for c in state.combat.order if c.side == "enemy"}
        self.assertEqual(enemy_names, {"魚人（左）", "魚人（右）"})

    def test_static_prompt_instructs_distinct_names_for_same_species_multiples(self):
        state = GroupState("discord-group-prompt-check")
        prompt = keeper._build_static_prompt(state)
        self.assertIn("Each simultaneously active instance of one enemy type needs a distinct display name", prompt)

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
        group_state.save_state(state)
        digest = scene_digest.create_digest(state, scene_label="with-location")
        self.assertEqual(digest["public"]["locations"]["u1"]["room_id"], "library")
        self.assertIn("recent_checkpoints", digest)
        state.timeline_id = "timeline-new"
        # Publish the new source before deriving its digest; do not simulate
        # persistence by editing the caller revision alone.
        group_state.save_state(state)
        second = scene_digest.create_digest(state, scene_label="new")
        self.assertEqual(scene_digest.latest_digest(state.group_id, "timeline-new")["digest_id"], second["digest_id"])
        self.assertEqual(scene_digest.get_digest(state.group_id, first["digest_id"])["scene_label"], "old")

    def test_digest_resets_watermark_after_log_trim(self):
        state = GroupState("discord-group-digest-trim")
        state.log = [{"role": "user", "content": str(i)} for i in range(20)]
        group_state.save_state(state)
        scene_digest.create_digest(state, scene_label="same")

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

    def test_rollback_keeps_committed_state_when_image_restore_fails(self):
        state = GroupState("discord-group-image-restore-failure")
        state.scenario_library_id = "scenario"
        group_state.save_state(state)
        checkpoint = checkpoints.create_checkpoint(state, label="before")
        state.scenario_title = "newer"
        group_state.save_state(state)

        with (
            patch("app.scenario_library.load_context", side_effect=OSError("image cache unavailable")),
            self.assertLogs("app.checkpoints", level=logging.ERROR) as captured,
        ):
            restored, _, _ = checkpoints.rollback(
                state.group_id, checkpoint["checkpoint_id"], actor_id="kp"
            )

        self.assertEqual(restored.scenario_title, "")
        self.assertEqual(group_state.load_state(state.group_id).scenario_title, "")
        self.assertIn("rollback_image_restore_failure", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
