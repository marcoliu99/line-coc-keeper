"""Tests for the queue-ack fix (docs/specs/enhancement-conversation-lock-
and-tool-loop-latency.md, item 2): when a message arrives and the
conversation lock is already held, a queued-notice reply fires only if the
wait is still ongoing after the delay — not immediately on contention, and
not at all for a wait that resolves before the delay elapses."""
import asyncio
import unittest
from unittest.mock import patch

from app import locks
from app.commands import router


class ConversationLockWithNoticeTests(unittest.TestCase):
    def test_no_notice_when_lock_is_immediately_available(self):
        async def scenario() -> list[str]:
            replies: list[str] = []

            async def reply(message: str) -> None:
                replies.append(message)

            async with router._conversation_lock_with_notice("conv-free", reply):
                pass
            return replies

        replies = asyncio.run(scenario())
        self.assertEqual(replies, [])

    def test_notice_sent_after_delay_when_lock_is_still_contended(self):
        with patch.object(router, "_QUEUE_ACK_DELAY_SECONDS", 0.05):
            async def scenario() -> list[str]:
                replies: list[str] = []

                async def reply(message: str) -> None:
                    replies.append(message)

                lock = locks.get_conversation_lock("conv-contended")
                await lock.acquire()

                async def release_after(delay: float) -> None:
                    await asyncio.sleep(delay)
                    lock.release()

                # Releases well after the 0.05s notice delay, so the notice
                # must have already fired by the time this hands the lock
                # back.
                asyncio.ensure_future(release_after(0.2))

                async with router._conversation_lock_with_notice("conv-contended", reply):
                    pass
                return replies

            replies = asyncio.run(scenario())

        self.assertEqual(len(replies), 1)
        self.assertIn("排入佇列", replies[0])

    def test_no_notice_when_lock_frees_before_the_delay_elapses(self):
        with patch.object(router, "_QUEUE_ACK_DELAY_SECONDS", 0.2):
            async def scenario() -> list[str]:
                replies: list[str] = []

                async def reply(message: str) -> None:
                    replies.append(message)

                lock = locks.get_conversation_lock("conv-quick-release")
                await lock.acquire()

                async def release_after(delay: float) -> None:
                    await asyncio.sleep(delay)
                    lock.release()

                # Releases well before the 0.2s notice delay would fire.
                asyncio.ensure_future(release_after(0.02))

                async with router._conversation_lock_with_notice("conv-quick-release", reply):
                    pass
                return replies

            replies = asyncio.run(scenario())

        self.assertEqual(replies, [])

    def test_lock_is_actually_held_during_the_context_body(self):
        async def scenario() -> bool:
            async def reply(_message: str) -> None:
                pass

            lock = locks.get_conversation_lock("conv-held-during-body")
            async with router._conversation_lock_with_notice("conv-held-during-body", reply):
                return lock.locked()
            return False  # pragma: no cover - unreachable

        self.assertTrue(asyncio.run(scenario()))

    def test_lock_is_released_after_the_context_exits(self):
        async def scenario() -> bool:
            async def reply(_message: str) -> None:
                pass

            lock = locks.get_conversation_lock("conv-released-after")
            async with router._conversation_lock_with_notice("conv-released-after", reply):
                pass
            return lock.locked()

        self.assertFalse(asyncio.run(scenario()))

    def test_lock_is_not_leaked_when_the_queued_notice_reply_itself_fails(self):
        # Review finding: if the background notify task's reply() call
        # raises a real exception (Discord API error, not a clean
        # cancellation) before the main coroutine's lock.acquire() has
        # resolved, that exception used to escape from `await notify_task`
        # before the lock's own `finally: lock.release()` was ever reached
        # - permanently leaking a lock that HAD been successfully acquired,
        # deadlocking every future command in that conversation.
        with patch.object(router, "_QUEUE_ACK_DELAY_SECONDS", 0.02):
            async def scenario() -> bool:
                async def failing_reply(_message: str) -> None:
                    raise RuntimeError("Discord API error")

                lock = locks.get_conversation_lock("conv-notify-fails")
                await lock.acquire()

                async def release_after(delay: float) -> None:
                    await asyncio.sleep(delay)
                    lock.release()

                # Releases well after the notice delay has already fired
                # (and failed), so the cleanup below has to deal with a
                # notify_task that finished with a real exception rather
                # than a clean cancellation.
                asyncio.ensure_future(release_after(0.1))

                async with router._conversation_lock_with_notice("conv-notify-fails", failing_reply):
                    pass
                return lock.locked()

            still_locked = asyncio.run(scenario())

        self.assertFalse(still_locked)


if __name__ == "__main__":
    unittest.main()
