"""Deterministic Phase 2A stale-work races against persisted GroupState."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import config, db, locks, scenario_intro, turn_commit
from app.commands import router
from app.models import BASE_SKILLS, Character, GroupState
from app.providers import registry
from app.repositories import state_transaction
from app.repositories.group_state import load_state, save_state
from app.services.pending_buttons import ControlCompletion


class UnlockedOpeningRaces(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "state.db"
        db_patch = patch.object(db, "DB_PATH", self.db_path)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db._ensure_tables()
        # A fallback opening delivers its reply through run_post_turn_maintenance_after_output, which
        # spawns the memory maintenance as a background task. Left running, it writes into the temporary
        # database directory while the test removes it ("Directory not empty").
        spawn_patch = patch("app.services.post_turn.spawn_post_turn_maintenance")
        spawn_patch.start()
        self.addCleanup(spawn_patch.stop)
        self.group = self.id().replace(".", "-")
        self.seed()

    def seed(self) -> None:
        char = Character(name="first", owner_id="first", str_=55, skills=dict(BASE_SKILLS))
        save_state(GroupState(
            group_id=self.group, timeline_id="timeline-v1", active=True,
            scenario_library_id="scenario-S", active_scenario_source_hash="full-source-hash-v1",
            scenario_text="same active chapter text", characters={"first": char},
        ))

    async def route(self, command: str = "/coc start") -> list[str]:
        replies: list[str] = []

        async def reply(value: str) -> None:
            replies.append(value)

        await router.handle_text_message(
            self.group, "first", lambda owner: owner, reply,
            AsyncMock(), AsyncMock(), AsyncMock(), command,
        )
        return replies

    def change(self, mutation) -> None:
        result = state_transaction.mutate(self.group, mutation, reason="opening_race_test")
        self.assertTrue(result.ok)

    def external_source_write(self, source_hash: str) -> None:
        """Model another process: bypass all Python process locks, keep SQLite atomic."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT data FROM group_states WHERE key = ?", (self.group,)).fetchone()
            data = json.loads(row[0])
            data["active_scenario_source_hash"] = source_hash
            data["state_revision"] += 1
            conn.execute("UPDATE group_states SET data = ? WHERE key = ?", (json.dumps(data), self.group))

    async def paused(self, data: dict, mutation) -> list[str]:
        entered = threading.Event()
        release = threading.Event()

        def extract(_text: str) -> dict:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("opening test barrier")
            return data

        with patch.object(scenario_intro, "extract_opening_narration", side_effect=extract):
            task = asyncio.create_task(self.route())
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            try:
                mutation()
            finally:
                release.set()
            return await task

    def assert_not_started(self, replies: list[str]) -> None:
        saved = load_state(self.group)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])
        self.assertNotIn("OLD OPENING", replies)

    async def test_race_01_scenario_switch_discards_old_opening(self) -> None:
        def switch(ctx) -> None:
            ctx.state.scenario_library_id = "scenario-T"
            ctx.state.active_scenario_source_hash = "full-source-hash-T"
            ctx.state.scenario_text = "T text"
            ctx.state.timeline_id = "timeline-T"
        replies = await self.paused({"found": True, "text": "OLD OPENING"}, lambda: self.change(switch))
        self.assert_not_started(replies)
        self.assertEqual(load_state(self.group).scenario_library_id, "scenario-T")

    async def test_race_02_same_id_repair_same_chapter_text_discards_old_opening(self) -> None:
        replies = await self.paused(
            {"found": True, "text": "OLD OPENING"},
            lambda: self.change(lambda ctx: setattr(
                ctx.state, "active_scenario_source_hash", "full-source-hash-v2")),
        )
        self.assert_not_started(replies)
        saved = load_state(self.group)
        self.assertEqual(saved.scenario_library_id, "scenario-S")
        self.assertEqual(saved.scenario_text, "same active chapter text")
        self.assertIn("請重新輸入", replies[-1])

    async def test_race_03_timeline_replacement_discards_old_opening(self) -> None:
        replies = await self.paused(
            {"found": True, "text": "OLD OPENING"},
            lambda: self.change(lambda ctx: setattr(ctx.state, "timeline_id", "timeline-v2")),
        )
        self.assert_not_started(replies)

    async def test_same_source_hash_new_chapter_context_discards_old_opening(self) -> None:
        def advance(ctx) -> None:
            ctx.state.active_chapter_id = "chapter-2"
            ctx.state.scenario_text = "different active chapter text"
        replies = await self.paused(
            {"found": True, "text": "OLD OPENING"}, lambda: self.change(advance),
        )
        self.assert_not_started(replies)
        self.assertEqual(load_state(self.group).active_scenario_source_hash, "full-source-hash-v1")

    async def test_race_04_two_starts_only_one_authoritative_start(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        count = 0
        count_lock = threading.Lock()
        data = {"found": True, "text": "OLD OPENING", "opening_check": {"type": "skill", "skill": "偵查"}}

        def extract(_text: str) -> dict:
            nonlocal count
            with count_lock:
                count += 1
                if count == 2:
                    entered.set()
            if not release.wait(5):
                raise TimeoutError("double extraction barrier")
            return data

        with patch.object(scenario_intro, "extract_opening_narration", side_effect=extract):
            a = asyncio.create_task(self.route())
            b = asyncio.create_task(self.route())
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            release.set()
            ra, rb = await asyncio.gather(a, b)
        saved = load_state(self.group)
        self.assertEqual(count, 2)
        self.assertTrue(saved.game_started)
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(set(saved.pending_checks), {"first"})
        self.assertEqual(sum("OLD OPENING" in replies for replies in (ra, rb)), 1)

    async def test_race_04_two_fallback_starts_only_one_commit(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        count = 0
        count_lock = threading.Lock()

        def extract(_text: str) -> dict:
            nonlocal count
            with count_lock:
                count += 1
                if count == 2:
                    entered.set()
            if not release.wait(5):
                raise TimeoutError("double fallback extraction barrier")
            return {"found": False}

        class Provider:
            async def run_conversation(self, *_args, **_kwargs) -> str:
                return "ONE FALLBACK"

        with (
            patch.object(scenario_intro, "extract_opening_narration", side_effect=extract),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            a = asyncio.create_task(self.route())
            b = asyncio.create_task(self.route())
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            release.set()
            ra, rb = await asyncio.gather(a, b)
        self.assertTrue(load_state(self.group).game_started)
        self.assertEqual(len(load_state(self.group).log), 2)
        self.assertEqual(sum("ONE FALLBACK" in replies for replies in (ra, rb)), 1)

    async def test_race_05_character_add_retire_switch_require_retry(self) -> None:
        def add(ctx) -> None:
            ctx.state.characters["second"] = Character(
                name="second", owner_id="second", str_=55, skills=dict(BASE_SKILLS),
            )
        def retire(ctx) -> None:
            ctx.state.retire_active_character("first")
        def switch(ctx) -> None:
            ctx.state.set_active_character("first", "alternate-first")

        for name, change in (("add", add), ("retire", retire), ("switch", switch)):
            with self.subTest(name=name):
                db.delete_json("group_states", self.group)
                self.seed()
                if name == "switch":
                    def install_alternate(ctx) -> None:
                        ctx.state.characters_by_id["alternate-first"] = Character(
                            name="alternate", owner_id="first", str_=55,
                            skills=dict(BASE_SKILLS), character_id="alternate-first", active=False,
                        )
                    self.change(install_alternate)
                replies = await self.paused(
                    {"found": True, "text": "OLD OPENING",
                     "opening_check": {"type": "skill", "skill": "偵查"}},
                    lambda change=change: self.change(change),
                )
                self.assert_not_started(replies)
                self.assertEqual(load_state(self.group).pending_checks, {})
                self.assertIn("請重新輸入", replies[-1])

    async def test_race_06_new_pending_pregen_luck_rechecks_admission(self) -> None:
        def add(ctx) -> None:
            ctx.state.pending_pregen_luck["first"] = "legacy-user:first"
        replies = await self.paused({"found": True, "text": "OLD OPENING"}, lambda: self.change(add))
        self.assert_not_started(replies)
        self.assertIn("LUCK", replies[-1])

    async def test_race_07_pending_check_keeps_path_difference(self) -> None:
        for opening_check, starts in ((None, True), ({"type": "skill", "skill": "偵查"}, False)):
            with self.subTest(opening_check=opening_check):
                db.delete_json("group_states", self.group)
                self.seed()
                def add(ctx) -> None:
                    ctx.state.pending_checks["first"] = {"type": "skill", "skill": "古語", "check_id": "old"}
                replies = await self.paused(
                    {"found": True, "text": "OLD OPENING", "opening_check": opening_check},
                    lambda: self.change(add),
                )
                saved = load_state(self.group)
                self.assertEqual(saved.game_started, starts)
                self.assertEqual(saved.pending_checks["first"]["check_id"], "old")
                if not starts:
                    self.assert_not_started(replies)

    async def test_race_08_pending_luck_keeps_path_difference(self) -> None:
        for opening_check, starts in ((None, True), ({"type": "skill", "skill": "偵查"}, False)):
            with self.subTest(opening_check=opening_check):
                db.delete_json("group_states", self.group)
                self.seed()
                def add(ctx) -> None:
                    ctx.state.pending_luck_decisions["first"] = {"decision_id": "pending"}
                replies = await self.paused(
                    {"found": True, "text": "OLD OPENING", "opening_check": opening_check},
                    lambda: self.change(add),
                )
                self.assertEqual(load_state(self.group).game_started, starts)
                if not starts:
                    self.assert_not_started(replies)

    async def test_race_08_fallback_pending_luck_is_not_early_admission(self) -> None:
        def add(ctx) -> None:
            ctx.state.pending_luck_decisions["first"] = {"decision_id": "pending"}
        with patch("app.services.game_opening.supervisor.run_turn", new_callable=AsyncMock,
                   return_value=("fallback result", [], [])) as pipeline:
            replies = await self.paused({"found": False}, lambda: self.change(add))
        pipeline.assert_awaited_once()
        self.assertEqual(replies[-1], "fallback result")

    async def test_race_09_unrelated_revision_may_continue(self) -> None:
        replies = await self.paused(
            {"found": True, "text": "OLD OPENING"},
            lambda: self.change(lambda ctx: setattr(ctx.state, "scenario_title", "metadata only")),
        )
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(replies[-1], "OLD OPENING")

    async def test_race_10_cancelled_extractor_worker_cannot_apply(self) -> None:
        self.change(lambda ctx: ctx.state.characters["first"].skills.pop("偵查", None))
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def extract(_text: str) -> dict:
            entered.set()
            release.wait(5)
            finished.set()
            return {"found": True, "text": "OLD OPENING"}

        with patch.object(scenario_intro, "extract_opening_narration", side_effect=extract):
            task = asyncio.create_task(self.route())
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 3))
        self.assertFalse(load_state(self.group).game_started)
        self.assertEqual(load_state(self.group).log, [])
        self.assertIn("偵查", load_state(self.group).characters["first"].skills)

    async def test_bound_scripted_commit_survives_discord_delivery_failure(self) -> None:
        async def reply(value: str) -> None:
            if value == "OLD OPENING":
                raise RuntimeError("Discord delivery failed")

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={
                "found": True, "text": "OLD OPENING", "opening_check": None,
            }),
            self.assertRaisesRegex(RuntimeError, "Discord delivery failed"),
        ):
            await router.handle_text_message(
                self.group, "first", lambda owner: owner, reply,
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc start",
            )
        self.assertTrue(load_state(self.group).game_started)
        self.assertEqual(len(load_state(self.group).log), 2)
        self.assertIn("已經開始過", (await self.route())[0])

    async def test_race_11_external_sqlite_writer_changes_source(self) -> None:
        replies = await self.paused(
            {"found": True, "text": "OLD OPENING"},
            lambda: self.external_source_write("external-full-hash-v2"),
        )
        self.assert_not_started(replies)

    async def test_race_12_false_extraction_is_revalidated_before_fallback(self) -> None:
        for field, value in (("active_scenario_source_hash", "source-v2"),
                             ("timeline_id", "timeline-v2")):
            with self.subTest(field=field):
                db.delete_json("group_states", self.group)
                self.seed()
                with patch("app.services.game_opening.supervisor.run_turn", new_callable=AsyncMock) as pipeline:
                    replies = await self.paused(
                        {"found": False},
                        lambda field=field, value=value: self.change(
                            lambda ctx: setattr(ctx.state, field, value)),
                    )
                self.assert_not_started(replies)
                pipeline.assert_not_awaited()

    async def test_race_13_source_changes_at_scripted_transaction_boundary(self) -> None:
        original = state_transaction.mutate
        def racing_mutate(conversation_id, mutation, **kwargs):
            if kwargs.get("reason") == "game_opening":
                self.external_source_write("source-v2")
            return original(conversation_id, mutation, **kwargs)
        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": True, "text": "OLD OPENING"}),
            patch.object(state_transaction, "mutate", side_effect=racing_mutate),
        ):
            replies = await self.route()
        self.assert_not_started(replies)

    async def test_race_14_interleaving_preserves_opening_check_instruction_order(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        messages: list[str] = []

        def extract(_text: str) -> dict:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("interleaving barrier")
            return {"found": True, "text": "OLD OPENING",
                    "opening_check": {"type": "skill", "skill": "偵查", "reason": "開場線索"}}

        async def start_reply(value: str) -> None:
            messages.append(value)

        async def other_reply(value: str) -> None:
            messages.append(value)

        with patch.object(scenario_intro, "extract_opening_narration", side_effect=extract):
            task = asyncio.create_task(router.handle_text_message(
                self.group, "first", lambda owner: owner, start_reply,
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc start",
            ))
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            try:
                await asyncio.wait_for(router.handle_text_message(
                    self.group, "first", lambda owner: owner, other_reply,
                    AsyncMock(), AsyncMock(), AsyncMock(), "/coc status",
                ), 3)
            finally:
                release.set()
                await task
        self.assertTrue(messages[0].startswith("📋"))
        self.assertNotEqual(messages[1], "OLD OPENING")
        self.assertEqual(messages[2], "OLD OPENING")
        self.assertIn("開場線索", messages[3])

    async def test_race_15_router_hook_claims_only_after_pending_commit(self) -> None:
        completion = ControlCompletion(self.group, {}, {})
        observed: list[tuple[str, bool, bool]] = []

        async def hook() -> None:
            state = load_state(self.group)
            observed.append(("hook", state.game_started, bool(state.pending_checks)))
            await completion.claim_locked()

        async def reply(value: str) -> None:
            observed.append(("reply", load_state(self.group).game_started, value == "OLD OPENING"))

        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "OLD OPENING",
            "opening_check": {"type": "skill", "skill": "偵查"},
        }):
            await router.handle_text_message(
                self.group, "first", lambda owner: owner, reply,
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc start",
                post_turn_hook=hook,
            )
        self.assertEqual(observed[0][0], "reply")
        self.assertEqual(observed[1], ("hook", False, False))
        self.assertIn(("hook", True, True), observed)
        self.assertEqual(len(completion.intents or []), 1)
        self.assertTrue(load_state(self.group).pending_checks["first"]["_buttons_posted"])

    async def test_prepare_claim_survives_second_hook_without_new_checks(self) -> None:
        self.change(lambda ctx: ctx.state.pending_checks.update({
            "first": {"type": "skill", "skill": "偵查", "check_id": "prior", "timeline_id": "timeline-v1"},
        }))
        completion = ControlCompletion(self.group, {}, {})
        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "OLD OPENING", "opening_check": None,
        }):
            await router.handle_text_message(
                self.group, "first", lambda owner: owner, AsyncMock(),
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc start",
                post_turn_hook=completion.claim_locked,
            )
        self.assertTrue(load_state(self.group).game_started)
        self.assertEqual(len(completion.intents or []), 1)
        self.assertEqual(completion.intents[0].entry["check_id"], "prior")

    async def test_race_16_fallback_source_guard_rejects_narrator_output(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        class Provider:
            async def run_conversation(self, *_args, **_kwargs) -> str:
                entered.set()
                await release.wait()
                return "OLD OPENING"
        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            task = asyncio.create_task(self.route())
            await asyncio.wait_for(entered.wait(), 3)
            self.external_source_write("source-v2")
            release.set()
            replies = await task
        self.assert_not_started(replies)
        self.assertIn("請重新輸入", replies[-1])

    async def test_fallback_narrator_still_holds_conversation_lock(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        class Provider:
            async def run_conversation(self, *_args, **_kwargs) -> str:
                entered.set()
                await release.wait()
                return "fallback"
        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            task = asyncio.create_task(self.route())
            await asyncio.wait_for(entered.wait(), 3)
            self.assertTrue(locks.get_conversation_lock(self.group).locked())
            release.set()
            await task

    async def test_fallback_commit_guard_rejects_same_source_new_chapter(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        class Provider:
            async def run_conversation(self, *_args, **_kwargs) -> str:
                entered.set()
                await release.wait()
                return "OLD OPENING"
        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            task = asyncio.create_task(self.route())
            await asyncio.wait_for(entered.wait(), 3)
            self.change(lambda ctx: setattr(ctx.state, "active_chapter_id", "chapter-2"))
            release.set()
            replies = await task
        self.assert_not_started(replies)

    async def test_stale_fallback_discards_queued_private_and_image_delivery(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        replies: list[str] = []
        send_dm = AsyncMock()
        send_image = AsyncMock()
        send_dm_image = AsyncMock()

        async def narrator(_message):
            entered.set()
            await release.wait()
            return "OLD OPENING", [("first", "OLD SECRET")], [(None, 1)]

        async def reply(value: str) -> None:
            replies.append(value)

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch("app.agents.narrator.run_narrator", side_effect=narrator),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            task = asyncio.create_task(router.handle_text_message(
                self.group, "first", lambda owner: owner, reply,
                send_dm, send_image, send_dm_image, "/coc start",
            ))
            await asyncio.wait_for(entered.wait(), 3)
            self.external_source_write("source-v2")
            release.set()
            await task
        self.assert_not_started(replies)
        send_dm.assert_not_awaited()
        send_image.assert_not_awaited()
        send_dm_image.assert_not_awaited()

    def test_fallback_commit_guard_checks_source_in_sqlite_transaction(self) -> None:
        old = load_state(self.group)
        self.change(lambda ctx: setattr(ctx.state, "active_scenario_source_hash", "source-v2"))
        with self.assertRaises(turn_commit.OpeningStartRejected) as caught:
            turn_commit.commit_turn_result(
                old, [{"role": "assistant", "content": "OLD OPENING"}],
                timeline_id="timeline-v1", start_game=True,
                expected_source_hash="full-source-hash-v1",
            )
        self.assertEqual(caught.exception.reason, "source_changed")
        self.assertFalse(load_state(self.group).game_started)

    def test_fallback_guard_precedes_action_ledger_duplicate_replay(self) -> None:
        old = load_state(self.group)
        entries = [{"role": "assistant", "content": "OLD OPENING"}]
        self.assertTrue(turn_commit.commit_turn_result(
            old, entries, timeline_id="timeline-v1", start_game=True,
            expected_source_hash="full-source-hash-v1", turn_id="same-turn",
        ))
        self.external_source_write("source-v2")
        with self.assertRaises(turn_commit.OpeningStartRejected) as caught:
            turn_commit.commit_turn_result(
                old, entries, timeline_id="timeline-v1", start_game=True,
                expected_source_hash="full-source-hash-v1", turn_id="same-turn",
            )
        self.assertIn(caught.exception.reason, {"source_changed", "already_started"})
        self.assertEqual(len(load_state(self.group).log), 1)
