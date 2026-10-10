"""The single transaction boundary for every game-state write.

``mutate`` is the only way application code changes a conversation's
``GroupState``. In one call it

1. takes the conversation's in-process lock,
2. opens a ``BEGIN IMMEDIATE`` SQLite transaction (which also serialises other
   processes using the same database file),
3. reads the **latest** stored state inside that transaction,
4. validates the timeline and optional latest-state guard, then the action ledger and revision,
5. runs the caller's mutation against that latest copy,
6. checks the local invariants of what the mutation touched,
7. writes the state row, the character mirrors, the action result and the
   staged events through the same connection, and commits.

Nothing is committed unless all of it succeeds, so a failure while saving the
action result rolls back the state change as well. A caller that retries with
the same ``action_id`` after a commit (a lost reply, a double click, a
continuation re-run) gets the stored result back instead of a second
application.

The mutation receives a :class:`TxContext`. It must be quick and purely local:
no LLM, OCR, Discord or network call and no second transaction on the same
conversation (a nested ``mutate`` raises :class:`NestedTransactionError`
instead of waiting on SQLite's own lock). Writes to other tables that must land
atomically with the state (checkpoints, manual pregens, correction archives)
use ``ctx.conn``.

Outcomes are values, not exceptions: ``applied``, ``duplicate``,
``awaiting_input`` (committed, a player must act next), ``stale_timeline``,
``conflict`` and ``rejected``. ``awaiting_input`` is the contract for a
mutation that calls ``ctx.awaiting_input()``; the check and combat services
currently leave a wait as ordinary state (a pending check or decision, the
combat phase) and commit it as ``applied``. Exceptions raised by the mutation itself
propagate after the transaction rolls back, so domain code can keep raising
``ValueError`` for its own validation.

Deployment note: the process lock only orders callers inside one Python
process. Cross-process safety rests on ``BEGIN IMMEDIATE`` plus the revision
stored in the row, which is exercised by ``tests/test_state_transaction.py``
with real concurrent processes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import uuid4

from app import db, locks, observability
from app.models import Character, GroupState
from app.repositories import group_state
from app.services import mutation_admission
from app.storage_errors import NestedTransactionError

_logger = logging.getLogger(__name__)

T = TypeVar("T")

ACTIONS_TABLE = "state_actions"
# Dedup records must outlive anything a player can still click or resend: Discord
# components time out far sooner than this, and a terminal check/combat state
# stops a double settlement even after a record is pruned.
ACTION_RETENTION_DAYS = 30
ACTION_RETENTION_MAX_PER_CONVERSATION = 1000
_PRUNE_EVERY_N_REVISIONS = 25
_KEY_SEPARATOR = "\x1f"


class Outcome(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    AWAITING_INPUT = "awaiting_input"
    STALE_TIMELINE = "stale_timeline"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class TxReject(Exception):
    """Raised inside a mutation to refuse the action without writing anything."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason




class CorruptStateError(ValueError):
    """The stored row cannot be read as a ``GroupState``."""


class StaleTimelineError(group_state.StateRevisionConflict):
    """A snapshot from before a reset or restore tried to write."""


class StateTransactionFailed(RuntimeError):
    """``mutate_value``/``run_snapshot`` saw an outcome that is not a success."""

    def __init__(self, result: TxResult[Any]) -> None:
        super().__init__(f"state transaction {result.outcome.value}: {result.reason}")
        self.result = result


@dataclass(frozen=True)
class StagedEvent:
    """A durable fact produced by one mutation, referenced by id and cause."""

    event_id: str
    kind: str
    causation_id: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "kind": self.kind,
            "causation_id": self.causation_id, "payload": dict(self.payload),
        }

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> StagedEvent:
        return StagedEvent(
            event_id=str(raw.get("event_id", "")), kind=str(raw.get("kind", "")),
            causation_id=str(raw.get("causation_id", "")), payload=dict(raw.get("payload") or {}),
        )


