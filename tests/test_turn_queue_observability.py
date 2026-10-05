"""Coverage for docs/specs/enhancement/measured_turn_latency_priorities
WP3.2 and WP3.3: queue depth and wait are reported per turn, the notice keeps
a queued player informed for the length of a p99 wait, and one index key is
built once under concurrent misses."""
import asyncio
import threading
import unittest
from unittest.mock import patch

from app import locks, scenario_rag
from app.commands import router, turn_scope


class TurnsAheadTests(unittest.TestCase):
    def test_uncontended_lock_reports_nobody_ahead(self):
        lock = locks.get_conversation_lock("conv-turns-free")
        self.assertEqual(lock.turns_ahead(), 0)

    def test_holder_and_waiters_are_both_counted(self):
        async def scenario() -> list[int]:
            lock = locks.get_conversation_lock("conv-turns-depth")
            seen: list[int] = []
            await lock.acquire()
            seen.append(lock.turns_ahead())  # holder only

            async def waiter() -> None:
                await lock.acquire()
                lock.release()

            first = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            seen.append(lock.turns_ahead())  # holder + one waiting
            second = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            seen.append(lock.turns_ahead())  # holder + two waiting
            lock.release()
            await asyncio.gather(first, second)
            seen.append(lock.turns_ahead())  # drained
            return seen

        self.assertEqual(asyncio.run(scenario()), [1, 2, 3, 0])


class TurnQueueEventTests(unittest.TestCase):
    def test_uncontended_turn_emits_no_queue_event(self):
        async def scenario() -> None:
            async def reply(_message: str) -> None:
                pass

            async with turn_scope.conversation_turn("conv-queue-free", reply):
                pass

        with patch.object(router.observability, "event") as event:
            asyncio.run(scenario())
        self.assertNotIn("turn.queue", [call.args[0] for call in event.call_args_list])

    def test_contended_turn_reports_wait_depth_route_and_role(self):
        async def scenario() -> None:
            async def reply(_message: str) -> None:
                pass

            lock = locks.get_conversation_lock("conv-queue-busy")
            await lock.acquire()

            async def queued() -> None:
                async with turn_scope.conversation_turn(
                    "conv-queue-busy", reply, route="check", speaker_role="player",
                ):
                    pass

            task = asyncio.create_task(queued())
            await asyncio.sleep(0)
            lock.release()
            await task

        with patch.object(turn_scope, "_QUEUE_ACK_DELAY_SECONDS", 30.0), \
                patch.object(router.observability, "event") as event:
            asyncio.run(scenario())
        queued_calls = [call for call in event.call_args_list if call.args[0] == "turn.queue"]
        self.assertEqual(len(queued_calls), 1)
        fields = queued_calls[0].kwargs
        self.assertEqual(fields["turns_ahead"], 1)
        self.assertEqual(fields["route"], "check")
        self.assertEqual(fields["speaker_role"], "player")
        self.assertGreaterEqual(fields["queue_wait_ms"], 0)

    def test_priority_gate_waiters_are_counted_once_with_lock_holder(self):
        async def scenario() -> list[str]:
            replies: list[str] = []
            release_holder = asyncio.Event()
            release_second = asyncio.Event()
            release_third = asyncio.Event()

            async def reply(message: str) -> None:
                replies.append(message)

            async def turn(release: asyncio.Event) -> None:
                async with turn_scope.keeper_turn(
                    "conv-gate-depth", is_kp=False, reply=reply, speaker_role="player",
                ):
                    await release.wait()

            holder = asyncio.create_task(turn(release_holder))
            while not locks.get_conversation_lock("conv-gate-depth").locked():
                await asyncio.sleep(0)
            second = asyncio.create_task(turn(release_second))
            while len(locks._keeper_priority_gates["conv-gate-depth"].player_waiters) < 1:
                await asyncio.sleep(0)
            third = asyncio.create_task(turn(release_third))
            while len(locks._keeper_priority_gates["conv-gate-depth"].player_waiters) < 2:
                await asyncio.sleep(0)
            await asyncio.sleep(0.03)
            release_holder.set()
            await asyncio.sleep(0.03)
            release_second.set()
            release_third.set()
            await asyncio.wait_for(asyncio.gather(holder, second, third), 2)
            return replies

        with patch.object(turn_scope, "_QUEUE_ACK_DELAY_SECONDS", 0.01), \
                patch.object(turn_scope, "_QUEUE_ACK_REFRESH_SECONDS", 0.01), \
                patch.object(router.observability, "event") as event:
            replies = asyncio.run(scenario())
        self.assertTrue(any("前面還有 2 個動作" in message for message in replies), replies)
        self.assertTrue(any("前面還有 1 個動作" in message for message in replies), replies)
        queue_positions = [call.kwargs["turns_ahead"] for call in event.call_args_list
                           if call.args[0] == "turn.queue"]
        self.assertIn(2, queue_positions)


