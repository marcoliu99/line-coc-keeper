"""Sending to Discord: message chunking, direct messages, interaction replies and their request metrics.

Split out of app/discord_bot.py unchanged. Every send goes through ``discord_operation`` so a Discord call that hangs is
bounded by ``DISCORD_REQUEST_TIMEOUT_SECONDS`` and counted in the request metrics.
"""
from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable
from typing import TypeVar

import discord

from app import (
    config,
    observability,
)
from app.commands.types import Reply, SendImage
from app.config import (
    LOG_SLOW_OPERATION_MS,
)
from app.discord_transport import gateway, help_ui, interactions
from app.repositories.group_state import load_state as load_group_state
from app.scenario_source_authoring import SourceReadyMessage

_logger = logging.getLogger(__name__)


_T = TypeVar("_T")


MAX_DISCORD_MESSAGE_CHARS = 1900  # Discord's hard limit is 2000; leave a margin


MAX_REPLY_MESSAGES = 10


_REPLY_METRIC_KEYS = (
    "reply_message_count", "reply_edit_count", "reply_chunk_count", "reply_bytes",
)


def _chunk_text(text: str) -> list[str]:
    text = text.strip() or "（沒有內容）"
    chunks = [text[i : i + MAX_DISCORD_MESSAGE_CHARS] for i in range(0, len(text), MAX_DISCORD_MESSAGE_CHARS)]
    return chunks[:MAX_REPLY_MESSAGES]


def _mark_reply_metrics(**touched: int) -> None:
    """Record one reply operation's metrics, zero-filling every
    _REPLY_METRIC_KEYS entry this call didn't touch.

    A caller reading metrics right after a single send/edit call — not just
    at the final per-request completion event, which backfills missing keys
    via request_metrics()'s setdefault — needs the full key set present.
    Centralized here (instead of each call site separately listing "the
    other keys") so a future reply-metric source only has to say what it
    touched, not enumerate what it didn't.
    """
    for key in _REPLY_METRIC_KEYS:
        observability.increment_metric(key, touched.get(key, 0))


def record_reply_output(text: str) -> None:
    """Account for text sent outside the shared Reply callback."""
    if not config.LOG_ENABLED:
        return
    _mark_reply_metrics(reply_message_count=1, reply_chunk_count=1, reply_bytes=len(text.encode("utf-8")))


def record_reply_binary(size: int) -> None:
    """Account for a Discord message that contains an attachment."""
    if not config.LOG_ENABLED:
        return
    _mark_reply_metrics(reply_message_count=1, reply_bytes=size)


def record_reply_edit(text: str) -> None:
    """Account for a text update to an existing Discord message."""
    if not config.LOG_ENABLED:
        return
    _mark_reply_metrics(reply_edit_count=1, reply_bytes=len(text.encode("utf-8")))


def request_metrics() -> dict[str, int]:
    """Return stable reply metric keys for request lifecycle events."""
    metrics = observability.current_metrics()
    for key in _REPLY_METRIC_KEYS:
        metrics.setdefault(key, 0)
    return metrics


def _record_sent_chunk(metrics: dict[str, int], chunk: str) -> None:
    """Add one chunk only after its Discord send has succeeded."""
    byte_count = len(chunk.encode("utf-8"))
    metrics["reply_message_count"] += 1
    metrics["reply_chunk_count"] += 1
    metrics["reply_bytes"] += byte_count
    observability.increment_metric("reply_message_count")
    observability.increment_metric("reply_chunk_count")
    observability.increment_metric("reply_bytes", byte_count)


async def discord_operation(awaitable: Awaitable[_T]) -> _T:
    """Bound one Discord API operation without retrying a possible send."""
    try:
        async with asyncio.timeout(config.DISCORD_REQUEST_TIMEOUT_SECONDS):
            return await awaitable
    except asyncio.TimeoutError:
        observability.event(
            "discord.request.timeout",
            level=logging.ERROR,
            timeout_ms=config.DISCORD_REQUEST_TIMEOUT_SECONDS * 1000,
            status="timeout",
        )
        raise


async def send_direct_message(
    channel: discord.abc.Messageable, text: str, *, view: discord.ui.View | None = None
) -> None:
    """Send a non-chunked public message with the same reply span as Reply."""
    if not config.LOG_ENABLED:
        if view is None:
            await discord_operation(channel.send(text))
        else:
            await discord_operation(channel.send(text, view=view))
        return
    byte_count = len(text.encode("utf-8"))
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=1,
        reply_bytes=byte_count,
        reply_chunk_count=1,
        reply_edit_count=0,
    ):
        if view is None:
            await discord_operation(channel.send(text))
        else:
            await discord_operation(channel.send(text, view=view))
    record_reply_output(text)


