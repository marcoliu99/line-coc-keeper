"""Real-storage stand-in for the old in-memory ``StateStorePatch`` fakes.

Tests used to replace ``load_state``/``save_state`` in several modules with a
dict. Every game-state write now goes through
``app.repositories.state_transaction``, so faking the two functions no longer
reaches the code under test. This helper keeps the same shape (``with
StateStorePatch(...) as store``, ``store.put(state)``, ``store.store[group]``)
but reads and writes the throwaway SQLite database that ``tests/conftest.py``
provides, so the tests now also cover the real transaction.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, Self, cast
from unittest.mock import patch

from app import db
from app.models import GroupState
from app.repositories import group_state, state_transaction


def clone_state(state: GroupState) -> GroupState:
    return GroupState.from_dict(state.to_dict())


class _StoredStates:
    """Dict-like read view over the stored group states."""

    def __getitem__(self, group_id: str) -> GroupState:
        if group_id not in db.list_keys("group_states"):
            raise KeyError(group_id)
        return group_state.load_state(group_id)

    def get(self, group_id: str, default: GroupState | None = None) -> GroupState | None:
        try:
            return self[group_id]
        except KeyError:
            return default

    def __contains__(self, group_id: object) -> bool:
        return isinstance(group_id, str) and group_id in db.list_keys("group_states")

    def __iter__(self) -> Iterator[str]:
        return iter(db.list_keys("group_states"))


def replace_state(state: GroupState, *, keep_revision: bool = False) -> GroupState:
    """Store ``state`` as the only truth for its group.

    The revision restarts at 1 unless ``keep_revision`` asks for the state's own
    revision (at least 1) to be the stored one.
    """
    db.delete_json("group_states", state.group_id)
    fresh = clone_state(state)
    fresh.state_revision = max(state.state_revision - 1, 0) if keep_revision else 0
    group_state.save_state(fresh)
    return fresh


class StateStorePatch:
    """``with StateStorePatch(module, ...) as store`` — the modules are ignored.

    Rows a context creates stay readable after it exits (old tests assert on
    ``store`` after the ``with`` block) and are removed when the next context
    starts, so each context begins with an empty store as the dict fake did.
    """

    _left_behind: ClassVar[set[str]] = set()

    def __init__(self, *_modules: object) -> None:
        self.store = _StoredStates()
        self._before: set[str] = set()

    def __enter__(self) -> Self:
        for key in StateStorePatch._left_behind:
            db.delete_json("group_states", key)
        StateStorePatch._left_behind = set()
        self._before = set(db.list_keys("group_states"))
        return self

    def __exit__(self, *_exc: object) -> None:
        StateStorePatch._left_behind |= set(db.list_keys("group_states")) - self._before

    def put(self, state: GroupState) -> None:
        replace_state(state)

    def get(self, group_id: str) -> GroupState:
        """A private copy of the stored state (KeyError when there is none)."""
        return clone_state(self.store[group_id])


class MemoryTransactions:
    """Stand-in for the transaction boundary, for tests about something else.

    Some tests drive pending-button or turn-routing behaviour around one shared
    in-memory ``GroupState`` and only need "a state write happened". This applies
    the mutation to that shared object and counts commits; it proves nothing
    about atomicity (``tests/test_state_transaction.py`` does that against real
    SQLite).
    """

    def __init__(
        self, state: GroupState, *, on_commit: Callable[[GroupState], None] | None = None,
    ) -> None:
        self.state = state
        self.commits = 0
        self.on_commit = on_commit

    def mutate(
        self, conversation_id: str, mutation: Callable[[state_transaction.TxContext], Any], **_options: Any,
    ) -> state_transaction.TxResult[Any]:
        ctx = state_transaction.TxContext(
            conn=cast(Any, None), state=self.state, conversation_id=conversation_id, action_id="",
            timeline_id=state_transaction.effective_timeline(self.state),
        )
        value = mutation(ctx)
        committed = not ctx.save_skipped
        if committed:
            self.commits += 1
            if self.on_commit is not None:
                self.on_commit(self.state)
        return state_transaction.TxResult(
            state_transaction.Outcome.APPLIED, value=value, revision=self.state.state_revision,
            timeline_id=state_transaction.effective_timeline(self.state), committed=committed,
        )

    async def amutate(
        self, conversation_id: str, mutation: Callable[[state_transaction.TxContext], Any], **options: Any,
    ) -> state_transaction.TxResult[Any]:
        return self.mutate(conversation_id, mutation, **options)

    @contextmanager
    def patched(self) -> Iterator[Self]:
        with patch.object(state_transaction, "mutate", self.mutate), \
                patch.object(state_transaction, "amutate", self.amutate):
            yield self