class QueueNoticeTests(unittest.TestCase):
    def test_notice_carries_position_and_refreshes_while_waiting(self):
        async def scenario() -> list[str]:
            replies: list[str] = []

            async def reply(message: str) -> None:
                replies.append(message)

            # Three notices at the patched cadence, then the wait resolves.
            task = asyncio.create_task(turn_scope._delayed_queue_notice(reply, lambda: 2))
            await asyncio.sleep(0.2)
            await turn_scope._stop_queue_notice_task(task)
            return replies

        with patch.object(turn_scope, "_QUEUE_ACK_DELAY_SECONDS", 0.01), \
                patch.object(turn_scope, "_QUEUE_ACK_REFRESH_SECONDS", 0.01):
            replies = asyncio.run(scenario())
        self.assertEqual(len(replies), turn_scope._QUEUE_ACK_MAX_NOTICES)
        for message in replies:
            self.assertIn("2", message)

    def test_a_waiter_counts_down_instead_of_recounting_the_queue(self):
        """Recounting after joining the queue would include the waiter itself
        and anyone who arrived behind it, so position must come from the entry
        snapshot minus completed turns."""
        async def scenario() -> list[int]:
            lock = locks.get_conversation_lock("conv-countdown")
            await lock.acquire()

            async def other() -> None:
                await lock.acquire()
                lock.release()

            # Two turns ahead, then one more arriving behind us.
            second = asyncio.create_task(other())
            await asyncio.sleep(0)
            entry_ahead, entry_completed = lock.turns_ahead(), lock.completed
            behind = asyncio.create_task(other())
            await asyncio.sleep(0)

            seen = [lock.remaining_ahead(entry_ahead, entry_completed)]
            lock.release()
            await second
            seen.append(lock.remaining_ahead(entry_ahead, entry_completed))
            await behind
            return seen

        # Entry saw holder + one waiter; the turn arriving behind must not
        # inflate it, and each completed turn must remove exactly one.
        self.assertEqual(asyncio.run(scenario()), [2, 0])

    def test_unknown_position_falls_back_to_the_original_message(self):
        async def scenario() -> list[str]:
            replies: list[str] = []

            async def reply(message: str) -> None:
                replies.append(message)

            task = asyncio.create_task(turn_scope._delayed_queue_notice(reply, lambda: 0))
            await asyncio.sleep(0.05)
            await turn_scope._stop_queue_notice_task(task)
            return replies

        with patch.object(turn_scope, "_QUEUE_ACK_DELAY_SECONDS", 0.01), \
                patch.object(turn_scope, "_QUEUE_ACK_REFRESH_SECONDS", 10.0):
            replies = asyncio.run(scenario())
        self.assertEqual(replies[:1], [turn_scope._QUEUE_ACK_MESSAGE])

    def test_a_failing_notice_stops_rather_than_retrying_forever(self):
        async def scenario() -> int:
            attempts = 0

            async def reply(_message: str) -> None:
                nonlocal attempts
                attempts += 1
                raise RuntimeError("discord down")

            task = asyncio.create_task(turn_scope._delayed_queue_notice(reply, lambda: 1))
            await asyncio.sleep(0.1)
            await turn_scope._stop_queue_notice_task(task)
            return attempts

        with patch.object(turn_scope, "_QUEUE_ACK_DELAY_SECONDS", 0.01), \
                patch.object(turn_scope, "_QUEUE_ACK_REFRESH_SECONDS", 0.01), \
                patch.object(router.observability, "event"):
            self.assertEqual(asyncio.run(scenario()), 1)


class IndexSingleflightTests(unittest.TestCase):
    def setUp(self):
        scenario_rag._index_cache.clear()
        scenario_rag._build_locks.clear()

    def tearDown(self):
        scenario_rag._index_cache.clear()
        scenario_rag._build_locks.clear()

    def test_concurrent_misses_build_and_persist_once(self):
        builds: list[str] = []
        saves: list[str] = []
        release = threading.Event()

        def slow_build(text: str):
            builds.append(text)
            release.wait(2)  # hold the first builder inside the lock
            return scenario_rag.ScenarioIndex(
                chunks=[], doc_freq={}, avg_length=0.0,
                text_hash=scenario_rag.hashlib.md5(text.encode(), usedforsecurity=False).hexdigest(),
                has_embeddings=False,
            )

        with patch.object(scenario_rag, "build_index", side_effect=slow_build), \
                patch.object(scenario_rag, "_load_index_from_disk", return_value=None), \
                patch.object(scenario_rag, "_save_index_to_disk",
                             side_effect=lambda key, _index: saves.append(key)):
            threads = [threading.Thread(target=scenario_rag.get_index, args=("g", "scenario text"))
                       for _ in range(4)]
            for thread in threads:
                thread.start()
            threading.Timer(0.05, release.set).start()
            for thread in threads:
                thread.join(3)

        # Without the per-key build lock each thread would embed and write.
        self.assertEqual(len(builds), 1)
        self.assertEqual(saves, ["g"])

    def test_separate_keys_do_not_serialize_behind_each_other(self):
        self.assertIsNot(scenario_rag._build_lock("a"), scenario_rag._build_lock("b"))
        self.assertIs(scenario_rag._build_lock("a"), scenario_rag._build_lock("a"))


if __name__ == "__main__":
    unittest.main()
