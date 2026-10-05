"""A turn that holds its locks long after its deadline is reported, never released."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app import config, locks


def events_named(spy, name):
    return [call for call in spy.call_args_list if call.args and call.args[0] == name]


class LockWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conversation = f"watchdog-{id(self)}"
        self.lock = locks.get_conversation_lock(self.conversation)
        await self.lock.acquire()

    async def test_a_turn_held_past_the_limit_is_reported_and_keeps_its_locks(self):
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.01), \
                patch.object(locks.observability, "event") as spy:
            handoff = locks.TurnHandoff(self.conversation, self.lock)
            handoff.turn_id = "turn-abc"
            await asyncio.sleep(0.1)
            reported = events_named(spy, "lock.held_too_long")
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0].kwargs["turn_id"], "turn-abc")
            self.assertEqual(reported[0].kwargs["phase"], "mutation")
            self.assertTrue(self.lock.locked(), "the watchdog must report, never release")
            handoff.close()
        self.assertFalse(self.lock.locked())
        self.assertEqual(len(events_named(spy, "lock.released_after_warning")), 1)

    async def test_a_turn_that_finishes_in_time_reports_nothing(self):
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 30.0), \
                patch.object(locks.observability, "event") as spy:
            handoff = locks.TurnHandoff(self.conversation, self.lock)
            handoff.close()
            await asyncio.sleep(0.05)
        self.assertEqual(spy.call_args_list, [])
        self.assertFalse(self.lock.locked())

    async def test_closing_cancels_the_timer(self):
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.05), \
                patch.object(locks.observability, "event") as spy:
            handoff = locks.TurnHandoff(self.conversation, self.lock)
            handoff.close()
            await asyncio.sleep(0.15)
        self.assertEqual(events_named(spy, "lock.held_too_long"), [])

    async def test_zero_turns_the_report_off(self):
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0), \
                patch.object(locks.observability, "event") as spy:
            handoff = locks.TurnHandoff(self.conversation, self.lock)
            await asyncio.sleep(0.05)
            handoff.close()
        self.assertEqual(spy.call_args_list, [])

    async def test_the_narration_phase_is_named(self):
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.01), \
                patch.object(locks.observability, "event") as spy:
            handoff = locks.TurnHandoff(self.conversation, self.lock)
            await handoff.to_narration()
            await asyncio.sleep(0.1)
            handoff.close()
        self.assertEqual(events_named(spy, "lock.held_too_long")[0].kwargs["phase"], "narration")

    async def test_a_turn_queueing_for_narration_holds_nothing_and_is_not_reported(self):
        narration = locks.get_narration_lock(self.conversation)
        await narration.acquire()  # the previous turn is still narrating
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.01), \
                patch.object(locks.observability, "event") as spy:
            handoff = locks.TurnHandoff(self.conversation, self.lock)
            waiting = asyncio.create_task(handoff.to_narration())
            await asyncio.sleep(0.1)
            self.assertEqual(events_named(spy, "lock.held_too_long"), [], "it holds no lock while it queues")
            narration.release()
            await asyncio.wait_for(waiting, 5)
            await asyncio.sleep(0.1)
            reported = events_named(spy, "lock.held_too_long")
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0].kwargs["phase"], "narration")
            handoff.close()
        self.assertFalse(narration.locked())

    async def test_a_cancelled_wait_for_narration_reports_nothing(self):
        narration = locks.get_narration_lock(self.conversation)
        await narration.acquire()
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.01), \
                patch.object(locks.observability, "event") as spy:
            handoff = locks.TurnHandoff(self.conversation, self.lock)
            waiting = asyncio.create_task(handoff.to_narration())
            await asyncio.sleep(0.1)
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            handoff.close()
            await asyncio.sleep(0.05)
        narration.release()
        self.assertEqual(spy.call_args_list, [])

    async def test_every_hold_has_its_own_turn_id(self):
        first = locks.TurnHandoff(self.conversation, self.lock)
        second = locks.TurnHandoff(self.conversation, self.lock)
        self.assertTrue(first.turn_id.startswith("turn"))
        self.assertNotEqual(first.turn_id, second.turn_id)
        first.close()

    async def test_the_default_is_the_deadline_plus_a_minute(self):
        self.assertEqual(config.LOCK_HELD_WARNING_SECONDS, config.LLM_TURN_DEADLINE_SECONDS + 60.0)


if __name__ == "__main__":
    unittest.main()
