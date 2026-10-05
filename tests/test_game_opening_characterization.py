"""Persistent behavior of /coc start before its orchestration moves modules."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import config, db, keeper, scenario_intro
from app.commands import router
from app.commands.handlers import system
from app.models import BASE_SKILLS, Character, GroupState
from app.providers import registry
from app.repositories import state_transaction
from app.repositories.group_state import StateRevisionConflict, load_state, save_state
from app.services import game_opening
from app.services.pending_buttons import ControlCompletion


def _character(owner: str, *, needs_healing: bool = False) -> Character:
    skills = dict(BASE_SKILLS)
    if needs_healing:
        skills.pop("偵查")
    return Character(name=owner, owner_id=owner, str_=55, skills=skills)


class GameOpeningCharacterization(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "state.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db._ensure_tables()
        self.group = self.id().replace(".", "-")

    def store(self, *, owners: tuple[str, ...] = ("first",), **fields: object) -> GroupState:
        state = GroupState(
            group_id=self.group, timeline_id="timeline-opening", active=True,
            scenario_text="開場劇本", characters={owner: _character(owner) for owner in owners},
        )
        for name, value in fields.items():
            setattr(state, name, value)
        save_state(state)
        return load_state(self.group)

    async def start(self) -> list[str]:
        replies: list[str] = []

        async def reply(message: str) -> None:
            replies.append(message)

        await system.handle_system_command(
            self.group, "first", reply, AsyncMock(), AsyncMock(), AsyncMock(),
            ["/coc", "start"],
        )
        return replies

    async def test_readiness_admission_rejects_without_mutation(self) -> None:
        for change, expected in (
            ({"active": False}, "還沒有載入劇本"),
            ({"scenario_text": ""}, "還沒有載入劇本"),
            ({"characters": {}}, "沒有任何調查員"),
            ({"pending_pregen_luck": {"first": "legacy-user:first"}}, "尚未由玩家擲 LUCK"),
            ({"game_started": True}, "已經開始過"),
        ):
            with self.subTest(change=change):
                # Each case gets a fresh row, preserving the real SQLite path.
                db.delete_json("group_states", self.group)
                before = self.store(**change)
                with patch.object(scenario_intro, "extract_opening_narration") as extract:
                    replies = await self.start()
                self.assertIn(expected, replies[0])
                self.assertEqual(load_state(self.group).state_revision, before.state_revision)
                self.assertFalse(extract.called)

    async def test_combat_replacement_guard_precedes_readiness(self) -> None:
        before = self.store()
        state = load_state(self.group)
        state.combat.active = True
        save_state(state)
        with patch.object(scenario_intro, "extract_opening_narration") as extract:
            replies = await self.start()
        saved = load_state(self.group)
        self.assertIn("戰鬥尚未結算", replies[0])
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.state_revision, before.state_revision + 1)
        extract.assert_not_called()

    async def test_healing_is_committed_before_extraction_and_survives_failure(self) -> None:
        before = self.store(characters={"first": _character("first", needs_healing=True)})
        seen: list[str] = []

        def fail_extraction(_text: str) -> dict:
            seen.append("extract")
            raise RuntimeError("provider escaped")

        async def reply(message: str) -> None:
            if message.startswith("📋"):
                saved = load_state(self.group)
                self.assertGreater(saved.state_revision, before.state_revision)
                self.assertIn("偵查", saved.characters["first"].skills)
                seen.append("roster")

        with (
            patch.object(scenario_intro, "extract_opening_narration", side_effect=fail_extraction),
            self.assertRaisesRegex(RuntimeError, "provider escaped"),
        ):
            await system.handle_system_command(
                self.group, "first", reply, AsyncMock(), AsyncMock(), AsyncMock(),
                ["/coc", "start"],
            )
        saved = load_state(self.group)
        self.assertEqual(seen, ["roster", "extract"])
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])
        self.assertEqual(saved.state_revision, before.state_revision + 1)

    async def test_readiness_callback_failure_keeps_healing_and_stops_opening(self) -> None:
        character = _character("first", needs_healing=True)
        character.hp = 0
        character.hp_max = 0
        before = self.store(characters={"first": character})
        delivered: list[str] = []

        async def fail_readiness(readiness: game_opening.OpeningReadiness) -> None:
            self.assertEqual(readiness.investigators[0].stats, "HP 10/10, SAN 50/99")
            self.assertTrue(readiness.investigators[0].notes)
            with self.assertRaises(FrozenInstanceError):
                readiness.unclaimed_pregens = 5
            delivered.append("roster attempted")
            raise RuntimeError("Discord roster failed")

        with (
            patch.object(scenario_intro, "extract_opening_narration") as extract,
            self.assertRaisesRegex(RuntimeError, "Discord roster failed"),
        ):
            await game_opening.open_game(self.group, "first", on_readiness=fail_readiness)
        saved = load_state(self.group)
        self.assertEqual(delivered, ["roster attempted"])
        self.assertEqual(saved.state_revision, before.state_revision + 1)
        self.assertEqual(saved.characters["first"].hp_max, 10)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.pending_checks, {})
        self.assertEqual(saved.log, [])
        extract.assert_not_called()

        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "重試開場", "opening_check": None,
        }):
            replies = await self.start()
        self.assertEqual(replies[-1], "重試開場")
        self.assertTrue(load_state(self.group).game_started)

    async def test_no_healing_means_no_extra_commit(self) -> None:
        before = self.store()
        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "開場", "opening_check": None,
        }):
            replies = await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.state_revision, before.state_revision + 1)
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(
            replies[0],
            "📋 全團調查員集結就緒名冊\n\n"
            "・【first】職業：自由人（玩家：first）：HP 10/10, SAN 50/99",
        )
        self.assertEqual(replies[1], "開場")

    async def test_no_check_scripted_start_ignores_existing_pending(self) -> None:
        pending = {"first": {"type": "skill", "skill": "偵查", "check_id": "old", "timeline_id": "timeline-opening"}}
        self.store(pending_checks=pending, pending_luck_decisions={"first": {"decision_id": "old-luck"}})
        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "開場", "opening_check": None,
        }):
            await self.start()
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(saved.pending_checks, pending)
        self.assertEqual(saved.pending_luck_decisions["first"]["decision_id"], "old-luck")

    async def test_group_check_blocker_is_atomic_but_prior_healing_remains(self) -> None:
        pending = {"second": {"type": "skill", "skill": "偵查", "check_id": "old", "timeline_id": "timeline-opening"}}
        before = self.store(
            characters={
                "first": _character("first", needs_healing=True),
                "second": _character("second"),
                "third": _character("third"),
            },
            pending_checks=pending,
        )
        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "開場", "opening_check": {"type": "skill", "skill": "古語"},
        }):
            replies = await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.state_revision, before.state_revision + 1)
        self.assertEqual(saved.pending_checks, pending)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])
        self.assertNotIn("古語", saved.characters["first"].skills)
        self.assertIn("偵查", saved.characters["first"].skills)
        self.assertIn("尚有待處理的檢定", replies[-1])

    async def test_group_luck_blocker_is_atomic(self) -> None:
        pending_luck = {"second": {"decision_id": "old-luck"}}
        before = self.store(owners=("first", "second"), pending_luck_decisions=pending_luck)
        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "開場", "opening_check": {"type": "skill", "skill": "古語"},
        }):
            replies = await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.state_revision, before.state_revision)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.pending_checks, {})
        self.assertEqual(saved.log, [])
        self.assertEqual(saved.pending_luck_decisions, pending_luck)
        self.assertIn("等待 Luck 決定", replies[-1])

    async def test_scripted_skill_and_sanity_commit_checks_history_and_start_together(self) -> None:
        for opening_check in (
            {"type": "skill", "skill": "古語", "reason": "讀碑文"},
            {"type": "sanity", "loss_success": "0", "loss_failure": "1d6", "reason": "目睹異象"},
        ):
            with self.subTest(opening_check=opening_check):
                db.delete_json("group_states", self.group)
                before = self.store(owners=("first", "second"))
                with patch.object(scenario_intro, "extract_opening_narration", return_value={
                    "found": True, "text": "開場", "opening_check": opening_check,
                }):
                    replies = await self.start()
                saved = load_state(self.group)
                self.assertEqual(saved.state_revision, before.state_revision + 1)
                self.assertTrue(saved.game_started)
                self.assertEqual(set(saved.pending_checks), {"first", "second"})
                self.assertEqual(len({v["check_id"] for v in saved.pending_checks.values()}), 2)
                self.assertEqual([x["record_kind"] for x in saved.log], ["opening_instruction", "narrative"])
                self.assertEqual([x["authority"] for x in saved.log], ["claim", "presentation"])
                self.assertEqual(len({x["turn_id"] for x in saved.log}), 1)
                self.assertEqual({x["timeline_id"] for x in saved.log}, {saved.timeline_id})
                self.assertEqual(replies[1], "開場")
                self.assertIn(opening_check["reason"], replies[2])
                if opening_check["type"] == "skill":
                    self.assertTrue(all("古語" in c.skills for c in saved.characters.values()))

    async def test_malformed_skill_check_is_ignored_by_existing_intro_adapter(self) -> None:
        self.store()

        class Provider:
            def analyze_text(self, *_args: object) -> dict:
                return {
                    "found": True, "text": "開場", "opening_check": {"type": "skill", "skill": ""},
                }

        with patch.object(scenario_intro, "conversation_provider", return_value=Provider()):
            replies = await self.start()
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(saved.pending_checks, {})
        self.assertEqual(replies[-1], "開場")

    async def test_provider_without_scripted_opening_uses_fallback_pipeline(self) -> None:
        self.store()

        class Provider:
            async def run_conversation(self, *_args: object, **_kwargs: object) -> str:
                return "後備開場"

        with (
            patch.object(scenario_intro, "conversation_provider", return_value=None),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            replies = await self.start()
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(replies[1], "後備開場")

    async def test_fallback_timeline_change_in_narrator_returns_stale_response(self) -> None:
        self.store()

        class Provider:
            async def run_conversation(self, *_args: object, **_kwargs: object) -> str:
                state_transaction.mutate(
                    self_group, lambda ctx: setattr(ctx.state, "timeline_id", "timeline-new"),
                    reason="test_timeline_replacement",
                )
                return "舊開場"

        self_group = self.group
        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            replies = await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.timeline_id, "timeline-new")
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])
        self.assertNotIn("舊開場", replies)
        self.assertIn("舊回覆未送出", replies[-1])

    async def test_real_fallback_narrator_failure_is_retryable(self) -> None:
        before = self.store(characters={"first": _character("first", needs_healing=True)})

        class Provider:
            async def run_conversation(self, *_args: object, **_kwargs: object) -> str:
                raise RuntimeError("provider failed")

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            replies = await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.state_revision, before.state_revision + 1)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])
        self.assertIn("遊戲尚未開始", replies[-1])

    async def test_scripted_delivery_failure_does_not_undo_start(self) -> None:
        self.store()
        delivered: list[str] = []

        async def reply(value: str) -> None:
            delivered.append(value)
            if value == "開場":
                raise RuntimeError("Discord opening failed")

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={
                "found": True, "text": "開場", "opening_check": {"type": "skill", "skill": "偵查"},
            }),
            self.assertRaisesRegex(RuntimeError, "Discord opening failed"),
        ):
            await system.handle_system_command(
                self.group, "first", reply, AsyncMock(), AsyncMock(), AsyncMock(),
                ["/coc", "start"],
            )
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(len(saved.log), 2)
        self.assertIn("first", saved.pending_checks)
        self.assertEqual(delivered[1], "開場")
        self.assertIn("已經開始過", (await self.start())[0])

    async def test_fallback_delivery_failure_does_not_undo_start(self) -> None:
        self.store()

        class Provider:
            async def run_conversation(self, *_args: object, **_kwargs: object) -> str:
                return "後備開場"

        async def reply(value: str) -> None:
            if value == "後備開場":
                raise RuntimeError("Discord fallback failed")

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
            self.assertRaisesRegex(RuntimeError, "Discord fallback failed"),
        ):
            await system.handle_system_command(
                self.group, "first", reply, AsyncMock(), AsyncMock(), AsyncMock(),
                ["/coc", "start"],
            )
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(len(saved.log), 2)
        self.assertIn("已經開始過", (await self.start())[0])

    async def test_fallback_output_precedes_private_and_image_delivery(self) -> None:
        self.store()
        order: list[str] = []

        async def reply(value: str) -> None:
            order.append("roster" if value.startswith("📋") else "public")

        async def send_dm(_owner: str, _message: str) -> None:
            order.append("private")

        async def send_image(_image: bytes, _conversation: str, _page: int) -> None:
            order.append("image")

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.object(game_opening.supervisor, "run_turn", new_callable=AsyncMock,
                         return_value=("後備開場", [("first", "秘密")], [(None, 1)])),
            patch("app.services.post_turn.load_page_image", return_value=b"PNG"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            await system.handle_system_command(
                self.group, "first", reply, send_dm, send_image, AsyncMock(),
                ["/coc", "start"],
            )
        self.assertEqual(order, ["roster", "public", "private", "image"])

    async def test_cancel_after_scripted_commit_keeps_start_and_blocks_retry(self) -> None:
        self.store()

        async def reply(value: str) -> None:
            if value == "開場":
                raise asyncio.CancelledError

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={
                "found": True, "text": "開場", "opening_check": None,
            }),
            self.assertRaises(asyncio.CancelledError),
        ):
            await system.handle_system_command(
                self.group, "first", reply, AsyncMock(), AsyncMock(), AsyncMock(),
                ["/coc", "start"],
            )
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(len(saved.log), 2)
        self.assertIn("已經開始過", (await self.start())[0])

    async def test_fallback_failure_keeps_healing_and_is_retryable(self) -> None:
        before = self.store(characters={"first": _character("first", needs_healing=True)})
        failed = "（開場生成暫時失敗，遊戲尚未開始；請稍後再輸入 /coc start。）"
        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.object(game_opening.supervisor, "run_turn", new_callable=AsyncMock, return_value=(failed, [], [])),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            first = await self.start()
            second = await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.state_revision, before.state_revision + 1)
        self.assertIn("偵查", saved.characters["first"].skills)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])
        self.assertEqual(first[-1], failed)
        self.assertEqual(second[-1], failed)

    async def test_fallback_does_not_admit_generic_pending_check(self) -> None:
        pending = {"first": {"type": "skill", "skill": "偵查", "check_id": "old"}}
        self.store(pending_checks=pending)
        pipeline = AsyncMock(return_value=("暫時失敗", [], []))
        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.object(game_opening.supervisor, "run_turn", pipeline),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            replies = await self.start()
        self.assertEqual(replies[1], "暫時失敗")
        pipeline.assert_awaited_once()
        self.assertEqual(load_state(self.group).pending_checks, pending)

    async def test_cancel_during_fallback_generation_keeps_opening_retryable(self) -> None:
        self.store(characters={"first": _character("first", needs_healing=True)})
        entered = asyncio.Event()

        async def generation(**_kwargs: object):
            entered.set()
            await asyncio.Future()

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.object(game_opening.supervisor, "run_turn", side_effect=generation),
        ):
            task = asyncio.create_task(self.start())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        saved = load_state(self.group)
        self.assertIn("偵查", saved.characters["first"].skills)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])

    async def test_router_serializes_double_start_and_keeps_extraction_locked(self) -> None:
        self.store()
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def extract(_text: str) -> dict:
            calls.append("extract")
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test extraction barrier")
            return {"found": True, "text": "開場", "opening_check": None}

        async def route() -> list[str]:
            replies: list[str] = []

            async def reply(value: str) -> None:
                replies.append(value)

            await router.handle_text_message(
                self.group, "first", lambda _owner: "first", reply,
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc start",
            )
            return replies

        with patch.object(scenario_intro, "extract_opening_narration", side_effect=extract):
            first = asyncio.create_task(route())
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = asyncio.create_task(route())
            await asyncio.sleep(0.02)
            self.assertFalse(second.done())
            self.assertEqual(calls, ["extract"])
            release.set()
            one, two = await asyncio.gather(first, second)
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(calls, ["extract"])
        self.assertEqual(one[1], "開場")
        self.assertIn("已經開始過", two[0])

    async def test_router_serializes_double_fallback_start(self) -> None:
        self.store()
        entered = asyncio.Event()
        release = asyncio.Event()

        class Provider:
            calls = 0

            async def run_conversation(self, *_args: object, **_kwargs: object) -> str:
                self.calls += 1
                entered.set()
                await release.wait()
                return "後備開場"

        provider = Provider()

        async def route() -> list[str]:
            replies: list[str] = []

            async def reply(value: str) -> None:
                replies.append(value)

            await router.handle_text_message(
                self.group, "first", lambda _owner: "first", reply,
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc start",
            )
            return replies

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": provider}),
            patch.object(config, "LLM_PROVIDER", "openai"),
            patch("app.services.post_turn.spawn_post_turn_maintenance"),
        ):
            first = asyncio.create_task(route())
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(route())
            await asyncio.sleep(0.02)
            self.assertFalse(second.done())
            release.set()
            one, two = await asyncio.gather(first, second)
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(one[1], "後備開場")
        self.assertIn("已經開始過", two[0])

    async def test_cancel_during_extraction_keeps_healing_but_no_start(self) -> None:
        self.store(characters={"first": _character("first", needs_healing=True)})
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def extract(_text: str) -> dict:
            entered.set()
            release.wait(3)
            finished.set()
            return {"found": True, "text": "開場", "opening_check": None}

        with patch.object(scenario_intro, "extract_opening_narration", side_effect=extract):
            task = asyncio.create_task(self.start())
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        saved = load_state(self.group)
        self.assertIn("偵查", saved.characters["first"].skills)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.pending_checks, {})
        self.assertEqual(saved.log, [])

    async def test_post_turn_hook_claims_opening_check_after_commit(self) -> None:
        self.store()
        completion = ControlCompletion(self.group, {}, {})
        replies: list[str] = []

        async def reply(value: str) -> None:
            replies.append(value)

        with patch.object(scenario_intro, "extract_opening_narration", return_value={
            "found": True, "text": "開場", "opening_check": {"type": "skill", "skill": "偵查"},
        }):
            await router.handle_text_message(
                self.group, "first", lambda _owner: "first", reply,
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc start",
                post_turn_hook=completion.claim_locked,
            )
        saved = load_state(self.group)
        self.assertTrue(saved.game_started)
        self.assertTrue(saved.pending_checks["first"]["_buttons_posted"])
        self.assertEqual(len(completion.intents or []), 1)
        self.assertEqual(completion.intents[0].entry["check_id"], saved.pending_checks["first"]["check_id"])
        self.assertEqual(replies[1], "開場")

    async def test_stale_scripted_commit_rolls_back_checks_and_history(self) -> None:
        self.store()
        original = state_transaction.commit_snapshot

        def conflict(state: GroupState, **kwargs: object):
            if state.game_started:
                state_transaction.mutate(
                    self.group, lambda ctx: setattr(ctx.state, "scenario_title", "newer write"),
                    reason="test_conflict",
                )
            return original(state, **kwargs)

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={
                "found": True, "text": "開場", "opening_check": {"type": "skill", "skill": "古語"},
            }),
            patch.object(state_transaction, "commit_snapshot", side_effect=conflict),
            self.assertRaises(StateRevisionConflict),
        ):
            await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.scenario_title, "newer write")
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.pending_checks, {})
        self.assertEqual(saved.log, [])

    async def test_scripted_timeline_change_before_commit_does_not_half_start(self) -> None:
        self.store()
        original = state_transaction.commit_snapshot

        def replace_timeline(state: GroupState, **kwargs: object):
            if state.game_started:
                state_transaction.mutate(
                    self.group, lambda ctx: setattr(ctx.state, "timeline_id", "timeline-new"),
                    reason="test_timeline_replacement",
                )
            return original(state, **kwargs)

        with (
            patch.object(scenario_intro, "extract_opening_narration", return_value={
                "found": True, "text": "舊開場", "opening_check": {"type": "skill", "skill": "古語"},
            }),
            patch.object(state_transaction, "commit_snapshot", side_effect=replace_timeline),
            self.assertRaises(state_transaction.StaleTimelineError),
        ):
            await self.start()
        saved = load_state(self.group)
        self.assertEqual(saved.timeline_id, "timeline-new")
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.pending_checks, {})
        self.assertEqual(saved.log, [])

    async def test_fallback_timeline_mismatch_does_not_start(self) -> None:
        self.store()
        old = load_state(self.group)
        state_transaction.mutate(
            self.group, lambda ctx: setattr(ctx.state, "timeline_id", "timeline-new"),
            reason="test_timeline_replacement",
        )
        self.assertFalse(keeper._commit_turn_result(
            old, [{"role": "assistant", "content": "舊開場"}],
            timeline_id="timeline-opening", start_game=True,
        ))
        saved = load_state(self.group)
        self.assertFalse(saved.game_started)
        self.assertEqual(saved.log, [])