@dataclass(frozen=True)
class TxResult(Generic[T]):
    outcome: Outcome
    value: T | None = None
    revision: int | None = None
    timeline_id: str = ""
    action_id: str = ""
    events: tuple[StagedEvent, ...] = ()
    reason: str = ""
    committed: bool = False
    retryable: bool = False
    # Only set on a duplicate: how the original call ended (``applied`` or
    # ``awaiting_input``), so a retry can tell a finished action from one that
    # is still waiting on a player.
    original_outcome: Outcome | None = None
    # JSON-safe facts the mutation chose to keep with the action. On a
    # duplicate this is what the original call stored, which is how a retry
    # reuses the first result instead of re-deriving it.
    result: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome in (Outcome.APPLIED, Outcome.DUPLICATE, Outcome.AWAITING_INPUT)


class TxContext:
    """What a mutation may see and do. ``state`` is a private working copy."""

    def __init__(
        self, *, conn: sqlite3.Connection, state: GroupState, conversation_id: str,
        action_id: str, timeline_id: str, reason: str = "",
    ) -> None:
        self.conn = conn
        self.state = state
        self.conversation_id = conversation_id
        self.action_id = action_id
        self.timeline_id = timeline_id
        # Why the state is being written (commit log). A mutation that only
        # learns the real reason while it runs (a check settling into a
        # combat step) may replace it before it returns.
        self.reason = reason
        self._events: list[StagedEvent] = []
        self.stored_result: dict[str, Any] = {}
        self.save_skipped = False
        self.awaiting: dict[str, Any] | None = None

    @property
    def events(self) -> tuple[StagedEvent, ...]:
        return tuple(self._events)

    def stage_event(
        self, kind: str, *, event_id: str = "", causation_id: str = "", **payload: Any,
    ) -> StagedEvent:
        event = StagedEvent(
            event_id=event_id or f"{kind}:{uuid4().hex[:12]}", kind=kind,
            causation_id=causation_id or self.action_id, payload=payload,
        )
        self._events.append(event)
        return event

    def set_result(self, payload: Mapping[str, Any]) -> None:
        """Keep JSON-safe facts with the action so a retry can replay them."""
        self.stored_result = json.loads(json.dumps(dict(payload), ensure_ascii=False, default=str))

    def skip_save(self) -> None:
        """Nothing to write: the state row, revision, mirrors and ledger stay untouched.

        Rows the mutation already wrote through ``conn`` are rolled back too, as
        for a rejection, so a skipped action leaves nothing behind.
        """
        self.save_skipped = True

    def awaiting_input(self, **details: Any) -> None:
        """Mark this commit as stopping for a player decision (not a rollback)."""
        self.awaiting = dict(details)

    def reject(self, reason: str, message: str = "") -> None:
        raise TxReject(reason, message)

    def replace_state(self, new_state: GroupState) -> None:
        """Swap the whole game state (``/coc newgame``, rollback)."""
        new_state.group_id = self.conversation_id
        self.state = new_state


# The mutation in progress for this thread/task. A mutation that opens another
# transaction would block on SQLite's write lock held by its own caller, so a
# nested ``mutate`` fails immediately; code that legitimately needs the open
# transaction (the pre-combat checkpoint) asks for it with ``ambient``.
_active: ContextVar[TxContext | None] = ContextVar("state_transaction_active", default=None)


def ambient(conversation_id: str) -> TxContext | None:
    """The transaction this call is running inside, if it is for this conversation.

    Domain helpers that must write atomically with the state they are changing
    (a checkpoint taken just before a fight) call this instead of opening their
    own transaction. A transaction for another conversation is refused: two
    conversations share SQLite's single write lock.
    """
    ctx = _active.get()
    if ctx is None:
        return None
    if ctx.conversation_id != conversation_id:
        raise NestedTransactionError(
            "a state mutation cannot touch another conversation's state"
        )
    return ctx


def effective_timeline(state: GroupState) -> str:
    return state.timeline_id or f"legacy-{state.group_id}"


