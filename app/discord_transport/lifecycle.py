"""The request lifecycle around a Discord interaction callback."""
from __future__ import annotations

import functools
import logging
import time

import discord

from app import config, observability
from app.config import LOG_SLOW_REQUEST_MS
from app.discord_transport import delivery, interactions
from app.services import mutation_admission

_logger = logging.getLogger(__name__)


def observed_interaction(callback):
    """Give persistent Discord buttons the same request lifecycle as messages."""
    @functools.wraps(callback)
    async def wrapped(self, interaction: discord.Interaction):
        channel_id = getattr(interaction.channel, "id", None)
        conversation_id = interactions.channel_conversation_id(channel_id) if channel_id is not None else None
        with observability.request_context(
            conversation_id=conversation_id,
        ):
            observability.event("turn.entry", entry="button")
            observed = config.LOG_ENABLED
            started = time.perf_counter() if observed else 0.0
            if observed:
                observability.event("request.started", platform="discord", message_kind="button")
            try:
                await callback(self, interaction)
            except mutation_admission.MutationHeld:
                await delivery.send_interaction_message(interaction, mutation_admission.NOTICE, ephemeral=True)
            except Exception as exc:
                if observed:
                    observability.event(
                        "request.failed", level=logging.ERROR,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        error_type=type(exc).__name__, status="error",
                        **delivery.request_metrics(),
                    )
                raise
            else:
                if observed:
                    duration_ms = (time.perf_counter() - started) * 1000
                    observability.event(
                        "request.completed",
                        level=logging.WARNING if duration_ms >= LOG_SLOW_REQUEST_MS else logging.INFO,
                        duration_ms=duration_ms,
                        slow_threshold_ms=LOG_SLOW_REQUEST_MS,
                        status="success",
                        **delivery.request_metrics(),
                    )
    return wrapped
