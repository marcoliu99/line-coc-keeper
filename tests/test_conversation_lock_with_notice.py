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


if __name__ == "__main__":
    unittest.main()