def request_fingerprint(payload: Any) -> str:
    """Stable digest of an action's request payload."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _ledger_key(conversation_id: str, timeline_id: str, action_id: str) -> str:
    return _KEY_SEPARATOR.join((conversation_id, timeline_id, action_id))


def _ledger_prefix(conversation_id: str) -> str:
    return conversation_id + _KEY_SEPARATOR


# --- local invariants --------------------------------------------------------

_RESOURCE_FIELDS = ("hp", "mp", "san", "luck")


def _character_keys(state: GroupState) -> dict[str, Character]:
    keyed: dict[str, Character] = {}
    for owner_id, character in state.characters.items():
        keyed[f"owner:{owner_id}"] = character
    for character_id, character in state.characters_by_id.items():
        keyed[f"id:{character_id}"] = character
    return keyed


def _resource_snapshot(state: GroupState) -> dict[str, tuple[Any, ...]]:
    snapshot: dict[str, tuple[Any, ...]] = {}
    for key, character in _character_keys(state).items():
        snapshot[key] = (
            *(getattr(character, name) for name in _RESOURCE_FIELDS),
            tuple(sorted((name, info.get("ammo")) for name, info in character.weapons.items())),
        )
    return snapshot


def _is_whole_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _invariant_violation(before: dict[str, tuple[Any, ...]], state: GroupState) -> str:
    """Name the first rule broken by a field this mutation actually changed.

    Only changed fields are judged, so an old save with an odd value is never
    rejected for something the current action did not touch.
    """
    for key, character in _character_keys(state).items():
        previous = before.get(key)
        if previous is None:
            continue
        for index, name in enumerate(_RESOURCE_FIELDS):
            value = getattr(character, name)
            if value == previous[index]:
                continue
            if not _is_whole_number(value) or value < 0:
                return f"{name}_out_of_range:{character.name}"
            ceiling = getattr(character, f"{name}_max", 0) if name in ("hp", "mp") else 0
            if ceiling and value > ceiling:
                return f"{name}_above_max:{character.name}"
        before_ammo = dict(previous[-1])
        for weapon, info in character.weapons.items():
            ammo = info.get("ammo")
            if ammo == before_ammo.get(weapon):
                continue
            if not isinstance(ammo, int) or isinstance(ammo, bool) or ammo < 0:
                return f"ammo_out_of_range:{character.name}:{weapon}"
            capacity = info.get("ammo_max")
            if capacity and ammo > capacity:
                return f"ammo_above_max:{character.name}:{weapon}"
    for collection in (state.pending_checks, state.pending_luck_decisions):
        for owner_id, entry in collection.items():
            if not isinstance(entry, dict):
                return f"pending_not_a_mapping:{owner_id}"
    return ""


# --- action ledger -----------------------------------------------------------

def _read_action(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT data FROM state_actions WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row is not None else None


def _write_action(
    conn: sqlite3.Connection, key: str, *, conversation_id: str, timeline_id: str,
    action_id: str, fingerprint: str, outcome: Outcome, revision: int,
    events: tuple[StagedEvent, ...], result: Mapping[str, Any],
) -> None:
    db.set_json_tx(conn, ACTIONS_TABLE, key, {
        "conversation_id": conversation_id, "timeline_id": timeline_id,
        "action_id": action_id, "fingerprint": fingerprint, "outcome": outcome.value,
        "revision": revision, "events": [event.to_json() for event in events],
        "result": dict(result), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def _prune_actions(conn: sqlite3.Connection, conversation_id: str) -> None:
    """Keep the ledger bounded; terminal domain state still blocks re-settlement."""
    prefix = _ledger_prefix(conversation_id)
    width = len(prefix)
    conn.execute(
        "DELETE FROM state_actions WHERE substr(key, 1, ?) = ? "
        "AND updated_at < datetime('now', ?)",
        (width, prefix, f"-{ACTION_RETENTION_DAYS} days"),
    )
    conn.execute(
        "DELETE FROM state_actions WHERE key IN ("
        "SELECT key FROM state_actions WHERE substr(key, 1, ?) = ? "
        "ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?)",
        (width, prefix, ACTION_RETENTION_MAX_PER_CONVERSATION),
    )


def recorded_action(conversation_id: str, timeline_id: str, action_id: str) -> dict[str, Any] | None:
    """Read-only lookup of a committed action (diagnostics and tests)."""
    return db.get_json(ACTIONS_TABLE, _ledger_key(conversation_id, timeline_id, action_id))


# --- snapshots ---------------------------------------------------------------

def sync_snapshot(target: GroupState, source: GroupState) -> bool:
    """Copy ``source`` into a caller's snapshot unless that would go backwards.

    A refresh that read the row before a newer commit and is applied after it
    must not roll the shared snapshot back. A different timeline (new game,
    rollback) always wins because revisions restart there.
    """
    if (
        target.group_id == source.group_id
        and effective_timeline(target) == effective_timeline(source)
        and source.state_revision < target.state_revision
    ):
        return False
    for state_field in fields(GroupState):
        setattr(target, state_field.name, getattr(source, state_field.name))
    return True


def refresh_snapshot(state: GroupState) -> GroupState:
    """Reload the committed state into a caller's snapshot, monotonically."""
    with locks.get_state_lock(state.group_id):
        sync_snapshot(state, group_state.load_state(state.group_id))
    return state


