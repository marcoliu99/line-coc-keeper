"""One turn's hold on a conversation: queue, own the locks, hand them on, run the post-turn hook, release.

Everything the router needs to take a turn lives here, so the order in which locks are taken and given back is
written once. ``conversation_turn`` is the ordinary path; ``keeper_turn`` adds the Keeper priority gate in front of
it. Both yield a ``locks.TurnHandoff``, which is how a turn gives the mutation locks to the next player once its
state is committed. The lock order is priority gate, conversation, Keeper turn, narration; ``tests/test_lock_order.py``
records the real acquisitions and fails on a violation. Moved out of ``router`` unchanged apart from the names.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from app import locks, observability
from app.commands.types import Reply

_logger = logging.getLogger(__name__)
PostTurnHook = Callable[[], Awaitable[None]]


async def run_post_turn_hook(hook: PostTurnHook | None) -> None:
    if hook is None:
        return
    try:
        await hook()
    except Exception:
        # Button recovery remains available to the Discord caller. Never
        # replace the turn's original error or prevent lock release.
        _logger.exception("failed to claim pending buttons before releasing conversation lock")


_QUEUE_ACK_DELAY_SECONDS = 10.0
# Measured conversation-lock waits reach p99 54.7 s and max 63.3 s, so one
# notice at 10 s leaves a queued player with no signal for the rest of it.
# Refresh a bounded number of times instead of going silent.
_QUEUE_ACK_REFRESH_SECONDS = 20.0
_QUEUE_ACK_MAX_NOTICES = 3
_QUEUE_ACK_MESSAGE = "🕒 守密人正在處理上一位調查員的行動，你的動作已排入佇列，請稍候……"
_QUEUE_ACK_MESSAGE_WITH_POSITION = (
    "🕒 守密人正在處理其他調查員的行動，你前面還有 {ahead} 個動作，請稍候……"
)


async def _delayed_queue_notice(
    reply: Reply, turns_ahead: Callable[[], int] | None = None,
) -> None:
    """Acknowledge a long wait, then keep the player informed while it lasts.

    Position is re-read for each notice so it reflects the queue draining.
    A wait that resolves before the first notice sends nothing, because the
    caller cancels this task on acquire.
    """
    delay = _QUEUE_ACK_DELAY_SECONDS
    for _ in range(_QUEUE_ACK_MAX_NOTICES):
        await asyncio.sleep(delay)
        delay = _QUEUE_ACK_REFRESH_SECONDS
        ahead = turns_ahead() if turns_ahead is not None else 0
        message = (
            _QUEUE_ACK_MESSAGE_WITH_POSITION.format(ahead=ahead) if ahead > 0
            else _QUEUE_ACK_MESSAGE
        )
        try:
            await reply(message)
        except Exception:  # noqa: BLE001 - best-effort UX hint, must never affect whether the lock gets released
            observability.event("queue_ack.notice_failed", level=logging.WARNING)
            return


def _emit_turn_queue(started: float, turns_ahead: int, route: str, speaker_role: str) -> None:
    """Report what a player actually waited for, per turn rather than per lock.

    `lock.wait` already times the acquire; this adds who queued behind whom,
    which is what v2 UX.4's T_turn_queue and its player-starvation question
    need. Emitted only on a contended acquire, so an uncontended turn stays
    off this path entirely.
    """
    observability.event(
        "turn.queue",
        queue_wait_ms=(time.monotonic() - started) * 1000,
        turns_ahead=turns_ahead,
        route=route or "text",
        speaker_role=speaker_role or "unknown",
    )


async def _stop_queue_notice_task(notify_task: asyncio.Task[None]) -> None:
    notify_task.cancel()
    try:
        await notify_task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - notice delivery must not affect lock cleanup
        observability.event("queue_ack.notify_task_failed", level=logging.WARNING)


@asynccontextmanager
async def conversation_turn(
    conversation_id: str, reply: Reply, post_turn_hook: PostTurnHook | None = None,
    *, route: str = "text", speaker_role: str = "",
) -> AsyncIterator[locks.TurnHandoff]:
    """Acquires the per-conversation lock, but doesn't leave a queued
    message waiting in silence: if the lock is already held, a background
    task sends a queued-notice reply only if the wait is *still* ongoing
    after ~10s — cancelled the moment the real acquire succeeds. A wait
    that resolves before then never triggers a notice at all; `typing()`
    (see app/discord_bot.py's on_message) is already running for the whole
    wait regardless, so this only needs to catch genuinely long waits
    instead of adding a second signal on top of typing() for short ones.
    See docs/specs/enhancement/enhancement-conversation-lock-and-tool-loop-latency.md
    for the full design discussion (this deliberately does not narrow the
    lock itself — see that doc for why an OCC-style rewrite was rejected).

    Review finding fixed here: `_delayed_queue_notice` already swallows its
    own `reply()` failures, but this cleanup also catches *any* exception
    from awaiting the notify task (not just `CancelledError`) as a second
    line of defense — if that cleanup ever let an exception through, it
    would escape before the `lock.release()` below ever runs, permanently
    leaking a lock that *was* successfully acquired and deadlocking every
    future command in that conversation until the process restarts. The
    notify task's own outcome must never be allowed to affect whether the
    lock we already hold gets released.
    """
    lock = locks.get_conversation_lock(conversation_id)
    waited_ms = 0.0
    if not lock.locked():
        await lock.acquire()
    else:
        ahead, settled = lock.turns_ahead(), lock.completed
        notify_task = asyncio.ensure_future(_delayed_queue_notice(
            reply, lambda: lock.remaining_ahead(ahead, settled)))
        started = time.monotonic()
        try:
            await lock.acquire()
        finally:
            await _stop_queue_notice_task(notify_task)
            _emit_turn_queue(started, ahead, route, speaker_role)
            waited_ms = (time.monotonic() - started) * 1000
    handoff = locks.TurnHandoff(conversation_id, lock)
    handoff.queue_wait_ms = waited_ms
    try:
        # Yielded so a turn can hand the mutation lock on once its state is
        # committed. A caller that ignores it keeps the lock to the end, which
        # is what close() then releases.
        yield handoff
    finally:
        try:
            await run_post_turn_hook(post_turn_hook)
        finally:
            handoff.close()


@asynccontextmanager
async def keeper_turn(
    conversation_id: str, *, is_kp: bool, reply: Reply,
    post_turn_hook: PostTurnHook | None = None,
    route: str = "text", speaker_role: str = "",
) -> AsyncIterator[locks.TurnHandoff]:
    """Notify after a long wait for either Keeper scheduling gate.

    Start the timer before the priority gate because that gate serializes
    turns ahead of the conversation lock. Once the priority gate is
    acquired, acquire the conversation lock in the established order, then
    cancel the single notice task before entering the handler body.
    """
    lock = locks.get_conversation_lock(conversation_id)
    waiting_task = asyncio.current_task()

    def turns_ahead() -> int:
        gate_ahead, gate_holder = locks.priority_gate_position(
            conversation_id, waiting_task, is_kp=is_kp,
        )
        # The priority-gate holder is usually also the conversation-lock
        # holder or a waiter there. Count that turn once across both queues.
        overlap = int(gate_ahead > 0 and lock.contains_task(gate_holder))
        return gate_ahead + lock.turns_ahead() - overlap

    ahead = turns_ahead()
    notify_task = asyncio.ensure_future(_delayed_queue_notice(
        reply, turns_ahead))
    started = time.monotonic()
    try:
        async with locks.get_keeper_priority_gate(conversation_id, is_kp=is_kp):
            await lock.acquire()
            await _stop_queue_notice_task(notify_task)
            _emit_turn_queue(started, ahead, route, speaker_role)
            handoff = locks.TurnHandoff(conversation_id, lock)
            handoff.queue_wait_ms = (time.monotonic() - started) * 1000
            try:
                yield handoff
            finally:
                try:
                    await run_post_turn_hook(post_turn_hook)
                finally:
                    handoff.close()
    finally:
        if not notify_task.done():
            await _stop_queue_notice_task(notify_task)
