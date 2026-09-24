"""Regression test for the typing-indicator fix (docs/specs/enhancement-
conversation-lock-and-tool-loop-latency.md, item 1): on_message must enter
message.channel.typing() around the whole _handle_message call, started
before any lock acquisition, so a queued message behind a long-running
Keeper turn doesn't look identical to the bot being dead for tens of
seconds."""
import asyncio
import unittest
from unittest.mock import MagicMock, patch


class _FakeTypingContextManager:
    """Minimal async context manager standing in for discord.py's real
    Typing object — records whether it was actually entered/exited."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> None:
        self.entered = True

    async def __aexit__(self, *exc_info: object) -> None:
        self.exited = True


class OnMessageTypingIndicatorTests(unittest.TestCase):
    def _fake_message(self) -> MagicMock:
        message = MagicMock()
        message.author.bot = False
        message.content = "hello"
        message.attachments = []
        message.channel.id = 12345
        return message

    def test_typing_is_entered_and_exited_around_handle_message(self):
        from app import discord_bot

        message = self._fake_message()
        typing_cm = _FakeTypingContextManager()
        message.channel.typing = MagicMock(return_value=typing_cm)

        handled_while_typing: list[bool] = []

        async def fake_handle_message(_message):
            handled_while_typing.append(typing_cm.entered)

        with patch.object(discord_bot, "_handle_message", fake_handle_message), \
             patch.object(discord_bot.config, "LOG_ENABLED", False):
            asyncio.run(discord_bot.on_message(message))

        message.channel.typing.assert_called_once()
        self.assertEqual(handled_while_typing, [True])
        self.assertTrue(typing_cm.exited)

    def test_typing_still_exits_when_handle_message_raises(self):
        from app import discord_bot

        message = self._fake_message()
        typing_cm = _FakeTypingContextManager()
        message.channel.typing = MagicMock(return_value=typing_cm)

        async def failing_handle_message(_message):
            raise RuntimeError("boom")

        with patch.object(discord_bot, "_handle_message", failing_handle_message), \
             patch.object(discord_bot.config, "LOG_ENABLED", False), \
             self.assertRaises(RuntimeError):
            asyncio.run(discord_bot.on_message(message))

        self.assertTrue(typing_cm.entered)
        self.assertTrue(typing_cm.exited)

    def test_handle_message_still_runs_when_entering_typing_fails(self):
        # Review finding: async with message.channel.typing() made
        # Typing.__aenter__'s real Discord API call (send_typing) a hard
        # prerequisite for processing any message at all - a transient
        # failure on that call alone (rate limit, network hiccup) used to
        # mean the message got silently dropped with no reply, an entirely
        # new single point of failure. _best_effort_typing must let
        # _handle_message run regardless.
        from app import discord_bot

        message = self._fake_message()

        class _FailingTyping:
            async def __aenter__(self) -> None:
                raise RuntimeError("Discord typing API failed")

            async def __aexit__(self, *exc_info: object) -> bool:
                return False  # pragma: no cover - __aenter__ raises first

        message.channel.typing = MagicMock(return_value=_FailingTyping())

        handled: list[bool] = []

        async def fake_handle_message(_message):
            handled.append(True)

        with patch.object(discord_bot, "_handle_message", fake_handle_message), \
             patch.object(discord_bot.config, "LOG_ENABLED", False):
            asyncio.run(discord_bot.on_message(message))

        self.assertEqual(handled, [True])

    def test_handle_message_still_runs_when_exiting_typing_fails(self):
        from app import discord_bot

        message = self._fake_message()

        class _FailingExitTyping:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *exc_info: object) -> bool:
                raise RuntimeError("Discord typing API failed on exit")

        message.channel.typing = MagicMock(return_value=_FailingExitTyping())

        handled: list[bool] = []

        async def fake_handle_message(_message):
            handled.append(True)

        with patch.object(discord_bot, "_handle_message", fake_handle_message), \
             patch.object(discord_bot.config, "LOG_ENABLED", False):
            asyncio.run(discord_bot.on_message(message))

        self.assertEqual(handled, [True])


if __name__ == "__main__":
    unittest.main()
