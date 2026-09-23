import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import discord_bot


class FakeChannel:
    def __init__(self) -> None:
        self.send = AsyncMock()


class DiscordReplyTextLogTests(unittest.TestCase):
    """Structured discord.reply metrics only ever capture counts/bytes, never
    what was actually said — "what story text did the Keeper just post to
    this channel" was previously unanswerable from the logs. This plain
    _logger.info call (gated by LOG_TEXT_ENABLED, same pattern as
    app/keeper.py's search_scenario query log) fixes that."""

    def test_logs_full_reply_text_when_log_enabled(self):
        channel = FakeChannel()
        reply = discord_bot._make_reply(channel)
        with patch.object(discord_bot.config, "LOG_ENABLED", True), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply("你走進了圖書館，燈光昏暗。"))
        mock_logger.info.assert_called_once_with(
            "discord_reply text=%r", "你走進了圖書館，燈光昏暗。"
        )

    def test_logs_full_reply_text_when_log_disabled(self):
        """LOG_ENABLED only governs the structured metrics span — the plain
        text log is independent and must still fire (gated by
        LOG_TEXT_ENABLED at the logging-config level, not by LOG_ENABLED)."""
        channel = FakeChannel()
        reply = discord_bot._make_reply(channel)
        with patch.object(discord_bot.config, "LOG_ENABLED", False), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply("這段文字即使 LOG_ENABLED 關閉也該被記錄。"))
        mock_logger.info.assert_called_once_with(
            "discord_reply text=%r", "這段文字即使 LOG_ENABLED 關閉也該被記錄。"
        )

    def test_logs_original_text_not_individual_chunks(self):
        """Logs the pre-chunking text once, not once per Discord-length chunk
        — chunking is a Discord API constraint, not a meaningful semantic
        boundary for "what did the Keeper say this turn"."""
        channel = FakeChannel()
        reply = discord_bot._make_reply(channel)
        long_text = "字" * (discord_bot.MAX_DISCORD_MESSAGE_CHARS + 100)
        with patch.object(discord_bot.config, "LOG_ENABLED", True), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply(long_text))
        mock_logger.info.assert_called_once_with("discord_reply text=%r", long_text)
        self.assertGreater(channel.send.await_count, 1)


if __name__ == "__main__":
    unittest.main()
