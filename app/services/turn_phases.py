"""Where a turn's wall-clock time went, phase by phase, without counting overlapping work twice.

A turn queues for the conversation, searches, lets the Executor think and call tools, narrates, and later maintains memory.
Several of those overlap (a tool runs *inside* the Executor's model call; the prefetch search runs *while* the turn queues),
so adding up how long each took exceeds the time the player waited. Each phase therefore reports its own span
(``total_ms``, merged within the phase) and the time it alone accounts for (``exclusive_ms``, where inner work wins over
the outer wait that contains it). The exclusive times plus ``other`` add up to ``wall_ms``; ``overlap_ms`` says how much
was double covered. Nothing here changes what a turn does.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, ParamSpec, TypeVar

from app import observability

PHASES = (
    "queue_wait", "initial_retrieval", "executor_llm", "tool_execution", "recovery_retrieval",
    "continuation_processing", "narrator_llm", "memory_search", "memory_write", "embedding", "other",
)
# When phases overlap, the one earlier in this list is credited: the specific work inside a wait, not the wait.
_PRIORITY = (
    "embedding", "tool_execution", "memory_write", "memory_search", "recovery_retrieval", "initial_retrieval",
    "executor_llm", "narrator_llm", "continuation_processing", "queue_wait",
)
assert set(_PRIORITY) | {"other"} == set(PHASES)

_P = ParamSpec("_P")
_R = TypeVar("_R")


class Timeline:
    def __init__(self, kind: str, turn_id: str, player_id: str, campaign_id: str, origin: float) -> None:
        self.kind, self.turn_id, self.player_id, self.campaign_id, self.origin = kind, turn_id, player_id, campaign_id, origin
        self._lock = threading.Lock()
        self.intervals: list[tuple[str, float, float]] = []

    def add(self, phase: str, start: float, end: float) -> None:
        if phase not in PHASES or phase == "other":
            raise ValueError(f"unknown phase {phase!r}")
        start = max(start, self.origin)
        if end > start:
            with self._lock:
                self.intervals.append((phase, start, end))

    def summary(self, end: float) -> dict[str, Any]:
        with self._lock:
            intervals = list(self.intervals)
        wall = max(0.0, end - self.origin)
        totals = {phase: _union(item for item in intervals if item[0] == phase) for phase in PHASES if phase != "other"}
        exclusive = dict.fromkeys(PHASES, 0.0)
        points = sorted({self.origin, end, *(point for _, a, b in intervals for point in (a, min(b, end)))})
        for left, right in itertools.pairwise(points):
            active = {phase for phase, a, b in intervals if a <= left and min(b, end) >= right}
            winner = next((phase for phase in _PRIORITY if phase in active), "other")
            exclusive[winner] += right - left
        covered = wall - exclusive["other"]
        return {
            "wall_ms": _ms(wall),
            "total_ms": {phase: _ms(value) for phase, value in totals.items() if value},
            "exclusive_ms": {phase: _ms(value) for phase, value in exclusive.items() if value},
            "overlap_ms": _ms(max(0.0, sum(totals.values()) - covered)),
        }


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def _union(intervals: Any) -> float:
    total = 0.0
    cursor = float("-inf")
    for _, start, end in sorted(intervals, key=lambda item: item[1]):
        start = max(start, cursor)
        if end > start:
            total += end - start
            cursor = end
    return total


_current: contextvars.ContextVar[Timeline | None] = contextvars.ContextVar("coc_turn_timeline", default=None)


def current() -> Timeline | None:
    return _current.get()


@contextlib.contextmanager
def phase(name: str) -> Iterator[None]:
    """Record the time spent inside the block as ``name`` on the current timeline; free when there is none."""
    timeline = _current.get()
    if timeline is None:
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        timeline.add(name, start, time.monotonic())


def seed(name: str, start: float, end: float) -> None:
    """Add a span that happened before the timeline began (the queue wait, a prefetch that ran while queued)."""
    if (timeline := _current.get()) is not None:
        timeline.add(name, start, end)


@contextlib.contextmanager
def timeline(
    kind: str, *, turn_id: str, player_id: str, campaign_id: str, queue_wait_ms: float = 0.0,
) -> Iterator[Timeline]:
    """Open a timeline for one turn (or one maintenance pass) and report it when the block ends."""
    now = time.monotonic()
    line = Timeline(kind, turn_id, player_id, campaign_id, now - max(0.0, queue_wait_ms) / 1000)
    if queue_wait_ms > 0:
        line.add("queue_wait", line.origin, now)
    token = _current.set(line)
    try:
        yield line
    finally:
        _current.reset(token)
        try:
            _report(line, time.monotonic())
        except Exception:
            logging.getLogger(__name__).exception("Could not report the turn's phase timeline")


def _report(line: Timeline, end: float) -> None:
    ids: dict[str, Any] = {
        "turn_id": line.turn_id, "player_id": observability.safe_identifier(line.player_id),
        "campaign_id": observability.safe_identifier(line.campaign_id), "kind": line.kind,
    }
    for name, start, stop in sorted(line.intervals, key=lambda item: item[1]):
        observability.event(
            "turn.phase", level=logging.DEBUG, phase=name, start_ms=_ms(start - line.origin),
            end_ms=_ms(stop - line.origin), duration_ms=_ms(stop - start), **ids,
        )
    observability.event("turn.phases", **ids, **line.summary(end))


def timed_turn(func: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
    """Time ``supervisor.run_turn``: its queue wait, prefetch, Executor, tools, Narrator and continuation."""
    signature = inspect.signature(func)

    @functools.wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        bound = signature.bind_partial(*args, **kwargs).arguments
        state = bound.get("state")
        handoff = bound.get("handoff")
        prefetched = bound.get("prefetched_retrieval")
        turn_id = observability.current_context().get("turn_id") or observability.new_id("turn")
        with observability.context(turn_id=turn_id), timeline(
            "continuation" if bound.get("turn_kind") == "resolved_check_followup" else "turn",
            turn_id=turn_id, player_id=str(bound.get("user_id", "")),
            campaign_id=str(getattr(state, "group_id", "")),
            queue_wait_ms=float(getattr(handoff, "queue_wait_ms", None) or 0),
        ):
            started, finished = getattr(prefetched, "started_at", None), getattr(prefetched, "finished_at", None)
            if started and finished:
                seed("initial_retrieval", started, finished)
            if bound.get("turn_kind") == "resolved_check_followup":
                with phase("continuation_processing"):
                    return await func(*args, **kwargs)
            return await func(*args, **kwargs)

    return wrapper