async def send_interaction_message(
    interaction: discord.Interaction, text: str, *, ephemeral: bool = False
) -> None:
    """Send an interaction response and include it in request metrics."""
    # A stale-button check normally runs after the callback has already
    # acknowledged the component with edit_interaction_view.  Discord only
    # permits one initial response, so use a follow-up in that case instead of
    # trying to send a second response and masking the useful stale-button
    # message with InteractionResponded.
    is_done = getattr(interaction.response, "is_done", None)
    response_is_done = bool(is_done()) if callable(is_done) else False
    if not config.LOG_ENABLED:
        if response_is_done:
            await discord_operation(interaction.followup.send(text, ephemeral=ephemeral))
        else:
            await discord_operation(interaction.response.send_message(text, ephemeral=ephemeral))
        return
    byte_count = len(text.encode("utf-8"))
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=1,
        reply_bytes=byte_count,
        reply_chunk_count=1,
        reply_edit_count=0,
        ephemeral=ephemeral,
    ):
        if response_is_done:
            await discord_operation(interaction.followup.send(text, ephemeral=ephemeral))
        else:
            await discord_operation(interaction.response.send_message(text, ephemeral=ephemeral))
    record_reply_output(text)


async def edit_interaction_message(
    interaction: discord.Interaction, text: str, *, view: discord.ui.View | None = None
) -> None:
    """Edit an existing interaction message without inflating message count."""
    if not config.LOG_ENABLED:
        await discord_operation(interaction.response.edit_message(content=text, view=view))
        return
    byte_count = len(text.encode("utf-8"))
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=0,
        reply_bytes=byte_count,
        reply_chunk_count=0,
        reply_edit_count=1,
    ):
        await discord_operation(interaction.response.edit_message(content=text, view=view))
    record_reply_edit(text)


async def edit_interaction_view(
    interaction: discord.Interaction, *, view: discord.ui.View | None = None
) -> None:
    """Measure an interaction edit that changes only the component view."""
    if not config.LOG_ENABLED:
        await discord_operation(interaction.response.edit_message(view=view))
        return
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=0,
        reply_bytes=0,
        reply_chunk_count=0,
        reply_edit_count=1,
    ):
        await discord_operation(interaction.response.edit_message(view=view))
    record_reply_edit("")


