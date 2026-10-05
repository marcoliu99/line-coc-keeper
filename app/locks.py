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
import logging
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from app import config, observability
from app.services import mutation_admission

# Legacy conversation lock: used by the current coarse-grained flow and kept
# unchanged while callers are migrated incrementally.
_locks: dict[str, _ObservableConversationLock] = {}

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
    active_task: asyncio.Task | None = None
    kp_waiters: deque[asyncio.Future[None]] = field(default_factory=deque)
    player_waiters: deque[asyncio.Future[None]] = field(default_factory=deque)
    waiter_tasks: dict[asyncio.Future[None], asyncio.Task] = field(default_factory=dict)

    def turns_ahead(self, task: asyncio.Task | None, *, is_kp: bool) -> int:
        """Current priority position, including the holder but excluding this task."""
        if task is not None and task is self.active_task:
            return 0
        own_queue = self.kp_waiters if is_kp else self.player_waiters
        own_before = 0
        for future in own_queue:
            if self.waiter_tasks.get(future) is task and task is not None:
                break
            if not future.cancelled():
                own_before += 1
        return int(self.active) + own_before + (0 if is_kp else sum(
            not future.cancelled() for future in self.kp_waiters
        ))

    async def acquire(self, *, is_kp: bool) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if not self.active and not self.kp_waiters and not self.player_waiters:
            self.active = True
            self.active_task = task
            return

        future: asyncio.Future[None] = loop.create_future()
        queue = self.kp_waiters if is_kp else self.player_waiters
        queue.append(future)
        if task is not None:
            self.waiter_tasks[future] = task
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
        finally:
            self.waiter_tasks.pop(future, None)

    def release(self) -> None:
        self.active_task = None
        while self.kp_waiters:
            future = self.kp_waiters.popleft()
            if not future.done():
                self.active_task = self.waiter_tasks.get(future)
                future.set_result(None)
                return

        while self.player_waiters:
            future = self.player_waiters.popleft()
            if not future.done():
                self.active_task = self.waiter_tasks.get(future)
                future.set_result(None)
                return

        self.active = False


_keeper_priority_gates: dict[str, _KeeperPriorityGate] = {}


def priority_gate_position(conversation_id: str, task: asyncio.Task | None, *, is_kp: bool) -> tuple[int, asyncio.Task | None]:
    gate = _keeper_priority_gates.get(conversation_id)
    if gate is None:
        return 0, None
    return gate.turns_ahead(task, is_kp=is_kp), gate.active_task


_watch_logger = logging.getLogger("app.locks")


class _HoldWatch:
    """Reports, never releases, a lock that one holder keeps far longer than any turn should.

    A conversation whose lock is never given back stays dead until the bot restarts, and until now nothing said so or
    named the holder. The timer is armed when the lock is acquired and cancelled when it is released, so it covers every
    path that takes the lock (a turn, a sudo act, a button click), and a waiter that holds nothing yet is never
    reported. It reports and does not release: a forced release would turn a stuck turn into two turns changing the
    state at once, which is worse than a stuck channel that someone can now see and name.

    The report is a plain WARNING on logger ``app.locks`` so a default deployment (``LOG_ENABLED`` off) sees it, and a
    structured event as well when that channel is on. It carries ids and times only, never player text.
    """

    def __init__(self, lock_name: str, conversation_id: str) -> None:
        self.lock_name = lock_name
        self.conversation_id = conversation_id
        self.turn_id: str | None = None
        self._ids: dict[str, str] = {}
        self._since = 0.0
        self._holder = ""
        self._warned = False
        self._timer: asyncio.TimerHandle | None = None

    def arm(self) -> None:
        threshold = config.LOCK_HELD_WARNING_SECONDS
        if threshold <= 0:
            return
        context = observability.current_context()
        self._ids = {key: context[key] for key in ("request_id", "turn_id") if key in context}
        self.turn_id = None  # a TurnHandoff names its hold after acquiring
        task = asyncio.current_task()
        self._holder = task.get_name() if task is not None else ""
        self._since = time.monotonic()
        self._warned = False
        try:
            self._timer = asyncio.get_running_loop().call_later(threshold, self._report_held)
        except RuntimeError:  # acquired outside a running loop: nothing to watch with
            self._timer = None

    def disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._warned:
            self._warned = False
            self._report("lock.released_after_warning")

    def _held_ms(self) -> int:
        return round((time.monotonic() - self._since) * 1000)

    def _report_held(self) -> None:
        self._timer = None
        self._warned = True
        self._report("lock.held_too_long")

    def _report(self, name: str) -> None:
        fields: dict[str, Any] = {
            "lock": self.lock_name, "conversation": observability.safe_identifier(self.conversation_id),
            "turn_id": self.turn_id or self._ids.get("turn_id"), "request_id": self._ids.get("request_id"),
            "holder": self._holder, "held_ms": self._held_ms(),
        }
        _watch_logger.warning("%s %s", name, " ".join(f"{key}={value}" for key, value in fields.items() if value))
        observability.event(name, level=logging.WARNING, **fields)