# --- the transaction ---------------------------------------------------------

Mutation = Callable[[TxContext], T]


def _result(outcome: Outcome, **kwargs: Any) -> TxResult[Any]:
    return TxResult(outcome=outcome, **kwargs)


def mutate(
    conversation_id: str,
    mutation: Mutation[T],
    *,
    reason: str = "tool",
    expected_timeline: str | None = None,
    action_id: str | None = None,
    request_fingerprint: str | None = None,
    expected_revision: int | None = None,
    latest_state_guard: Callable[[GroupState], str | None] | None = None,
) -> TxResult[T]:
    """Apply ``mutation`` to the latest state of ``conversation_id`` atomically.

    ``expected_timeline`` rejects work that belongs to a game the group has
    since reset or restored. ``action_id`` plus ``request_fingerprint`` make a
    repeated request return the stored result (same fingerprint) or a conflict
    (different fingerprint). ``expected_revision`` is for actions computed from
    a snapshot: any other writer in between yields ``conflict`` and the stale
    snapshot is never written back. Deterministic deltas that can be recomputed
    on the latest state leave it unset. ``latest_state_guard`` runs against the
    latest row under BEGIN IMMEDIATE before action-ledger replay.
    """
    return _mutate(
        conversation_id, mutation, reason=reason, expected_timeline=expected_timeline,
        action_id=action_id, request_fingerprint=request_fingerprint,
        expected_revision=expected_revision, latest_state_guard=latest_state_guard,
    )[0]


