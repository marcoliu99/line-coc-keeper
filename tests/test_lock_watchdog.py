"""A lock held far past a turn's deadline is reported, never released, for every path that takes it."""
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

    async def test_a_direct_holder_past_the_limit_is_reported_and_keeps_its_lock(self):
        """A sudo act or a button click takes the lock without a TurnHandoff; it is watched all the same."""
        lock = locks.get_conversation_lock(self.conversation)
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.01), \
                patch.object(locks.observability, "event") as spy, \
                self.assertLogs("app.locks", "WARNING") as logged:
            async with lock:
                await asyncio.sleep(0.1)
                self.assertTrue(lock.locked(), "the watchdog must report, never release")
            reported = events_named(spy, "lock.held_too_long")
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0].kwargs["lock"], "conversation")
            self.assertEqual(len(events_named(spy, "lock.released_after_warning")), 1)
        self.assertTrue(any("lock.held_too_long" in line and "lock=conversation" in line for line in logged.output))
        self.assertFalse(lock.locked())

    async def test_the_report_reaches_the_default_text_log_without_log_enabled(self):
        """LOG_ENABLED is off by default and makes observability.event a no-op; the warning must not depend on it."""
        with patch.object(config, "LOG_ENABLED", False), patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.01), \
                self.assertLogs("app.locks", "WARNING") as logged:
            async with locks.get_keeper_turn_lock(self.conversation):
                await asyncio.sleep(0.1)
        self.assertTrue(any("lock.held_too_long" in line and "lock=keeper_turn" in line for line in logged.output))

    async def test_a_hold_that_ends_in_time_reports_nothing(self):
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 30.0), \
                patch.object(locks.observability, "event") as spy, \
                self.assertNoLogs("app.locks", "WARNING"):
            async with locks.get_conversation_lock(self.conversation):
                pass
            await asyncio.sleep(0.05)
        self.assertEqual(spy.call_args_list, [])

    async def test_zero_turns_the_report_off(self):
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0), \
                patch.object(locks.observability, "event") as spy:
            async with locks.get_narration_lock(self.conversation):
                await asyncio.sleep(0.05)
        self.assertEqual(spy.call_args_list, [])

    async def test_a_waiter_holds_nothing_and_is_not_reported(self):
        """The turn queueing for narration is the previous holder's to report; a cancelled wait reports nothing."""
        narration = locks.get_narration_lock(self.conversation)
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.05), \
                patch.object(locks.observability, "event") as spy:
            await narration.acquire()
            waiter = asyncio.create_task(narration.acquire())
            await asyncio.sleep(0.15)
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            narration.release()
        reported = events_named(spy, "lock.held_too_long")
        self.assertEqual(len(reported), 1, "only the holder is reported, once")
        self.assertEqual(reported[0].kwargs["lock"], "narration")

    async def test_the_hold_is_named_after_the_turn_that_takes_it(self):
        lock = locks.get_conversation_lock(self.conversation)
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.01), \
                patch.object(locks.observability, "event") as spy:
            await lock.acquire()
            handoff = locks.TurnHandoff(self.conversation, lock)
            await asyncio.sleep(0.1)
            handoff.close()
        reported = events_named(spy, "lock.held_too_long")
        self.assertEqual(reported[0].kwargs["turn_id"], handoff.turn_id)

    async def test_a_handed_off_turn_is_reported_on_the_lock_it_still_holds(self):
        lock = locks.get_conversation_lock(self.conversation)
        with patch.object(config, "LOCK_HELD_WARNING_SECONDS", 0.05), \
                patch.object(locks.observability, "event") as spy:
            await lock.acquire()
            handoff = locks.TurnHandoff(self.conversation, lock)
            await handoff.to_narration()  # frees the conversation lock before the timer fires
            await asyncio.sleep(0.15)
            handoff.close()
        reported = events_named(spy, "lock.held_too_long")
        self.assertEqual([call.kwargs["lock"] for call in reported], ["narration"])
        self.assertEqual(reported[0].kwargs["turn_id"], handoff.turn_id)

    async def test_every_hold_has_its_own_turn_id(self):
        lock = locks.get_conversation_lock(self.conversation)
        await lock.acquire()
        first = locks.TurnHandoff(self.conversation, lock)
        second = locks.TurnHandoff(self.conversation, lock)
        self.assertTrue(first.turn_id.startswith("turn"))
        self.assertNotEqual(first.turn_id, second.turn_id)
        first.close()

    async def test_the_default_is_the_deadline_plus_a_minute(self):
        self.assertEqual(config.LOCK_HELD_WARNING_SECONDS, config.LLM_TURN_DEADLINE_SECONDS + 60.0)


if __name__ == "__main__":
    unittest.main()
