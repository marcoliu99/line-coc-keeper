"""Delivering what a settled turn queued, then maintaining the log off the critical path.

Shared by the check follow-up, the ordinary turn and the opening. Best effort
and transport-agnostic: callers pass the reply / DM / image callbacks.
"""
from __future__ import annotations

import asyncio
import logging

from app import config, memory_maintenance, observability
from app.commands.types import Reply, SendDM, SendDMImage, SendImage
from app.repositories.group_state import load_page_image

_logger = logging.getLogger(__name__)


async def deliver_side_effects(
    conversation_id: str,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
) -> None:
    """Shared by handle_text_message and handle_check_command — delivers
    whatever the Keeper queued via send_private_info/show_scenario_image.
    Best-effort: a DM can fail if Discord members disallow server-member DMs, and we
    deliberately don't fall back to posting the content publicly, since that
    would defeat the entire point of it being private."""
    for owner_id, message in private_messages:
        try:
            await send_dm(owner_id, f"🤫（私訊）{message}")
        except Exception:
            _logger.exception("send_dm failed for owner_id=%s in conversation_id=%s", owner_id, conversation_id)

    for image_owner_id, page_number in image_requests:
        png_bytes = load_page_image(conversation_id, page_number)
        if not png_bytes:
            continue  # Keeper referenced a page with no stored image — quietly skip
        try:
            if image_owner_id:
                await send_dm_image(image_owner_id, png_bytes, conversation_id, page_number)
            else:
                await send_image(png_bytes, conversation_id, page_number)
        except Exception:
            _logger.exception(
                "send_dm_image/send_image failed for owner_id=%s in conversation_id=%s", image_owner_id, conversation_id
            )


# Background maintenance tasks (see spawn_post_turn_maintenance below) have
# to be kept referenced somewhere until they finish, or asyncio is free to
# garbage-collect a still-running Task out from under itself. This set exists
# purely to hold that reference; each task removes itself once done.
_pending_maintenance_tasks: set[asyncio.Task] = set()


def spawn_post_turn_maintenance(conversation_id: str) -> None:
    """Fires memory_maintenance.run_post_turn_maintenance as an independent background
    task instead of awaiting it inline. It used to be awaited from *inside*
    the Keeper turn lock (and, on most call paths, the coarser per-
    conversation lock too) — see run_post_turn_maintenance_after_output
    below. The player already has their reply by the time this runs; when a
    trim actually fires (roughly every MAX_LOG_TURNS*2 turns), this call
    makes a real LLM summarization request (2-5s) plus an embeddings API call
    (300-800ms) synchronously in a worker thread, which meant the *next*
    message for this conversation — even from a different player, doing
    something with nothing to do with campaign_summary or memory indexing —
    sat blocked behind that lock for however long maintenance happened to
    take.

    Detaching this from the turn-level locks is only safe because
    memory_maintenance.run_post_turn_maintenance was hardened to tolerate running fully
    unlocked around its own slow LLM/embedding calls: a per-group_id
    in-flight guard keeps two passes for the same conversation from ever
    overlapping, and its persist step re-derives what to trim from a freshly
    reloaded state.log (content-matched against the chunk it actually
    summarized) instead of blindly overwriting with a pre-computed snapshot
    — otherwise a concurrent turn's commit_turn_result landing in the gap
    while maintenance is mid-flight would have its new log entries silently
    discarded when maintenance's stale snapshot got written back. See
    memory_maintenance.py's run_post_turn_maintenance/_persist_memory_maintenance_state
    docstrings for the details; this was found and confirmed by data-loss
    reproduction during PR review, not from first-principles design."""
    task = asyncio.create_task(run_post_turn_maintenance_safely(conversation_id))
    _pending_maintenance_tasks.add(task)
    task.add_done_callback(_pending_maintenance_tasks.discard)


async def run_post_turn_maintenance_safely(conversation_id: str) -> None:
    with observability.detached_context(
        maintenance_id=observability.new_id("maintenance"),
        conversation_id=conversation_id,
    ):
        try:
            metrics: dict[str, object] = {}
            with observability.span(
                "maintenance", trigger="post_turn", metrics=metrics,
                slow_threshold_ms=config.LOG_SLOW_OPERATION_MS,
                slow_event="maintenance.slow",
            ):
                result = await asyncio.to_thread(memory_maintenance.run_post_turn_maintenance, conversation_id)
                metrics.update(result or {})
        except Exception:
            _logger.exception("post-turn maintenance failed (background) for conversation_id=%s", conversation_id)


async def run_post_turn_maintenance_after_output(
    conversation_id: str,
    reply: Reply,
    public_message: str,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
    run_maintenance: bool = True,
) -> None:
    try:
        await reply(public_message)
        await deliver_side_effects(conversation_id, send_dm, send_image, send_dm_image, private_messages, image_requests)
    finally:
        if run_maintenance:
            spawn_post_turn_maintenance(conversation_id)
