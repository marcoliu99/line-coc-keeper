"""Per-conversation locks so two messages for the same conversation can't race
on data/groups/*.json.

app/state.py does a full read-modify-write (load_state -> mutate -> save_state)
with no locking of its own. Without serialization, two events for the same
conversation arriving close together can each load the same on-disk snapshot,
mutate their own in-memory copy, and save — the second save silently discards
whatever the first one wrote (e.g. an HP/SAN change from a skill check that
happened "at the same time" as another player's).

"Conversation" here is a platform-agnostic id — a LINE group/room/user, a
Discord channel, whatever an adapter in app/main.py or app/discord_bot.py
addresses one GroupState by (see app/commands.py, which namespaces these as
"line-group-...", "discord-channel-...", etc. to keep platforms from colliding).

The fix here is coarse but correct: one asyncio.Lock per conversation_id, held
for the entire duration of handling one message (from the initial load_state
through every save_state it triggers, including while awaiting a slow Keeper
LLM call). Different conversations still run fully concurrently — only messages
within the same conversation queue up behind each other.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

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


def get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    lock = _locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
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


# Dialogue batching (Discord only — see docs/dialogue_batching_design_spec.md).
# One BatchRound per conversation, tracking a batch's lifecycle independently
# of the locks above: `active` means "a round is currently being collected
# or run", covering both the actual Keeper call and the short post-call
# grace period, not just the moments the LLM call itself is in flight.
# Deliberately not implemented by polling get_conversation_lock/
# get_keeper_turn_lock's .locked() — those are awaited *after* a caller has
# already decided to proceed, so checking .locked() from outside first would
# race against whoever is about to acquire or release them. KP Assistant
# messages never join a BatchRound; they always use the pre-existing solo
# path (app/commands.py's _handle_ordinary_text_message_locked), and only
# ever touch a BatchRound to wake a player round that's mid-grace so it
# flushes before the KP turn proceeds through get_keeper_priority_gate.
MAX_BATCH_SIZE = 5  # fixed per docs/dialogue_batching_design_spec.md, not env-configurable this round


@dataclass
class QueuedMessage:
    """One player message waiting for (or included in) a batched Keeper
    turn. `resolved_location` is app/commands.py's Map/Scene Engine result
    for this specific message (see _resolve_map_action_transaction there),
    resolved at enqueue time — same contract as run_turn's own
    resolved_location parameter, just computed earlier and carried along."""
    seq: int
    user_id: str
    speaker_name: str
    text: str
    received_at: float
    resolved_location: dict | None = None


@dataclass
class BatchRound:
    pending: list[QueuedMessage] = field(default_factory=list)
    active: bool = False
    grace_wake: asyncio.Event = field(default_factory=asyncio.Event)
    _next_seq: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def join(
        self, *, user_id: str, speaker_name: str, text: str, resolved_location: dict | None
    ) -> tuple[bool, QueuedMessage]:
        """Returns (True, packet) if the caller is now the leader for this
        round — responsible for actually running it (see app/commands.py's
        _run_batch_leader_loop), in which case the packet is handed directly
        to the leader and deliberately NOT added to `pending` (the leader
        already has it; leaving it there too would make it reappear in the
        very next next_round() batch as well). Otherwise (False, packet):
        the message is appended to `pending` for the current leader to pick
        up via next_round()."""
        async with self._lock:
            self._next_seq += 1
            packet = QueuedMessage(
                seq=self._next_seq,
                user_id=user_id,
                speaker_name=speaker_name,
                text=text,
                received_at=time.monotonic(),
                resolved_location=resolved_location,
            )
            if not self.active:
                self.active = True
                return True, packet
            self.pending.append(packet)
            if len(self.pending) >= MAX_BATCH_SIZE:
                self.grace_wake.set()
            return False, packet

    async def wake_for_kp(self) -> None:
        """Called whenever a KP Assistant message arrives for this
        conversation. If a player round is currently open and has something
        queued (mid-grace, not yet handed off to the leader's next Keeper
        call), wake it immediately so that batch flushes before the KP turn
        proceeds — a player round must never keep KP waiting indefinitely.
        A no-op if nothing is queued (including conversations that never use
        batching at all, e.g. LINE, whose BatchRound simply stays empty
        forever)."""
        async with self._lock:
            if self.pending:
                self.grace_wake.set()

    async def next_round(self, max_wait_seconds: float) -> list[QueuedMessage] | None:
        """Leader-only: call after finishing a round's Keeper turn. Returns
        the next batch to run, or None if the round is over (`active`
        becomes False as soon as this returns None). If something is already
        queued, waits up to `max_wait_seconds` — woken early by MAX_BATCH_SIZE
        or wake_for_kp() — before taking a fresh snapshot of `pending`, so a
        message that arrives exactly as the wait ends still has a chance to
        be captured before the snapshot; anything after that snapshot starts
        an entirely new next_round() wait rather than being lost."""
        async with self._lock:
            if not self.pending:
                self.active = False
                return None
            wake_event = self.grace_wake

        try:
            await asyncio.wait_for(wake_event.wait(), timeout=max_wait_seconds)
        except asyncio.TimeoutError:
            pass

        async with self._lock:
            # Cap what one round actually takes at MAX_BATCH_SIZE: join()'s
            # own len(pending) >= MAX_BATCH_SIZE check only *requests* an
            # early wake (sets grace_wake) — it doesn't stop more callers
            # from acquiring this same lock and appending before the leader
            # gets back here to drain. Without this slice, a fast-arriving
            # burst could hand one Keeper call an unbounded number of
            # messages, defeating the whole point of the cap.
            batch = self.pending[:MAX_BATCH_SIZE]
            self.pending = self.pending[MAX_BATCH_SIZE:]
            self.grace_wake = asyncio.Event()
            if self.pending:
                # Already at/over the cap before this round even started —
                # the next round should pick it up immediately, not sit
                # through another grace wait on top of however long this
                # remainder already queued.
                self.grace_wake.set()
            if not batch:
                self.active = False
                return None
            return batch


_batch_rounds: dict[str, BatchRound] = {}


def get_batch_round(conversation_id: str) -> BatchRound:
    round_ = _batch_rounds.get(conversation_id)
    if round_ is None:
        round_ = BatchRound()
        _batch_rounds[conversation_id] = round_
    return round_


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