class _WatchedLock(asyncio.Lock):
    """An asyncio.Lock that a `_HoldWatch` watches; behaves exactly like one."""

    def __init__(self, lock_name: str, conversation_id: str) -> None:
        super().__init__()
        self.watch = _HoldWatch(lock_name, conversation_id)

    async def acquire(self) -> Literal[True]:
        await super().acquire()
        self.watch.arm()
        return True

    def release(self) -> None:
        self.watch.disarm()
        super().release()


def _label_hold(lock: asyncio.Lock, turn_id: str) -> None:
    """Name the turn that holds `lock`, when it is a watched one."""
    watch = getattr(lock, "watch", None)
    if isinstance(watch, _HoldWatch):
        watch.turn_id = turn_id


class _ObservableConversationLock(asyncio.Lock):
    """Conversation lock that measures queue wait without changing semantics.

    Two counters, both maintained here rather than read from asyncio's private
    _waiters so they stay correct if that internal changes:

    `blocked` counts callers currently inside acquire(). Read *before* a caller
    enters the queue it gives how many turns are already ahead of it, because
    asyncio.Lock hands the lock over in arrival order.

    `completed` counts turns that have finished holding the lock. A waiter
    cannot recount its own position later — a plain counter cannot tell who
    arrived before it from who arrived after — so it subtracts this counter's
    progress from its entry snapshot instead.
    """

    def __init__(self, conversation_id: str) -> None:
        super().__init__()
        self.conversation_id = conversation_id
        self.blocked = 0
        self.completed = 0
        self.holder_task: asyncio.Task | None = None
        self.waiting_tasks: set[asyncio.Task] = set()
        self.watch = _HoldWatch("conversation", conversation_id)

    def contains_task(self, task: asyncio.Task | None) -> bool:
        return task is not None and (task is self.holder_task or task in self.waiting_tasks)

    def turns_ahead(self) -> int:
        """Turns already queued: the holder plus anyone waiting.

        Only meaningful before the calling turn enters the queue itself.
        """
        return (1 + self.blocked) if self.locked() else 0

    def remaining_ahead(self, entry_ahead: int, entry_completed: int) -> int:
        """How much of an entry-time queue is left, for a waiter's own position."""
        return max(0, entry_ahead - (self.completed - entry_completed))

    async def acquire(self) -> Literal[True]:
        task = asyncio.current_task()
        self.blocked += 1
        if task is not None:
            self.waiting_tasks.add(task)
        try:
            with observability.span(
                "lock.wait",
                lock_name="conversation",
                lock_threshold_ms=config.LOG_SLOW_OPERATION_MS,
                slow_threshold_ms=config.LOG_SLOW_OPERATION_MS,
            ):
                await super().acquire()
                self.holder_task = task
                try:
                    mutation_admission.check_conversation_entry(self.conversation_id)
                except mutation_admission.MutationHeld:
                    # Released through the override, not super(), so a waiter's
                    # countdown still sees this turn leave the queue.
                    self.release()
                    raise
                self.watch.arm()
                return True
        finally:
            self.blocked -= 1
            if task is not None:
                self.waiting_tasks.discard(task)

    def release(self) -> None:
        self.completed += 1
        self.holder_task = None
        self.watch.disarm()
        super().release()


def get_conversation_lock(conversation_id: str) -> _ObservableConversationLock:
    lock = _locks.get(conversation_id)
    if lock is None:
        lock = _ObservableConversationLock(conversation_id)
        _locks[conversation_id] = lock
    return lock


def get_state_lock(conversation_id: str) -> threading.RLock:
    lock = _state_locks.get(conversation_id)
    if lock is None:
        lock = threading.RLock()
        _state_locks[conversation_id] = lock
    return lock


# Narration holds no mutation: an ordinary turn's Narrator runs with tools=[].
# Once a turn's state is committed it can hand off to here, freeing the next
# turn's Executor to start. Ordering survives because both locks are FIFO and
# the mutation lock already serialized the Executors: a turn reaches narration
# in the order it reached mutation.
_narration_locks: dict[str, asyncio.Lock] = {}


def get_narration_lock(conversation_id: str) -> asyncio.Lock:
    lock = _narration_locks.get(conversation_id)
    if lock is None:
        lock = _WatchedLock("narration", conversation_id)
        _narration_locks[conversation_id] = lock
    return lock


