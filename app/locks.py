"""Per-conversation locks so two messages for the same conversation can't race
on data/groups/*.json.

app/state.py does a full read-modify-write (load_state -> mutate -> save_state)
with no locking of its own. Without serialization, two events for the same
conversation arriving close together can each load the same on-disk snapshot,
mutate their own in-memory copy, and save — the second save silently discards
whatever the first one wrote (e.g. an HP/SAN change from a skill check that
happened "at the same time" as another player's).

"Conversation" here is a Discord channel id, namespaced as
"discord-channel-..." so it cannot collide with unrelated database keys.

The fix here is coarse but correct: one asyncio.Lock per conversation_id, held
for the entire duration of handling one message (from the initial load_state
through every save_state it triggers, including while awaiting a slow Keeper
LLM call). Different conversations still run fully concurrently — only messages
within the same conversation queue up behind each other.
"""
from __future__ import annotations

import asyncio
import threading
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

from app import config, observability

# Legacy conversation lock: used by the current coarse-grained flow and kept
# unchanged while callers are migrated incrementally.
_locks: dict[str, asyncio.Lock] = {}

# State lock: future per-conversation synchronous state transactions around
# load_state -> mutate -> save_state. It is a threading.RLock so Keeper worker
# threads can use the same authoritative lock; async callers should not hold it
# across slow event-loop work.
_state_locks: dict[str, threading.RLock] = {}

# Keeper turn lock: future serialization for Keeper/LLM turns per conversation.
_keeper_turn_locks: dict[str, asyncio.Lock] = {}


@dataclass
class _KeeperPriorityGate:
    active: bool = False
    kp_waiters: deque[asyncio.Future[None]] = field(default_factory=deque)
    player_waiters: deque[asyncio.Future[None]] = field(default_factory=deque)

    async def acquire(self, *, is_kp: bool) -> None:
        loop = asyncio.get_running_loop()
        if not self.active and not self.kp_waiters and not self.player_waiters:
            self.active = True
            return

        future: asyncio.Future[None] = loop.create_future()
        queue = self.kp_waiters if is_kp else self.player_waiters
        queue.append(future)
        try:
            await future
        except BaseException:
            if future.done() and not future.cancelled():
                # This waiter had already been selected as the next holder,
                # but the task was cancelled before entering the protected
                # block. Hand the gate to the next waiter instead of leaving
                # `active` stuck forever.
                self.release()
            else:
                future.cancel()
                try:
                    queue.remove(future)
                except ValueError:
                    pass
            raise

    def release(self) -> None:
        while self.kp_waiters:
            future = self.kp_waiters.popleft()
            if not future.done():
                future.set_result(None)
                return

        while self.player_waiters:
            future = self.player_waiters.popleft()
            if not future.done():
                future.set_result(None)
                return

        self.active = False


_keeper_priority_gates: dict[str, _KeeperPriorityGate] = {}


class _ObservableConversationLock(asyncio.Lock):
    """Conversation lock that measures queue wait without changing semantics."""

    async def acquire(self) -> Literal[True]:
        with observability.span(
            "lock.wait",
            lock_name="conversation",
            lock_threshold_ms=config.LOG_SLOW_OPERATION_MS,
            slow_threshold_ms=config.LOG_SLOW_OPERATION_MS,
        ):
            await super().acquire()
            return True


def get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    lock = _locks.get(conversation_id)
    if lock is None:
        lock = _ObservableConversationLock()
        _locks[conversation_id] = lock
    return lock


def get_state_lock(conversation_id: str) -> threading.RLock:
    lock = _state_locks.get(conversation_id)
    if lock is None:
        lock = threading.RLock()
        _state_locks[conversation_id] = lock
    return lock


def get_keeper_turn_lock(conversation_id: str) -> asyncio.Lock:
    lock = _keeper_turn_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _keeper_turn_locks[conversation_id] = lock
    return lock


@asynccontextmanager
async def get_keeper_priority_gate(conversation_id: str, *, is_kp: bool) -> AsyncIterator[None]:
    """Per-conversation async gate for Keeper turn scheduling — wraps ordinary
    (non-/coc-command) text messages in app/commands.py's handle_text_message,
    but only when the conversation currently has a KP Assistant; otherwise
    that call site bypasses this gate entirely and keeps the original
    conversation-lock-only path. Separate from the conversation/state/turn
    locks above. It admits at most one holder per conversation; when the
    holder releases, queued KP Assistant turns are admitted before queued
    player turns, with FIFO order preserved inside each priority class. A
    running holder is never preempted.
    """
    gate = _keeper_priority_gates.get(conversation_id)
    if gate is None:
        gate = _KeeperPriorityGate()
        _keeper_priority_gates[conversation_id] = gate
    await gate.acquire(is_kp=is_kp)
    try:
        yield
    finally:
        gate.release()


# A resolved check/Luck-decision involves a slow Keeper (LLM) call, so a
# player's second click/resend while the first is still in flight would
# otherwise just queue behind get_conversation_lock above and run as a
# genuinely separate, duplicate roll once its turn comes — not blocked, just
# delayed. This is a non-blocking try-acquire (never awaits) checked *before*
# that lock, specifically to reject a duplicate outright instead of queuing
# it: see app/commands.py's handle_check_command/handle_luck_decision and
# app/discord_bot.py's CheckButton/LuckSpendButton callbacks, which all
# acquire this around themselves and release it in a finally block.
_in_flight_checks: set[tuple[str, str]] = set()


def try_acquire_check(conversation_id: str, user_id: str) -> bool:
    """True if acquired (caller must release_check when done); False if this
    (conversation, user) pair already has a check/Luck-decision in flight —
    caller should tell the player to wait rather than proceeding."""
    key = (conversation_id, user_id)
    if key in _in_flight_checks:
        return False
    _in_flight_checks.add(key)
    return True


def release_check(conversation_id: str, user_id: str) -> None:
    _in_flight_checks.discard((conversation_id, user_id))