async def send_direct_image(
    channel: discord.abc.Messageable, png_bytes: bytes, page_number: int
) -> None:
    """Send a public attachment with Discord reply timing and byte metrics."""
    filename = f"page_{page_number}.png"
    if not config.LOG_ENABLED:
        await discord_operation(channel.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
        return
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=1,
        reply_bytes=len(png_bytes),
        reply_chunk_count=0,
        reply_edit_count=0,
        reply_kind="image",
    ):
        await discord_operation(channel.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
    record_reply_binary(len(png_bytes))


def log_reply_text(text: str) -> None:
    """Plain text log, not a structured event field — same rationale as
    app/keeper.py's search_scenario query log: the structured discord.reply
    span (in both make_reply and make_interaction_reply below) only ever
    captures counts/bytes, never what was actually said, so "what story text
    did the Keeper just post to this channel" was previously unanswerable
    from the logs.

    The LOG_TEXT_ENABLED check happens here, at the call site, rather than
    being left to app/logging_config.py's downstream _ChannelFilter:
    - Correctness: configure_logging() skips installing that filter entirely
      when both LOG_ENABLED and LOG_TEXT_ENABLED are false. A host that
      configures its own root handler before we run would still capture
      this record in full despite LOG_TEXT_ENABLED=false, defeating the
      documented opt-out for potentially sensitive story text.
    - Efficiency: even in the normal case, checking here means a disabled
      toggle costs nothing — no LogRecord built, no formatting, nothing
      queued to the background listener thread — on what is now a
      per-reply (not per-rare-search) hot path.
    """
    if config.LOG_TEXT_ENABLED:
        _logger.info("discord_reply text=%r", text)


def make_reply(channel: discord.abc.Messageable) -> Reply:
    async def send_recorded(chunk: str) -> None:
        state = None
        channel_id = getattr(channel, "id", None)
        if isinstance(channel_id, int):
            try:
                state = await asyncio.to_thread(load_group_state, interactions.channel_conversation_id(channel_id))
            except Exception:
                _logger.exception("Unable to capture correction target timeline")
        sent = await discord_operation(channel.send(chunk))
        if state is not None and isinstance(getattr(sent, "id", None), int):
            from app.services.narrative_corrections import record_message
            try:
                await asyncio.to_thread(record_message, state, str(sent.id), chunk)
            except Exception:
                _logger.exception("Unable to persist correction target receipt; message already sent")

    async def reply(text: str) -> None:
        channel_id = getattr(channel, "id", None)
        if isinstance(text, SourceReadyMessage) and isinstance(channel_id, int):
            await discord_operation(channel.send(str(text), view=help_ui.SourceReadyView(interactions.channel_conversation_id(channel_id), text)))
            return
        log_reply_text(text)
        chunks = _chunk_text(text)
        if not config.LOG_ENABLED:
            for chunk in chunks:
                await send_recorded(chunk)
            return
        reply_metrics = {
            "reply_message_count": 0,
            "reply_chunk_count": 0,
            "reply_bytes": 0,
        }
        with observability.span(
            "discord.reply",
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            metrics=reply_metrics,
        ):
            # Written to the shared _METRICS context, not passed as a literal
            # span() field — span() merges **_METRICS.get() into the log
            # event too, and a key present in both would collide (see
            # app/observability.py:span's **fields unpacking).
            observability.increment_metric("reply_edit_count", 0)
            for chunk in chunks:
                await send_recorded(chunk)
                _record_sent_chunk(reply_metrics, chunk)

    return reply


async def send_dm(owner_id: str, text: str) -> None:
    # owner_id is str(discord.Member.id), as stored on Character.owner_id. Raises
    # if the user has DMs from server members disabled; commands.py swallows
    # that (see its docstring on why it doesn't fall back to posting publicly).
    user = gateway.client.get_user(int(owner_id)) or await discord_operation(gateway.client.fetch_user(int(owner_id)))
    if user is None:
        raise RuntimeError(f"Discord user {owner_id} could not be resolved")
    for chunk in _chunk_text(text):
        if not config.LOG_ENABLED:
            await discord_operation(user.send(chunk))
            continue
        with observability.span(
            "discord.reply",
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            reply_kind="dm",
            reply_message_count=1,
            reply_chunk_count=1,
            reply_bytes=len(chunk.encode("utf-8")),
            reply_edit_count=0,
        ):
            await discord_operation(user.send(chunk))
        record_reply_output(chunk)


def make_send_image(channel: discord.abc.Messageable) -> SendImage:
    async def send_image(png_bytes: bytes, conversation_id: str, page_number: int) -> None:
        # conversation_id/page_number are retained in the shared callback
        # signature for state-aware image sends; Discord attaches bytes directly.
        await send_direct_image(channel, png_bytes, page_number)

    return send_image


async def send_dm_image(owner_id: str, png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    user = gateway.client.get_user(int(owner_id)) or await discord_operation(gateway.client.fetch_user(int(owner_id)))
    if user is None:
        raise RuntimeError(f"Discord user {owner_id} could not be resolved")
    filename = f"page_{page_number}.png"
    if not config.LOG_ENABLED:
        await discord_operation(user.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
        return
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_kind="dm_image",
        reply_message_count=1,
        reply_chunk_count=0,
        reply_bytes=len(png_bytes),
        reply_edit_count=0,
    ):
        await discord_operation(user.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
    record_reply_binary(len(png_bytes))


def make_interaction_reply(interaction: discord.Interaction) -> Reply:
    # Used only after the initial interaction response has been consumed
    # (defer/edit_message), so the actual send has to go through followup.
    async def reply(text: str) -> None:
        if isinstance(text, SourceReadyMessage) and interaction.channel is not None:
            await discord_operation(interaction.followup.send(
                str(text), ephemeral=True,
                view=help_ui.SourceReadyView(interactions.channel_conversation_id(interaction.channel.id), text)))
            return
        log_reply_text(text)
        chunks = _chunk_text(text)
        if not config.LOG_ENABLED:
            for chunk in chunks:
                await discord_operation(interaction.followup.send(chunk))
            return
        reply_metrics = {
            "reply_message_count": 0,
            "reply_chunk_count": 0,
            "reply_bytes": 0,
        }
        with observability.span(
            "discord.reply",
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            metrics=reply_metrics,
        ):
            # See make_reply's comment on why this isn't also a literal
            # span() field.
            observability.increment_metric("reply_edit_count", 0)
            for chunk in chunks:
                await discord_operation(interaction.followup.send(chunk))
                _record_sent_chunk(reply_metrics, chunk)

    return reply
