import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import discord_bot


class FakeChannel:
    def __init__(self) -> None:
        self.send = AsyncMock()


class FakeInteraction:
    """Minimal stand-in for discord.Interaction — _make_interaction_reply
    only ever touches .followup.send."""

    def __init__(self) -> None:
        self.followup = type("FakeFollowup", (), {"send": AsyncMock()})()


class DiscordReplyTextLogTests(unittest.TestCase):
    """Structured discord.reply metrics only ever capture counts/bytes, never
    what was actually said — "what story text did the Keeper just post to
    this channel" was previously unanswerable from the logs. _log_reply_text()
    (gated by LOG_TEXT_ENABLED, same pattern as app/keeper.py's
    search_scenario query log) fixes that, shared by both _make_reply
    (public-channel messages) and _make_interaction_reply (button/interaction
    followups — check and Luck-roll buttons post narrated outcomes through
    this path, which a first pass at this feature missed since it's a
    separate, near-identical twin of _make_reply)."""

    def test_make_reply_logs_full_text_when_log_text_enabled(self):
        channel = FakeChannel()
        reply = discord_bot._make_reply(channel)
        with patch.object(discord_bot.config, "LOG_TEXT_ENABLED", True), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply("你走進了圖書館，燈光昏暗。"))
        mock_logger.info.assert_called_once_with(
            "discord_reply text=%r", "你走進了圖書館，燈光昏暗。"
        )

    def test_make_reply_skips_logging_when_log_text_disabled(self):
        """Security fix: the check must happen at the call site, not be left
        to logging_config's downstream _ChannelFilter — a host that installs
        its own root handler before configure_logging() runs would otherwise
        still capture this record in full despite LOG_TEXT_ENABLED=false,
        and even under normal setup a disabled toggle should cost nothing
        (no LogRecord built or queued) on this now-per-reply hot path."""
        channel = FakeChannel()
        reply = discord_bot._make_reply(channel)
        with patch.object(discord_bot.config, "LOG_TEXT_ENABLED", False), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply("這段文字在 LOG_TEXT_ENABLED=false 時絕對不能被記錄。"))
        mock_logger.info.assert_not_called()

    def test_make_reply_logging_independent_of_log_enabled(self):
        """LOG_ENABLED only governs the structured metrics span — the plain
        text log must fire regardless of its value, as long as
        LOG_TEXT_ENABLED is true."""
        channel = FakeChannel()
        reply = discord_bot._make_reply(channel)
        with patch.object(discord_bot.config, "LOG_TEXT_ENABLED", True), \
                patch.object(discord_bot.config, "LOG_ENABLED", False), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply("這段文字即使 LOG_ENABLED 關閉也該被記錄。"))
        mock_logger.info.assert_called_once_with(
            "discord_reply text=%r", "這段文字即使 LOG_ENABLED 關閉也該被記錄。"
        )

    def test_make_reply_logs_original_text_not_individual_chunks(self):
        """Logs the pre-chunking text once, not once per Discord-length chunk
        — chunking is a Discord API constraint, not a meaningful semantic
        boundary for "what did the Keeper say this turn"."""
        channel = FakeChannel()
        reply = discord_bot._make_reply(channel)
        long_text = "字" * (discord_bot.MAX_DISCORD_MESSAGE_CHARS + 100)
        with patch.object(discord_bot.config, "LOG_TEXT_ENABLED", True), \
                patch.object(discord_bot.config, "LOG_ENABLED", True), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply(long_text))
        mock_logger.info.assert_called_once_with("discord_reply text=%r", long_text)
        self.assertGreater(channel.send.await_count, 1)

    def test_make_interaction_reply_logs_full_text_when_log_text_enabled(self):
        """Coverage-gap fix: check/Luck-roll button callbacks post their
        narrated outcome through _make_interaction_reply, not _make_reply —
        this path must log the same way or "what did the Keeper say" stays
        unanswerable for every check/luck-roll turn."""
        interaction = FakeInteraction()
        reply = discord_bot._make_interaction_reply(interaction)
        with patch.object(discord_bot.config, "LOG_TEXT_ENABLED", True), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply("你擲出了一個大成功！"))
        mock_logger.info.assert_called_once_with("discord_reply text=%r", "你擲出了一個大成功！")

    def test_make_interaction_reply_skips_logging_when_log_text_disabled(self):
        interaction = FakeInteraction()
        reply = discord_bot._make_interaction_reply(interaction)
        with patch.object(discord_bot.config, "LOG_TEXT_ENABLED", False), \
                patch.object(discord_bot, "_logger") as mock_logger:
            asyncio.run(reply("這段文字在 LOG_TEXT_ENABLED=false 時絕對不能被記錄。"))
        mock_logger.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