class TurnHandoff:
    """Moves one turn from the mutation phase to the narration phase.

    `close` is what the caller's `finally` runs, and it is exact about which
    locks this turn still holds. A conversation lock released twice raises,
    and one never released deadlocks the channel until the process restarts,
    so neither may depend on which branch the turn took.
    """

    def __init__(self, conversation_id: str, mutation_lock: asyncio.Lock) -> None:
        self.conversation_id = conversation_id
        # Every lock this turn holds for its mutation phase, in acquisition
        # order. The conversation lock alone is not enough: an ordinary text
        # turn also takes the Keeper turn lock deeper in, and a handoff that
        # left that one held would release nothing the next turn is actually
        # waiting on — it would reach the Keeper lock and block there anyway,
        # having already loaded a state snapshot that this turn's commit has
        # not landed in yet.
        self._mutation_locks: list[asyncio.Lock] = [mutation_lock]
        self._holds_narration = False
        # How long this turn waited for the lock, for the turn's phase timeline (app/services/turn_phases.py).
        self.queue_wait_ms = 0.0
        # One id for this turn's hold. The ordinary text turn adopts it for its own events, and the locks it takes carry it
        # into `lock.held_too_long`, so the report names the turn that holds them.
        self.turn_id: str = observability.new_id("turn")
        _label_hold(mutation_lock, self.turn_id)

    @property
    def narrating(self) -> bool:
        return self._holds_narration

    @asynccontextmanager
    async def mutation_phase_lock(self, lock: asyncio.Lock) -> AsyncIterator[None]:
        """Acquire `lock` as part of this turn's mutation phase.

        Released by whichever comes first: `to_narration`, or leaving this
        block. Both go through `_release_mutation_locks`, so a turn that hands
        off does not release it a second time on the way out, and one that
        never hands off still releases it exactly once.
        """
        await lock.acquire()
        self._mutation_locks.append(lock)
        _label_hold(lock, self.turn_id)
        try:
            yield
        finally:
            if lock in self._mutation_locks:
                self._mutation_locks.remove(lock)
                lock.release()

    def _release_mutation_locks(self) -> None:
        # Reverse acquisition order, so a waiter woken on the outermost lock
        # finds the inner ones already free.
        while self._mutation_locks:
            self._mutation_locks.pop().release()

    async def to_narration(self) -> None:
        """Release the mutation locks and queue for this conversation's narration.

        Idempotent: a turn that already handed off, or never held a mutation
        lock, is a no-op rather than an error.
        """
        if not self._mutation_locks:
            return
        self._release_mutation_locks()
        # Cancelled while queueing leaves this turn holding neither lock, which
        # is exactly what close then sees: _holds_narration is set only after
        # the acquire returns.
        narration = get_narration_lock(self.conversation_id)
        await narration.acquire()
        _label_hold(narration, self.turn_id)
        self._holds_narration = True

    def close(self) -> None:
        self._release_mutation_locks()
        if self._holds_narration:
            self._holds_narration = False
            get_narration_lock(self.conversation_id).release()


@asynccontextmanager
async def narrating_turn(conversation_id: str) -> AsyncIterator[None]:
    """Hold the Keeper turn lock *and* this conversation's narration slot.

    For a route that narrates, commits and posts without handing anything on:
    `/coc check` and Luck follow-ups, `/coc map` moves, the `/coc start`
    opening, and sudo acts.

    The Keeper turn lock alone used to order these against an ordinary turn,
    but only by accident — the ordinary turn held it to the end. Once
    NARRATION_OUTSIDE_MUTATION_LOCK lets that turn hand its mutation locks on,
    a command route would find both free and could narrate, commit its log
    entries and post while the earlier turn was still narrating: two Narrators
    at once on one conversation, with `state.log` and the visible replies in
    the wrong order.

    Always in this order — Keeper turn lock, then narration — and a turn that
    hands off releases the first two *before* taking narration, so neither ever
    waits on the other.
    """
    # One id for the whole hold, taken before either lock so both carry it into `lock.held_too_long`. The turn that
    # runs inside (`supervisor.run_turn`) adopts it from the context rather than making another.
    turn_id = observability.current_context().get("turn_id") or observability.new_id("turn")
    keeper = get_keeper_turn_lock(conversation_id)
    async with keeper:
        _label_hold(keeper, turn_id)
        narration = get_narration_lock(conversation_id)
        await narration.acquire()
        _label_hold(narration, turn_id)
        try:
            with observability.context(turn_id=turn_id):
                yield
        finally:
            narration.release()


def get_keeper_turn_lock(conversation_id: str) -> asyncio.Lock:
    lock = _keeper_turn_locks.get(conversation_id)
    if lock is None:
        lock = _WatchedLock("keeper_turn", conversation_id)
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
# it: see app/commands/handlers/checks.py's handle_check_command/handle_luck_decision and
# app/discord_transport/controls.py's CheckButton/LuckSpendButton callbacks, which all
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