def _mutate(
    conversation_id: str,
    mutation: Mutation[T],
    *,
    reason: str,
    expected_timeline: str | None,
    action_id: str | None,
    request_fingerprint: str | None,
    expected_revision: int | None,
    latest_state_guard: Callable[[GroupState], str | None] | None,
) -> tuple[TxResult[T], GroupState | None]:
    """``mutate`` plus the committed working copy, for snapshot adapters."""
    if _active.get() is not None:
        raise NestedTransactionError(
            "a state mutation cannot open another state transaction; "
            "share the outer TxContext (state_transaction.ambient) instead"
        )
    fingerprint = request_fingerprint or ""
    started = time.monotonic()
    committed_state: GroupState | None = None
    commit: group_state.StateCommit | None = None
    result: TxResult[T]
    metrics: dict[str, int | bool] = {}
    phase = "read"
    with observability.span("state.transaction", operation=reason, metrics=metrics):
        token = None
        try:
            with locks.get_state_lock(conversation_id), db.transaction() as conn, db.state_transaction_scope():
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT data FROM group_states WHERE key = ?", (conversation_id,)
                ).fetchone()
                try:
                    current = json.loads(row[0]) if row is not None else None
                    latest = (
                        GroupState.from_dict(current) if current is not None
                        else GroupState(group_id=conversation_id)
                    )
                except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                    raise CorruptStateError(
                        f"stored state for {observability.safe_identifier(conversation_id)} is unreadable"
                    ) from exc
                mutation_admission.assert_admitted(conversation_id, timeline_id=latest.timeline_id)
                timeline_id = effective_timeline(latest)

                if expected_timeline and expected_timeline != timeline_id:
                    return _stale_timeline(
                        reason, expected_timeline, timeline_id, action_id or "",
                    ), None
                if latest_state_guard is not None:
                    guard_reason = latest_state_guard(latest)
                    if guard_reason:
                        return _result(
                            Outcome.REJECTED, revision=latest.state_revision,
                            timeline_id=timeline_id, action_id=action_id or "",
                            reason=guard_reason,
                        ), None
                ledger_key = ""
                if action_id:
                    ledger_key = _ledger_key(conversation_id, timeline_id, action_id)
                    prior = _read_action(conn, ledger_key)
                    if prior is not None:
                        return _replay(prior, action_id, fingerprint, conversation_id, reason), None
                if expected_revision is not None and latest.state_revision != expected_revision:
                    observability.event(
                        "state.transaction.conflict", level=logging.WARNING, reason=reason,
                        expected_revision=expected_revision, current_revision=latest.state_revision,
                        group_id_hash=observability.safe_identifier(conversation_id),
                    )
                    return _result(
                        Outcome.CONFLICT, revision=latest.state_revision, timeline_id=timeline_id,
                        action_id=action_id or "", reason="revision_mismatch", retryable=True,
                    ), None

                before = _resource_snapshot(latest)
                ctx = TxContext(
                    conn=conn, state=latest, conversation_id=conversation_id,
                    action_id=action_id or "", timeline_id=timeline_id, reason=reason,
                )
                phase = "mutation"
                token = _active.set(ctx)
                try:
                    value = mutation(ctx)
                except TxReject as rejection:
                    _active.reset(token)
                    token = None
                    # The mutation may already have written through ctx.conn;
                    # a refused action leaves nothing behind.
                    conn.rollback()
                    return _result(
                        Outcome.REJECTED, revision=latest.state_revision, timeline_id=timeline_id,
                        action_id=action_id or "", reason=rejection.reason,
                    ), None

                _active.reset(token)
                token = None
                phase = "persist"
                changed_state = ctx.state
                if not ctx.save_skipped:
                    violation = _invariant_violation(before, changed_state)
                    if violation:
                        observability.event(
                            "state.transaction.invariant_rejected", level=logging.ERROR,
                            reason=reason, violation=violation.split(":", 1)[0],
                        )
                        conn.rollback()
                        return _result(
                            Outcome.REJECTED, revision=latest.state_revision, timeline_id=timeline_id,
                            action_id=action_id or "", reason=f"invariant:{violation}",
                        ), None
                    commit = group_state.write_state_tx(
                        changed_state, reason=ctx.reason or reason, conn=conn, previous=current,
                    )
                    revision = commit.revision
                    stored_timeline = commit.timeline_id
                else:
                    # An action that saved nothing leaves nothing behind: drop what
                    # it wrote through ctx.conn (a checkpoint taken for a battle
                    # that was then not saved) along with the state.
                    conn.rollback()
                    revision = latest.state_revision
                    stored_timeline = timeline_id
                outcome = Outcome.AWAITING_INPUT if ctx.awaiting is not None else Outcome.APPLIED
                events = ctx.events
                if action_id and not ctx.save_skipped:
                    # The ledger belongs to the timeline the action ran in. A
                    # reset flow moves to a new timeline, and its own id is
                    # never replayed, so it is recorded under the new one. An
                    # action that chose to save nothing had no effect, so it is
                    # not recorded and a later retry is evaluated afresh.
                    _write_action(
                        conn, _ledger_key(conversation_id, stored_timeline, action_id),
                        conversation_id=conversation_id, timeline_id=stored_timeline,
                        action_id=action_id, fingerprint=fingerprint, outcome=outcome,
                        revision=revision, events=events, result=ctx.stored_result,
                    )
                    if revision % _PRUNE_EVERY_N_REVISIONS == 0:
                        _prune_actions(conn, conversation_id)
                result = _result(
                    outcome, value=value, revision=revision, timeline_id=stored_timeline,
                    action_id=action_id or "", events=events, committed=commit is not None,
                    result=ctx.stored_result,
                )
                committed_state = changed_state
                phase = "commit"
        except Exception as exc:
            # A domain error raised by the mutation is the caller's to handle;
            # a storage failure must never be mistaken for a saved action.
            if phase != "mutation" and not isinstance(exc, mutation_admission.MutationHeld):
                observability.event(
                    "state.save.failed", level=logging.ERROR, reason=reason, phase=phase,
                    error_type=type(exc).__name__,
                    group_id_hash=observability.safe_identifier(conversation_id),
                )
            raise
        finally:
            if token is not None:
                _active.reset(token)
    if commit is not None and committed_state is not None:
        commit.apply(committed_state)
    observability.event(
        "state.transaction.completed", reason=reason, outcome=result.outcome.value,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result, committed_state


def _stale_timeline(
    reason: str, expected: str, current: str, action_id: str,
) -> TxResult[Any]:
    observability.event(
        "state.transaction.stale_timeline", level=logging.WARNING, reason=reason,
        expected_timeline_id=expected, current_timeline_id=current,
    )
    return _result(
        Outcome.STALE_TIMELINE, timeline_id=current, action_id=action_id,
        reason="timeline_mismatch",
    )


def _replay(
    prior: Mapping[str, Any], action_id: str, fingerprint: str, conversation_id: str, reason: str,
) -> TxResult[Any]:
    stored_fingerprint = str(prior.get("fingerprint", ""))
    if stored_fingerprint != fingerprint:
        observability.event(
            "state.transaction.action_payload_mismatch", level=logging.WARNING, reason=reason,
            group_id_hash=observability.safe_identifier(conversation_id),
        )
        return _result(
            Outcome.CONFLICT, revision=int(prior.get("revision", 0)),
            timeline_id=str(prior.get("timeline_id", "")), action_id=action_id,
            reason="action_payload_mismatch",
        )
    return _result(
        Outcome.DUPLICATE, revision=int(prior.get("revision", 0)),
        timeline_id=str(prior.get("timeline_id", "")), action_id=action_id,
        original_outcome=Outcome(prior.get("outcome", Outcome.APPLIED.value)),
        events=tuple(StagedEvent.from_json(event) for event in prior.get("events", [])),
        result=dict(prior.get("result") or {}),
    )


async def amutate(
    conversation_id: str,
    mutation: Mutation[T],
    **kwargs: Any,
) -> TxResult[T]:
    """``mutate`` off the event loop.

    The worker thread is not abandoned when the awaiting task is cancelled: the
    transaction either commits fully or not at all, and a retry with the same
    ``action_id`` reuses whatever it committed.
    """
    return await asyncio.shield(asyncio.to_thread(lambda: mutate(conversation_id, mutation, **kwargs)))


_NO_REPLAY = ("a replayed action returns its stored receipt in TxResult.result, not a value: "
              "call mutate()/commit_for_snapshot() and read the TxResult instead")


def mutate_value(conversation_id: str, mutation: Mutation[T], **kwargs: Any) -> T:
    """``mutate`` for callers that treat anything but success as an error. Not for a replayable action: a
    duplicate has no ``value`` to return."""
    if kwargs.get("action_id"):
        raise ValueError(_NO_REPLAY)
    outcome = mutate(conversation_id, mutation, **kwargs)
    if not outcome.ok:
        raise StateTransactionFailed(outcome)
    return outcome.value  # type: ignore[return-value]


class _SnapshotTimeline:
    """Sentinel: judge the caller's snapshot against the stored timeline."""


SNAPSHOT_TIMELINE = _SnapshotTimeline()


def commit_for_snapshot(
    state: GroupState,
    mutation: Mutation[T],
    *,
    reason: str = "tool",
    expected_timeline: str | None | _SnapshotTimeline = SNAPSHOT_TIMELINE,
    action_id: str | None = None,
    request_fingerprint: str | None = None,
    expected_revision: int | None = None,
    latest_state_guard: Callable[[GroupState], str | None] | None = None,
) -> TxResult[T]:
    """``mutate`` for a caller that holds a snapshot of the conversation.

    The caller's snapshot is refreshed from whatever was committed (or, when
    the transaction did not apply, from the latest stored state) so later work
    sees it. By default the snapshot's own timeline must still be current; a
    caller that captured its timeline earlier (a turn that spans awaits) passes
    that value, and ``None`` skips the check.
    """
    expected = (
        (state.timeline_id or None) if isinstance(expected_timeline, _SnapshotTimeline)
        else expected_timeline
    )
    outcome, committed = _mutate(
        state.group_id, mutation, reason=reason,
        expected_timeline=expected,
        action_id=action_id, request_fingerprint=request_fingerprint,
        expected_revision=expected_revision, latest_state_guard=latest_state_guard,
    )
    if committed is not None:
        sync_snapshot(state, committed)
    elif outcome.outcome is not Outcome.DUPLICATE:
        refresh_snapshot(state)
    return outcome


def run_snapshot(
    state: GroupState,
    mutation: Mutation[T],
    *,
    reason: str = "tool",
    action_id: str | None = None,
    request_fingerprint: str | None = None,
    expected_revision: int | None = None,
) -> T:
    """``commit_for_snapshot`` for callers that treat a failure as an exception. Not for a replayable action:
    a duplicate has no ``value`` to return.

    A stale snapshot timeline raises ``MutationHeld``; a revision conflict
    raises ``StateRevisionConflict``; a rule rejection raises
    ``StateTransactionFailed``.
    """
    if action_id:
        raise ValueError(_NO_REPLAY)
    outcome = commit_for_snapshot(
        state, mutation, reason=reason, action_id=action_id,
        request_fingerprint=request_fingerprint, expected_revision=expected_revision,
    )
    if outcome.outcome is Outcome.STALE_TIMELINE:
        raise mutation_admission.MutationHeld("stale tool timeline")
    if outcome.outcome is Outcome.CONFLICT:
        raise group_state.StateRevisionConflict(
            f"state transaction conflict for {observability.safe_identifier(state.group_id)}: {outcome.reason}"
        )
    if not outcome.ok:
        raise StateTransactionFailed(outcome)
    return outcome.value  # type: ignore[return-value]


def commit_snapshot(
    state: GroupState,
    *,
    reason: str = "command",
    mutate_tx: Callable[[sqlite3.Connection], None] | None = None,
    action_id: str | None = None,
    request_fingerprint: str | None = None,
) -> TxResult[None]:
    """Strict path for an action computed on a loaded snapshot.

    "Loaded" is ``state.loaded_timeline_id``: a flow that deliberately starts a
    new timeline rewrites ``state.timeline_id`` first and is still judged
    against the timeline it read.

    Use this when the caller validated and changed a ``GroupState`` it loaded
    (typically under ``locks.get_state_lock``) and the change cannot be
    re-expressed as a delta on the latest state. The snapshot is written only if
    the stored revision **and** timeline still match what was loaded; otherwise
    nothing is written and the matching exception is raised, so an old
    snapshot can never overwrite newer state — including a new game that
    happens to be at the same revision number. ``mutate_tx`` receives the
    transaction's connection for writes that must land atomically with the
    state. Prefer ``mutate`` whenever the change is a delta.
    """
    def write(ctx: TxContext) -> None:
        if mutate_tx is not None:
            mutate_tx(ctx.conn)
        ctx.replace_state(state)

    outcome = mutate(
        state.group_id, write, reason=reason, expected_timeline=state.loaded_timeline_id or None,
        expected_revision=state.state_revision, action_id=action_id,
        request_fingerprint=request_fingerprint,
    )
    if outcome.outcome is Outcome.STALE_TIMELINE:
        raise StaleTimelineError(
            f"state timeline changed for {observability.safe_identifier(state.group_id)}: "
            f"loaded={state.loaded_timeline_id}, current={outcome.timeline_id}"
        )
    if outcome.outcome is Outcome.CONFLICT:
        raise group_state.StateRevisionConflict(
            f"state revision conflict for {observability.safe_identifier(state.group_id)}: "
            f"loaded={state.state_revision}, current={outcome.revision}"
        )
    if not outcome.ok:
        raise StateTransactionFailed(outcome)
    return outcome
