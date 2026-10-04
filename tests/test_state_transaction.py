"""Storage-level behaviour of the single game-state write boundary.

These tests use the real SQLite database that ``tests/conftest.py`` points at a
throwaway directory; nothing here fakes the repository. Concurrency is real
threads and real subprocesses, and the "restart" assertions read the file with a
fresh connection rather than through the module under test.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from app import db, locks
from app.models import Character, GroupState
from app.repositories import group_state, state_transaction
from app.repositories.state_transaction import Outcome, TxReject
from app.services import mutation_admission


def _conversation() -> str:
    return f"discord-channel-tx-{uuid4().hex[:10]}"


def _seed(conversation_id: str, **characters: Character) -> GroupState:
    state = GroupState(group_id=conversation_id)
    for owner_id, character in characters.items():
        state.characters[owner_id] = character
        state.characters_by_id[character.character_id or owner_id] = character
    group_state.save_state(state)
    return group_state.load_state(conversation_id)


def _raw_row(conversation_id: str) -> dict | None:
    """Read the state with a brand-new connection, as a restarted process would."""
    connection = sqlite3.connect(db.DB_PATH)
    try:
        row = connection.execute(
            "SELECT data FROM group_states WHERE key = ?", (conversation_id,)
        ).fetchone()
    finally:
        connection.close()
    return json.loads(row[0]) if row else None


def _ledger_rows(conversation_id: str) -> list[dict]:
    connection = sqlite3.connect(db.DB_PATH)
    try:
        rows = connection.execute("SELECT key, data FROM state_actions").fetchall()
    finally:
        connection.close()
    prefix = conversation_id + "\x1f"
    return [json.loads(data) for key, data in rows if key.startswith(prefix)]


def _bump_round(ctx: state_transaction.TxContext) -> int:
    ctx.state.mechanical_round += 1
    return ctx.state.mechanical_round


class StateTransactionTests(unittest.TestCase):
    # S1 -------------------------------------------------------------------
    def test_concurrent_deltas_to_different_resources_are_all_kept(self):
        conversation = _conversation()
        ada = Character(name="Ada", owner_id="u1", hp=20, hp_max=20, san=60, character_id="c1")
        _seed(conversation, u1=ada)
        start_revision = group_state.load_state(conversation).state_revision
        ready = threading.Barrier(2)

        def drain(field: str) -> None:
            ready.wait()
            for _ in range(8):
                def apply(ctx: state_transaction.TxContext, field: str = field) -> None:
                    character = ctx.state.characters["u1"]
                    setattr(character, field, getattr(character, field) - 1)
                state_transaction.mutate(conversation, apply, reason=f"test_{field}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(drain, name) for name in ("hp", "san")]:
                future.result()

        loaded = group_state.load_state(conversation)
        self.assertEqual(loaded.characters["u1"].hp, 12)
        self.assertEqual(loaded.characters["u1"].san, 52)
        self.assertEqual(loaded.state_revision, start_revision + 16)

    # S2 -------------------------------------------------------------------
    def test_separate_processes_are_serialised_by_storage(self):
        conversation = _conversation()
        _seed(conversation)
        start_revision = group_state.load_state(conversation).state_revision
        script = textwrap.dedent(
            """
            import sys
            from app.repositories import state_transaction
            conversation, count = sys.argv[1], int(sys.argv[2])
            for _ in range(count):
                def bump(ctx):
                    ctx.state.mechanical_round += 1
                result = state_transaction.mutate(conversation, bump, reason="process_test")
                assert result.outcome.value == "applied", result
            """
        )
        env = {
            **os.environ,
            "DB_PATH": str(db.DB_PATH),
            "DATA_DIR": str(db.DB_PATH.parent / "groups"),
            "BACKUP_DIR": str(db.DB_PATH.parent / "backups"),
        }
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", script, conversation, "12"],
                env=env, cwd=os.getcwd(), stderr=subprocess.PIPE, text=True,
            )
            for _ in range(3)
        ]
        failures = []
        for worker in workers:
            _, stderr = worker.communicate(timeout=120)
            if worker.returncode != 0:
                failures.append(stderr)
        self.assertEqual(failures, [])
        loaded = group_state.load_state(conversation)
        self.assertEqual(loaded.mechanical_round, 36)
        self.assertEqual(loaded.state_revision, start_revision + 36)

    # S3 / S4 --------------------------------------------------------------
    def test_same_action_submitted_twice_applies_once(self):
        conversation = _conversation()
        ada = Character(name="Ada", owner_id="u1", hp=10, hp_max=10, san=60, character_id="c1")
        _seed(conversation, u1=ada)
        gate = threading.Barrier(2)
        outcomes: list[state_transaction.TxResult] = []

        def lose_san(ctx: state_transaction.TxContext) -> int:
            ctx.state.characters["u1"].san -= 4
            ctx.stage_event("san_loss", event_id="evt-1", amount=4)
            ctx.set_result({"san_after": ctx.state.characters["u1"].san})
            return ctx.state.characters["u1"].san

        def submit() -> None:
            gate.wait()
            outcomes.append(state_transaction.mutate(
                conversation, lose_san, action_id="act-1",
                request_fingerprint=state_transaction.request_fingerprint({"san": 4}),
            ))

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(o.outcome.value for o in outcomes), ["applied", "duplicate"])
        loaded = group_state.load_state(conversation)
        self.assertEqual(loaded.characters["u1"].san, 56)
        duplicate = next(o for o in outcomes if o.outcome is Outcome.DUPLICATE)
        applied = next(o for o in outcomes if o.outcome is Outcome.APPLIED)
        self.assertEqual(duplicate.result, {"san_after": 56})
        self.assertEqual(duplicate.revision, applied.revision)
        self.assertEqual([e.event_id for e in duplicate.events], ["evt-1"])
        self.assertEqual(duplicate.original_outcome, Outcome.APPLIED)
        rows = [r for r in _ledger_rows(conversation) if r["action_id"] == "act-1"]
        self.assertEqual(len(rows), 1)

    def test_same_action_id_with_different_payload_conflicts_and_changes_nothing(self):
        conversation = _conversation()
        ada = Character(name="Ada", owner_id="u1", hp=10, hp_max=10, character_id="c1")
        _seed(conversation, u1=ada)

        def hit(amount: int):
            def apply(ctx: state_transaction.TxContext) -> None:
                ctx.state.characters["u1"].hp -= amount
            return apply

        first = state_transaction.mutate(
            conversation, hit(2), action_id="act", request_fingerprint=state_transaction.request_fingerprint({"d": 2}),
        )
        revision = group_state.load_state(conversation).state_revision
        second = state_transaction.mutate(
            conversation, hit(5), action_id="act", request_fingerprint=state_transaction.request_fingerprint({"d": 5}),
        )
        self.assertEqual(first.outcome, Outcome.APPLIED)
        self.assertEqual(second.outcome, Outcome.CONFLICT)
        self.assertEqual(second.reason, "action_payload_mismatch")
        loaded = group_state.load_state(conversation)
        self.assertEqual(loaded.characters["u1"].hp, 8)
        self.assertEqual(loaded.state_revision, revision)

    # S5 -------------------------------------------------------------------
    def test_button_from_before_a_reset_is_stale_and_runs_nothing(self):
        conversation = _conversation()
        _seed(conversation)
        old_timeline = group_state.load_state(conversation).timeline_id
        calls: list[str] = []

        def reset(ctx: state_transaction.TxContext) -> None:
            ctx.replace_state(GroupState(group_id=conversation))

        state_transaction.mutate(conversation, reset, reason="newgame")

        def roll(ctx: state_transaction.TxContext) -> None:
            calls.append("rolled")  # stands in for a dice roll and a deduction
            ctx.state.mechanical_round += 1

        outcome = state_transaction.mutate(
            conversation, roll, expected_timeline=old_timeline, action_id="old-button",
        )
        self.assertEqual(outcome.outcome, Outcome.STALE_TIMELINE)
        self.assertEqual(calls, [])
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 0)
        self.assertNotEqual(group_state.load_state(conversation).timeline_id, old_timeline)

    def test_restore_does_not_revive_an_action_recorded_before_the_reset(self):
        conversation = _conversation()
        _seed(conversation)
        timeline = group_state.load_state(conversation).timeline_id
        state_transaction.mutate(conversation, _bump_round, action_id="a", request_fingerprint="f")
        state_transaction.mutate(
            conversation, lambda ctx: ctx.replace_state(GroupState(group_id=conversation)), reason="newgame",
        )
        # Same action id in the new timeline is a new action, not a replay.
        again = state_transaction.mutate(conversation, _bump_round, action_id="a", request_fingerprint="f")
        self.assertEqual(again.outcome, Outcome.APPLIED)
        self.assertNotEqual(again.timeline_id, timeline)

    # S6 -------------------------------------------------------------------
    def test_failure_while_saving_the_action_result_rolls_back_the_state(self):
        conversation = _conversation()
        ada = Character(name="Ada", owner_id="u1", hp=10, hp_max=10, character_id="c1")
        _seed(conversation, u1=ada)
        before = _raw_row(conversation)
        real_set_json_tx = db.set_json_tx

        def failing_set_json_tx(conn, table, key, value):
            if table == "state_actions":
                raise sqlite3.OperationalError("disk I/O error injected")
            return real_set_json_tx(conn, table, key, value)

        def hit(ctx: state_transaction.TxContext) -> None:
            ctx.state.characters["u1"].hp -= 3

        with patch.object(db, "set_json_tx", failing_set_json_tx), self.assertRaises(sqlite3.OperationalError):
            state_transaction.mutate(conversation, hit, action_id="boom", request_fingerprint="x")

        self.assertEqual(_raw_row(conversation), before)
        self.assertEqual([r for r in _ledger_rows(conversation) if r["action_id"] == "boom"], [])
        retry = state_transaction.mutate(conversation, hit, action_id="boom", request_fingerprint="x")
        self.assertEqual(retry.outcome, Outcome.APPLIED)
        self.assertEqual(group_state.load_state(conversation).characters["u1"].hp, 7)

    def test_failure_while_writing_the_state_row_leaves_no_ledger_entry(self):
        conversation = _conversation()
        _seed(conversation)
        before = _raw_row(conversation)
        real_write = group_state.write_state_tx

        def failing_write(*args, **kwargs):
            real_write(*args, **kwargs)
            raise sqlite3.OperationalError("failed after the row was written")

        with patch.object(group_state, "write_state_tx", failing_write), self.assertRaises(sqlite3.OperationalError):
            state_transaction.mutate(conversation, _bump_round, action_id="late", request_fingerprint="x")

        self.assertEqual(_raw_row(conversation), before)
        self.assertEqual([r for r in _ledger_rows(conversation) if r["action_id"] == "late"], [])

    def test_hard_process_crash_in_the_middle_of_a_commit_leaves_nothing_behind(self):
        conversation = _conversation()
        _seed(conversation)
        before = _raw_row(conversation)
        script = textwrap.dedent(
            """
            import os, sys
            from app.repositories import state_transaction
            def crash(ctx):
                ctx.state.mechanical_round += 99
                ctx.conn.execute(
                    "INSERT INTO state_checkpoints (key, data) VALUES ('crash-marker', '{}')"
                )
                os._exit(7)
            state_transaction.mutate(sys.argv[1], crash, action_id="crash", request_fingerprint="x")
            """
        )
        env = {**os.environ, "DB_PATH": str(db.DB_PATH), "DATA_DIR": str(db.DB_PATH.parent / "groups"),
               "BACKUP_DIR": str(db.DB_PATH.parent / "backups")}
        completed = subprocess.run(
            [sys.executable, "-c", script, conversation], env=env, cwd=os.getcwd(), timeout=60,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 7, completed.stderr)
        self.assertEqual(_raw_row(conversation), before)
        marker = sqlite3.connect(db.DB_PATH)
        try:
            self.assertIsNone(marker.execute(
                "SELECT 1 FROM state_checkpoints WHERE key = 'crash-marker'").fetchone())
        finally:
            marker.close()
        self.assertEqual([r for r in _ledger_rows(conversation) if r["action_id"] == "crash"], [])

    # S7 -------------------------------------------------------------------
    def test_retry_after_delivery_failure_reuses_the_committed_result(self):
        conversation = _conversation()
        ada = Character(name="Ada", owner_id="u1", hp=10, hp_max=10, san=60, character_id="c1")
        _seed(conversation, u1=ada)
        rolls = iter([4, 1])  # a second roll would be a different number

        def sanity_loss(ctx: state_transaction.TxContext) -> None:
            loss = next(rolls)
            ctx.state.characters["u1"].san -= loss
            ctx.set_result({"loss": loss, "san_after": ctx.state.characters["u1"].san})

        def deliver(result: state_transaction.TxResult) -> str:
            if not hasattr(deliver, "failed"):
                deliver.failed = True  # type: ignore[attr-defined]
                raise ConnectionError("Discord send failed")
            return f"loss={result.result['loss']}"

        first = state_transaction.mutate(conversation, sanity_loss, action_id="san-1", request_fingerprint="f")
        with self.assertRaises(ConnectionError):
            deliver(first)
        retry = state_transaction.mutate(conversation, sanity_loss, action_id="san-1", request_fingerprint="f")
        self.assertEqual(retry.outcome, Outcome.DUPLICATE)
        self.assertEqual(deliver(retry), "loss=4")
        self.assertEqual(group_state.load_state(conversation).characters["u1"].san, 56)

    # S8 -------------------------------------------------------------------
    def test_late_older_refresh_does_not_roll_a_snapshot_back(self):
        conversation = _conversation()
        _seed(conversation)
        older = group_state.load_state(conversation)
        state_transaction.mutate(conversation, _bump_round)
        newer = group_state.load_state(conversation)
        snapshot = GroupState(group_id=conversation)
        self.assertTrue(state_transaction.sync_snapshot(snapshot, newer))
        self.assertFalse(state_transaction.sync_snapshot(snapshot, older))
        self.assertEqual(snapshot.state_revision, newer.state_revision)
        self.assertEqual(snapshot.mechanical_round, 1)

    def test_a_new_timeline_always_replaces_a_snapshot_even_at_a_lower_revision(self):
        conversation = _conversation()
        _seed(conversation)
        for _ in range(3):
            state_transaction.mutate(conversation, _bump_round)
        snapshot = group_state.load_state(conversation)
        state_transaction.mutate(
            conversation, lambda ctx: ctx.replace_state(GroupState(group_id=conversation)), reason="newgame",
        )
        reset = group_state.load_state(conversation)
        self.assertLess(reset.state_revision, snapshot.state_revision)
        self.assertTrue(state_transaction.sync_snapshot(snapshot, reset))
        self.assertEqual(snapshot.timeline_id, reset.timeline_id)

    # S9 -------------------------------------------------------------------
    def test_failing_or_aborted_mutation_leaves_state_snapshot_and_lock_clean(self):
        conversation = _conversation()
        ada = Character(name="Ada", owner_id="u1", hp=10, hp_max=10, character_id="c1")
        seeded = _seed(conversation, u1=ada)
        snapshot = group_state.load_state(conversation)
        before = _raw_row(conversation)

        class Abort(BaseException):
            pass

        def explode(ctx: state_transaction.TxContext) -> None:
            ctx.state.characters["u1"].hp = 1
            raise ValueError("domain validation failed")

        def abort(ctx: state_transaction.TxContext) -> None:
            ctx.state.characters["u1"].hp = 2
            raise Abort()

        with self.assertRaises(ValueError):
            state_transaction.run_snapshot(snapshot, explode)
        with self.assertRaises(Abort):
            state_transaction.mutate(conversation, abort)

        self.assertEqual(_raw_row(conversation), before)
        self.assertEqual(snapshot.characters["u1"].hp, 10)
        self.assertEqual(seeded.characters["u1"].hp, 10)
        lock = locks.get_state_lock(conversation)
        self.assertTrue(lock.acquire(timeout=1), "state lock leaked after an aborted mutation")
        lock.release()
        self.assertEqual(state_transaction.mutate(conversation, _bump_round).outcome, Outcome.APPLIED)

    def test_cancelled_awaiter_does_not_leave_a_half_committed_action(self):
        conversation = _conversation()
        _seed(conversation)
        entered = threading.Event()
        release = threading.Event()

        def slow(ctx: state_transaction.TxContext) -> None:
            entered.set()
            release.wait(timeout=10)
            ctx.state.mechanical_round += 1

        async def scenario() -> None:
            task = asyncio.create_task(state_transaction.amutate(
                conversation, slow, action_id="slow", request_fingerprint="f",
            ))
            while not entered.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()

        asyncio.run(scenario())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and group_state.load_state(conversation).mechanical_round != 1:
            time.sleep(0.05)
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 1)
        retry = state_transaction.mutate(conversation, _bump_round, action_id="slow", request_fingerprint="f")
        self.assertEqual(retry.outcome, Outcome.DUPLICATE)
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 1)

    # S10 ------------------------------------------------------------------
    def test_conversations_do_not_share_a_process_lock_or_leak_into_each_other(self):
        first, second = _conversation(), _conversation()
        _seed(first)
        _seed(second)
        self.assertIsNot(locks.get_state_lock(first), locks.get_state_lock(second))

        held = locks.get_state_lock(first)
        held.acquire()
        try:
            done: list[state_transaction.TxResult] = []
            worker = threading.Thread(
                target=lambda: done.append(state_transaction.mutate(second, _bump_round))
            )
            worker.start()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive(), "another conversation's lock blocked this one")
        finally:
            held.release()
        self.assertEqual(done[0].outcome, Outcome.APPLIED)


        def count(conversation_id: str, times: int) -> None:
            for _ in range(times):
                state_transaction.mutate(conversation_id, _bump_round)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(count, first, 6), pool.submit(count, second, 9)]:
                future.result()
        self.assertEqual(group_state.load_state(first).mechanical_round, 6)
        self.assertEqual(group_state.load_state(second).mechanical_round, 1 + 9)

    # snapshot actions ------------------------------------------------------
    def test_expected_revision_conflict_never_writes_a_stale_snapshot_back(self):
        conversation = _conversation()
        _seed(conversation)
        snapshot = group_state.load_state(conversation)
        state_transaction.mutate(conversation, _bump_round)  # someone else wrote first

        wrote: list[str] = []

        def from_snapshot(ctx: state_transaction.TxContext) -> None:
            wrote.append("ran")
            ctx.state.mechanical_round = 100

        outcome = state_transaction.mutate(
            conversation, from_snapshot, expected_revision=snapshot.state_revision,
        )
        self.assertEqual(outcome.outcome, Outcome.CONFLICT)
        self.assertTrue(outcome.retryable)
        self.assertEqual(wrote, [])
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 1)

    def test_duplicate_is_answered_before_a_revision_conflict(self):
        conversation = _conversation()
        _seed(conversation)
        revision = group_state.load_state(conversation).state_revision
        first = state_transaction.mutate(
            conversation, _bump_round, action_id="a", request_fingerprint="f", expected_revision=revision,
        )
        again = state_transaction.mutate(
            conversation, _bump_round, action_id="a", request_fingerprint="f", expected_revision=revision,
        )
        self.assertEqual(first.outcome, Outcome.APPLIED)
        self.assertEqual(again.outcome, Outcome.DUPLICATE)
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 1)

    def test_run_snapshot_refreshes_the_caller_and_rejects_a_stale_timeline(self):
        conversation = _conversation()
        _seed(conversation)
        snapshot = group_state.load_state(conversation)
        self.assertEqual(state_transaction.run_snapshot(snapshot, _bump_round), 1)
        self.assertEqual(snapshot.mechanical_round, 1)
        self.assertEqual(snapshot.state_revision, group_state.load_state(conversation).state_revision)

        state_transaction.mutate(
            conversation, lambda ctx: ctx.replace_state(GroupState(group_id=conversation)), reason="newgame",
        )
        with self.assertRaises(mutation_admission.MutationHeld):
            state_transaction.run_snapshot(snapshot, _bump_round)

    # mutation contract -----------------------------------------------------
    def test_awaiting_input_commits_and_is_remembered_on_replay(self):
        conversation = _conversation()
        _seed(conversation)

        def request_roll(ctx: state_transaction.TxContext) -> None:
            ctx.state.pending_checks["u1"] = {"check_id": "chk-1", "skill": "偵查"}
            ctx.awaiting_input(options=["roll"])

        first = state_transaction.mutate(conversation, request_roll, action_id="ask", request_fingerprint="f")
        again = state_transaction.mutate(conversation, request_roll, action_id="ask", request_fingerprint="f")
        self.assertEqual(first.outcome, Outcome.AWAITING_INPUT)
        self.assertTrue(first.committed)
        self.assertEqual(again.outcome, Outcome.DUPLICATE)
        self.assertEqual(again.original_outcome, Outcome.AWAITING_INPUT)
        self.assertIn("u1", group_state.load_state(conversation).pending_checks)

    def test_rejection_rolls_back_everything_including_writes_through_the_connection(self):
        conversation = _conversation()
        _seed(conversation)
        before = _raw_row(conversation)
        marker = f"{conversation}:marker"

        def refuse(ctx: state_transaction.TxContext) -> None:
            ctx.state.mechanical_round = 50
            db.set_json_tx(ctx.conn, "state_checkpoints", marker, {"group_id": conversation})
            raise TxReject("not_your_turn")

        outcome = state_transaction.mutate(conversation, refuse, action_id="r", request_fingerprint="f")
        self.assertEqual(outcome.outcome, Outcome.REJECTED)
        self.assertEqual(outcome.reason, "not_your_turn")
        self.assertEqual(_raw_row(conversation), before)
        self.assertIsNone(db.get_json("state_checkpoints", marker))
        self.assertEqual([r for r in _ledger_rows(conversation) if r["action_id"] == "r"], [])

    def test_extra_tables_written_through_ctx_conn_commit_with_the_state(self):
        conversation = _conversation()
        _seed(conversation)
        marker = f"{conversation}:committed"

        def both(ctx: state_transaction.TxContext) -> None:
            ctx.state.mechanical_round += 1
            db.set_json_tx(ctx.conn, "state_checkpoints", marker, {"group_id": conversation})

        state_transaction.mutate(conversation, both)
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 1)
        self.assertEqual(db.get_json("state_checkpoints", marker), {"group_id": conversation})
        db.delete_json("state_checkpoints", marker)

    def test_skip_save_keeps_revision_and_writes_nothing(self):
        conversation = _conversation()
        _seed(conversation)
        before = _raw_row(conversation)

        def nothing_to_do(ctx: state_transaction.TxContext) -> str:
            ctx.state.mechanical_round = 77  # discarded: the mutation chose not to save
            ctx.skip_save()
            return "noop"

        outcome = state_transaction.mutate(conversation, nothing_to_do)
        self.assertEqual(outcome.value, "noop")
        self.assertFalse(outcome.committed)
        self.assertEqual(_raw_row(conversation), before)

    def test_nested_transaction_is_refused_immediately(self):
        conversation = _conversation()
        other = _conversation()
        _seed(conversation)
        _seed(other)
        started = time.monotonic()

        def nested(ctx: state_transaction.TxContext) -> None:
            ctx.state.mechanical_round = 9
            state_transaction.mutate(other, _bump_round)

        with self.assertRaises(state_transaction.NestedTransactionError):
            state_transaction.mutate(conversation, nested)
        self.assertLess(time.monotonic() - started, 2, "nested mutate must not wait on SQLite's lock")
        self.assertEqual(group_state.load_state(conversation).mechanical_round, 0)
        self.assertEqual(state_transaction.mutate(conversation, _bump_round).outcome, Outcome.APPLIED)

    def test_invariant_judges_only_the_fields_the_mutation_changed(self):
        conversation = _conversation()
        legacy = Character(name="Old", owner_id="u1", hp=15, hp_max=10, character_id="c1")  # odd old save
        _seed(conversation, u1=legacy)

        def unrelated(ctx: state_transaction.TxContext) -> None:
            ctx.state.mechanical_round += 1

        def bad(ctx: state_transaction.TxContext) -> None:
            ctx.state.characters["u1"].san = -3

        self.assertEqual(state_transaction.mutate(conversation, unrelated).outcome, Outcome.APPLIED)
        rejected = state_transaction.mutate(conversation, bad)
        self.assertEqual(rejected.outcome, Outcome.REJECTED)
        self.assertTrue(rejected.reason.startswith("invariant:san_out_of_range"))
        self.assertEqual(group_state.load_state(conversation).characters["u1"].san, 50)

    def test_ammo_invariant(self):
        conversation = _conversation()
        gunner = Character(
            name="Gun", owner_id="u1", character_id="c1", weapons={".38": {"ammo": 6, "ammo_max": 6}},
        )
        _seed(conversation, u1=gunner)

        def overdraw(ctx: state_transaction.TxContext) -> None:
            ctx.state.characters["u1"].weapons[".38"]["ammo"] = -1

        self.assertEqual(state_transaction.mutate(conversation, overdraw).outcome, Outcome.REJECTED)

    def test_staged_events_are_stored_with_their_cause(self):
        conversation = _conversation()
        _seed(conversation)

        def with_events(ctx: state_transaction.TxContext) -> None:
            ctx.stage_event("check_resolved", event_id="evt-a", causation_id="chk-1", roll=33)
            ctx.stage_event("san_loss", event_id="evt-b", causation_id="evt-a", amount=2)

        result = state_transaction.mutate(conversation, with_events, action_id="ev", request_fingerprint="f")
        self.assertEqual([(e.event_id, e.causation_id) for e in result.events], [("evt-a", "chk-1"), ("evt-b", "evt-a")])
        stored = state_transaction.recorded_action(conversation, result.timeline_id, "ev")
        assert stored is not None
        self.assertEqual([e["event_id"] for e in stored["events"]], ["evt-a", "evt-b"])

    def test_ledger_stays_bounded_per_conversation(self):
        conversation = _conversation()
        other = _conversation()
        _seed(conversation)
        _seed(other)
        state_transaction.mutate(other, _bump_round, action_id="keep-me", request_fingerprint="f")
        with patch.object(state_transaction, "ACTION_RETENTION_MAX_PER_CONVERSATION", 5), \
                patch.object(state_transaction, "_PRUNE_EVERY_N_REVISIONS", 1):
            for index in range(12):
                state_transaction.mutate(conversation, _bump_round, action_id=f"a{index}", request_fingerprint="f")
        rows = _ledger_rows(conversation)
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["action_id"] for r in rows}, {f"a{index}" for index in range(7, 12)})
        self.assertEqual(len(_ledger_rows(other)), 1)

    def test_a_conversation_without_a_row_gets_a_timeline_on_first_write(self):
        conversation = _conversation()
        result = state_transaction.mutate(conversation, _bump_round, action_id="first", request_fingerprint="f")
        self.assertEqual(result.outcome, Outcome.APPLIED)
        loaded = group_state.load_state(conversation)
        self.assertTrue(loaded.timeline_id.startswith("timeline-"))
        self.assertEqual(result.timeline_id, loaded.timeline_id)
        self.assertEqual(result.revision, 1)

    def test_held_conversation_refuses_the_write(self):
        conversation = _conversation()
        _seed(conversation)
        owner = mutation_admission.start_worker(conversation, group_state.load_state(conversation).timeline_id, "tool")
        mutation_admission.detach(owner)
        try:
            with self.assertRaises(mutation_admission.MutationHeld):
                state_transaction.mutate(conversation, _bump_round)
        finally:
            mutation_admission.settle(owner)


if __name__ == "__main__":
    unittest.main()
