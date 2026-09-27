"""Process-local worker ownership. Durable crash recovery is a later tranche.

A timed-out asyncio task does not prove its thread stopped. Only the actual
worker's finally may settle its generation. Database writes recheck ownership
and the original timeline, including callers which bypass Discord routing.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

NOTICE = "上一個操作仍在完成或核對中，目前暫停本團的變更。請稍後查看狀態；不要重擲或重做已完成的部分。"


class MutationHeld(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerOwner:
    conversation_id: str
    timeline_id: str
    generation: str
    operation: str


_guard = threading.RLock()
_workers: dict[str, WorkerOwner] = {}
_started: set[str] = set()
_holds: dict[str, set[str]] = {}
_current: ContextVar[WorkerOwner | None] = ContextVar("mutation_owner", default=None)


def assert_admitted(conversation_id: str, *, timeline_id: str | None = None) -> None:
    owner = _current.get()
    with _guard:
        holds = _holds.get(conversation_id, set())
        permitted = owner is not None and owner.conversation_id == conversation_id and owner.generation in holds
        if holds and not permitted:
            raise MutationHeld(NOTICE)
        if owner and owner.conversation_id == conversation_id:
            if owner.generation not in _workers:
                raise MutationHeld("worker ownership has already settled")
            if timeline_id is not None and timeline_id != owner.timeline_id:
                raise MutationHeld("worker timeline has changed; obsolete effects were rejected")


def is_held(conversation_id: str) -> bool:
    with _guard:
        return bool(_holds.get(conversation_id))


def start_worker(conversation_id: str, timeline_id: str, operation: str) -> WorkerOwner:
    with _guard:
        assert_admitted(conversation_id)
        owner = WorkerOwner(conversation_id, timeline_id, uuid4().hex, operation)
        _workers[owner.generation] = owner
        return owner


def detach(owner: WorkerOwner) -> None:
    """Publish the hold synchronously, before any marker I/O or cancellation exit."""
    with _guard:
        if _workers.get(owner.generation) == owner:
            _holds.setdefault(owner.conversation_id, set()).add(owner.generation)


def settle(owner: WorkerOwner) -> None:
    """Called by the actual worker, after recording its result, never by Task.done."""
    with _guard:
        if _workers.get(owner.generation) != owner:
            return
        del _workers[owner.generation]
        _started.discard(owner.generation)
        holds = _holds.get(owner.conversation_id)
        if holds is not None:
            holds.discard(owner.generation)
            if not holds:
                del _holds[owner.conversation_id]


@contextmanager
def bind(owner: WorkerOwner):
    token = _current.set(owner)
    try:
        yield
    finally:
        _current.reset(token)


# These commands were audited to read/display only. Everything else is held
# conservatively, including dice, character switching and timeline replacement.
PURE_READ_COMMANDS = frozenset({"help", "status", "characters", "purchases"})


def command_is_read_only(text: str) -> bool:
    parts = text.strip().split()
    return len(parts) == 2 and parts[0].lower() == "/coc" and parts[1].lower() in PURE_READ_COMMANDS


_read_scope: ContextVar[bool] = ContextVar("admission_read_scope", default=False)


@contextmanager
def command_scope(text: str):
    token = _read_scope.set(command_is_read_only(text))
    try:
        yield
    finally:
        _read_scope.reset(token)


def check_conversation_entry(conversation_id: str) -> None:
    if not _read_scope.get():
        assert_admitted(conversation_id)


def guard_async_entry(function):
    """Guard direct command/upload handlers as well as their routed callers."""
    import functools
    import inspect

    signature = inspect.signature(function)

    @functools.wraps(function)
    async def guarded(*args, **kwargs):
        bound = signature.bind(*args, **kwargs).arguments
        group_id = bound.get("conversation_id")
        parts = bound.get("parts")
        text = " ".join(parts) if parts else bound.get("text", "")
        if group_id and is_held(group_id) and not command_is_read_only(text):
            reply = bound.get("reply")
            if reply:
                await reply(NOTICE)
                return False
            raise MutationHeld(NOTICE)
        return await function(*args, **kwargs)

    return guarded


def is_detached(owner: WorkerOwner) -> bool:
    with _guard:
        return owner.generation in _holds.get(owner.conversation_id, set())


def outstanding_workers() -> int:
    with _guard:
        return len(_workers)


def mark_started(owner: WorkerOwner) -> bool:
    with _guard:
        if _workers.get(owner.generation) != owner:
            return False
        _started.add(owner.generation)
        return True


def reject_unstarted(owner: WorkerOwner) -> None:
    """A cancelled Task can reject queued work only if the thread never started."""
    with _guard:
        if owner.generation not in _started:
            settle(owner)
