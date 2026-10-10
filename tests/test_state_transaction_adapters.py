"""The callers that sit on the state transaction keep their contracts.

Each test drives a real entry point (Keeper tool, turn commit, command handler,
pending-button claim, rollback) against the throwaway SQLite database.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app import checkpoints, observability, tool_dispatch, turn_commit
from app.commands.handlers import character as character_handler
from app.keeper_tools import support
from app.models import Character, GroupState
from app.repositories import group_state, state_transaction
from app.services import pending_buttons


def _conversation() -> str:
    return f"discord-channel-adapter-{uuid4().hex[:10]}"


def _investigator(owner_id: str = "u1", **kwargs) -> Character:
    values = {"name": "Ada", "owner_id": owner_id, "character_id": f"char-{owner_id}",
              "hp": 20, "hp_max": 20, "san": 60, "skills": {"偵查": 50}}
    values.update(kwargs)
    return Character(**values)


def _seed(conversation_id: str, *characters: Character, active: bool = True) -> GroupState:
    state = GroupState(group_id=conversation_id, active=active)
    for character in characters:
        state.characters[character.owner_id] = character
        state.characters_by_id[character.character_id] = character
        state.active_character_id_by_user[character.owner_id] = character.character_id
    group_state.save_state(state)
    return group_state.load_state(conversation_id)


class TurnCommitTests(unittest.TestCase):
    ENTRIES = ({"role": "user", "content": "我檢查門鎖"}, {"role": "assistant", "content": "鎖是新的。"})

    def test_retrying_the_same_turn_commit_logs_it_once(self):
        conversation = _conversation()
        state = _seed(conversation)
        timeline = state.timeline_id
        first = turn_commit.commit_turn_result(state, [dict(e) for e in self.ENTRIES], timeline_id=timeline, turn_id="turn-1")
        retry = turn_commit.commit_turn_result(state, [dict(e) for e in self.ENTRIES], timeline_id=timeline, turn_id="turn-1")
        self.assertTrue(first and retry)
        self.assertEqual(len(group_state.load_state(conversation).log), 2)

    def test_a_deliberate_repeat_in_a_new_turn_is_logged_again(self):
        conversation = _conversation()
        state = _seed(conversation)
        timeline = state.timeline_id
        turn_commit.commit_turn_result(state, [dict(e) for e in self.ENTRIES], timeline_id=timeline, turn_id="turn-1")
        turn_commit.commit_turn_result(state, [dict(e) for e in self.ENTRIES], timeline_id=timeline, turn_id="turn-2")
        self.assertEqual(len(group_state.load_state(conversation).log), 4)

    def test_same_turn_with_different_entries_is_a_different_action(self):
        conversation = _conversation()
        state = _seed(conversation)
        timeline = state.timeline_id
        turn_commit.commit_turn_result(state, [dict(self.ENTRIES[0])], timeline_id=timeline, turn_id="turn-1")
        turn_commit.commit_turn_result(state, [dict(self.ENTRIES[1])], timeline_id=timeline, turn_id="turn-1")
        self.assertEqual(len(group_state.load_state(conversation).log), 2)

    def test_a_turn_from_before_a_reset_is_not_committed_and_the_snapshot_is_refreshed(self):
        conversation = _conversation()
        state = _seed(conversation)
        old_timeline = state.timeline_id
        state_transaction.mutate(
            conversation, lambda ctx: ctx.replace_state(GroupState(group_id=conversation)), reason="newgame",
        )
        with patch.object(observability, "event") as event:
            committed = turn_commit.commit_turn_result(
                state, [dict(e) for e in self.ENTRIES], timeline_id=old_timeline, turn_id="turn-old",
            )
        self.assertFalse(committed)
        self.assertEqual(group_state.load_state(conversation).log, [])
        self.assertEqual(state.timeline_id, group_state.load_state(conversation).timeline_id)
        self.assertIn("state.turn_commit_skipped", [call.args[0] for call in event.call_args_list])

    def test_a_turn_commit_does_not_overwrite_a_tool_change_made_after_the_turn_loaded(self):
        conversation = _conversation()
        state = _seed(conversation, _investigator())
        tool_dispatch.execute_tool(state, "adjust_character", {"investigator": "Ada", "field": "hp", "delta": -3}, [], [])
        stale = group_state.load_state(conversation)
        stale.characters["u1"].hp = 20  # a snapshot that predates the tool call
        stale.state_revision -= 1
        turn_commit.commit_turn_result(stale, [dict(e) for e in self.ENTRIES], timeline_id=state.timeline_id, turn_id="t")
        stored = group_state.load_state(conversation)
        self.assertEqual(stored.characters["u1"].hp, 17)
        self.assertEqual(len(stored.log), 2)

    def test_the_run_without_a_request_context_still_has_a_stable_id_when_the_caller_passes_one(self):
        self.assertEqual(observability.current_context().get("turn_id"), None)
        conversation = _conversation()
        state = _seed(conversation)
        for _ in range(2):
            turn_commit.commit_turn_result(
                state, [dict(e) for e in self.ENTRIES], timeline_id=state.timeline_id, turn_id="owned-by-run_turn",
            )
        self.assertEqual(len(group_state.load_state(conversation).log), 2)


class KeeperToolAdapterTests(unittest.TestCase):
    def test_concurrent_tools_on_different_resources_lose_no_update(self):
        conversation = _conversation()
        _seed(conversation, _investigator())
        gate = threading.Barrier(2)

        def damage(field: str) -> None:
            local = group_state.load_state(conversation)  # each worker holds its own snapshot
            gate.wait()
            for _ in range(6):
                result = tool_dispatch.execute_tool(
                    local, "adjust_character", {"investigator": "Ada", "field": field, "delta": -1}, [], [],
                )
                assert result["ok"], result

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(damage, "hp"), pool.submit(damage, "san")]:
                future.result()
        stored = group_state.load_state(conversation)
        self.assertEqual((stored.characters["u1"].hp, stored.characters["u1"].san), (14, 54))

    def test_a_tool_snapshot_from_before_a_reset_is_refused(self):
        conversation = _conversation()
        state = _seed(conversation, _investigator())
        state_transaction.mutate(
            conversation, lambda ctx: ctx.replace_state(GroupState(group_id=conversation)), reason="newgame",
        )
        result = tool_dispatch.execute_tool(
            state, "adjust_character", {"investigator": "Ada", "field": "hp", "delta": -1}, [], [],
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(group_state.load_state(conversation).characters, {})

    def test_a_noop_mutation_does_not_advance_the_revision_and_refreshes_the_snapshot(self):
        conversation = _conversation()
        snapshot = _seed(conversation, _investigator())
        revision = snapshot.state_revision
        value = support.mutate_tool_state(snapshot, lambda latest: support.ToolStateMutation("nothing", should_save=False))
        self.assertEqual(value, "nothing")
        self.assertEqual(group_state.load_state(conversation).state_revision, revision)
        support.mutate_tool_state(snapshot, lambda latest: setattr(latest, "mechanical_round", 2))
        self.assertEqual(group_state.load_state(conversation).state_revision, revision + 1)
        self.assertEqual(snapshot.state_revision, revision + 1)

    def test_a_legacy_state_without_a_timeline_gets_one_before_the_turn(self):
        conversation = _conversation()
        from app import db
        db.set_json("group_states", conversation, GroupState(group_id=conversation).to_dict())
        snapshot = group_state.load_state(conversation)
        timeline = turn_commit.ensure_turn_timeline(snapshot)
        self.assertTrue(timeline.startswith("timeline-"))
        self.assertEqual(group_state.load_state(conversation).timeline_id, timeline)
        self.assertEqual(turn_commit.ensure_turn_timeline(snapshot), timeline)


class HandlerAdapterTests(unittest.TestCase):
    def test_a_command_handler_and_a_keeper_tool_do_not_overwrite_each_other(self):
        conversation = _conversation()
        _seed(conversation, _investigator())
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        async def setskill() -> None:
            for index in range(6):
                await character_handler.handle_character_command(
                    conversation, "u1", reply, AsyncMock(), ["/coc", "setskill", "Ada", f"技能{index}", "40"],
                )

        def tool_hits() -> None:
            local = group_state.load_state(conversation)
            for _ in range(6):
                tool_dispatch.execute_tool(local, "adjust_character", {"investigator": "Ada", "field": "hp", "delta": -1}, [], [])

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tool_future = pool.submit(tool_hits)
            command_future = pool.submit(lambda: asyncio.run(setskill()))
            tool_future.result()
            command_future.result()
        stored = group_state.load_state(conversation)
        self.assertEqual(stored.characters["u1"].hp, 14)
        self.assertEqual({f"技能{index}" for index in range(6)} - set(stored.characters["u1"].skills), set())
        self.assertEqual(len(replies), 6)

    def test_a_validation_made_on_a_stale_read_is_repeated_on_the_latest_state(self):
        """/coc pc is refused when the character appeared between the first read and the write."""
        conversation = _conversation()
        _seed(conversation)
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        original_load = character_handler.load_state

        def load_then_race(group_id: str) -> GroupState:
            loaded = original_load(group_id)
            if not getattr(load_then_race, "raced", False):
                load_then_race.raced = True  # type: ignore[attr-defined]
                state_transaction.mutate(
                    group_id, lambda ctx: ctx.state.characters.update({"u1": _investigator()}), reason="race",
                )
                state_transaction.mutate(
                    group_id, lambda ctx: ctx.state.set_active_character("u1", "char-u1"), reason="race",
                )
            return loaded

        with patch.object(character_handler, "load_state", load_then_race):
            asyncio.run(character_handler.handle_character_command(
                conversation, "u1", reply, AsyncMock(), ["/coc", "pc", "Bob", "偵探"],
            ))
        self.assertTrue(any("已經有角色" in text for text in replies), replies)
        self.assertEqual(group_state.load_state(conversation).characters["u1"].name, "Ada")


class PendingButtonClaimTests(unittest.TestCase):
    def test_the_claim_marks_the_latest_pending_entry_and_keeps_concurrent_changes(self):
        conversation = _conversation()
        state = _seed(conversation, _investigator())
        state_transaction.mutate(
            conversation,
            lambda ctx: ctx.state.pending_checks.update({"u1": {"type": "skill", "skill": "偵查", "check_id": "chk-1"}}),
        )
        state_transaction.mutate(conversation, lambda ctx: setattr(ctx.state.characters["u1"], "hp", 11))
        intents = asyncio.run(pending_buttons.claim_pending_buttons_locked(conversation, {}, {}))
        stored = group_state.load_state(conversation)
        self.assertEqual([(i.kind, i.owner_id) for i in intents], [("check", "u1")])
        self.assertTrue(stored.pending_checks["u1"]["_buttons_posted"])
        self.assertEqual(stored.characters["u1"].hp, 11)
        self.assertEqual(intents[0].timeline_id, state.timeline_id)
        # A second claim has nothing new to post and writes nothing.
        revision = stored.state_revision
        self.assertEqual(asyncio.run(pending_buttons.claim_pending_buttons_locked(conversation, {}, {})), [])
        self.assertEqual(group_state.load_state(conversation).state_revision, revision)


class RollbackAtomicityTests(unittest.TestCase):
    def test_a_failed_rollback_leaves_no_pre_rollback_checkpoint_and_no_state_change(self):
        conversation = _conversation()
        state = _seed(conversation)
        checkpoint = checkpoints.create_checkpoint(state, label="before")
        state_transaction.mutate(conversation, lambda ctx: setattr(ctx.state, "mechanical_round", 5))
        before = checkpoints.list_checkpoints(conversation)
        with patch.object(group_state, "write_state_tx", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            checkpoints.rollback(conversation, checkpoint["checkpoint_id"], actor_id="kp")
        self.assertEqual(checkpoints.list_checkpoints(conversation), before)
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 5)

    def test_a_rollback_starts_a_new_timeline_so_old_buttons_go_stale(self):
        conversation = _conversation()
        state = _seed(conversation)
        old_timeline = state.timeline_id
        checkpoint = checkpoints.create_checkpoint(state, label="before")
        restored, _, pre = checkpoints.rollback(conversation, checkpoint["checkpoint_id"], actor_id="kp")
        self.assertNotEqual(restored.timeline_id, old_timeline)
        self.assertEqual(group_state.load_state(conversation).timeline_id, restored.timeline_id)
        stale = state_transaction.mutate(
            conversation, lambda ctx: None, expected_timeline=old_timeline, action_id="old-button",
        )
        self.assertEqual(stale.outcome, state_transaction.Outcome.STALE_TIMELINE)
        self.assertIn(pre["checkpoint_id"], {c["checkpoint_id"] for c in checkpoints.list_checkpoints(conversation)})


if __name__ == "__main__":
    unittest.main()


class ValueHelpersRefuseReplayableActions(unittest.TestCase):
    """A replayed action keeps its stored receipt in ``TxResult.result``; the value helpers would hand back ``None``
    for it, so they refuse an ``action_id`` instead of returning something that looks like a success."""

    def test_both_helpers_refuse_an_action_id_and_write_nothing(self):
        conversation = _conversation()
        state = _seed(conversation, _investigator())
        before = group_state.load_state(conversation).state_revision
        with self.assertRaisesRegex(ValueError, "replayed action"):
            state_transaction.mutate_value(conversation, lambda ctx: 1, reason="test", action_id="a1")
        with self.assertRaisesRegex(ValueError, "replayed action"):
            state_transaction.run_snapshot(state, lambda ctx: 1, reason="test", action_id="a1")
        self.assertEqual(group_state.load_state(conversation).state_revision, before)
