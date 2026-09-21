"""Small helpers for asyncio work that intentionally outlives its caller."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import observability

_logger = logging.getLogger(__name__)
_background_tasks: dict[asyncio.AbstractEventLoop, set[asyncio.Future[Any]]] = {}
_observed_tasks: set[asyncio.Future[Any]] = set()


def observe_background_task(task: asyncio.Future[Any], *, operation: str) -> None:
    """Consume a deliberately abandoned task's eventual result.

    ``asyncio.wait_for(asyncio.shield(...))`` lets a synchronous worker finish
    without cancelling its underlying thread.  This callback consumes its
    eventual exception and records it instead of producing an unobserved-task
    warning.
    """

    if task in _observed_tasks:
        return
    _observed_tasks.add(task)
    loop = task.get_loop()
    registry = _background_tasks.setdefault(loop, set())
    registry.add(task)

    def _consume(done: asyncio.Future[Any]) -> None:
        _observed_tasks.discard(done)
        registry.discard(done)
        if not registry:
            _background_tasks.pop(loop, None)
        if done.cancelled():
            return
        try:
            done.result()
        except Exception as exc:
            observability.event(
                "async.background_task.failed",
                level=logging.ERROR,
                operation=operation,
                status="error",
                error_type=type(exc).__name__,
            )
            _logger.exception("Detached %s task failed", operation)

    task.add_done_callback(_consume)


async def wait_for_background_tasks(timeout: float) -> None:
    """Wait for observed detached workers on the current event loop.

    A timeout/cancelled ``asyncio.to_thread`` operation cannot interrupt the
    underlying synchronous worker.  The observer prevents an unhandled late
    exception; this shutdown hook additionally gives those workers a bounded
    chance to finish before the event loop is closed.
    """
    registry = _background_tasks.get(asyncio.get_running_loop())
    if not registry:
        return
    tasks = tuple(task for task in registry if not task.done())
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    if pending:
        observability.event(
            "async.background_task.shutdown_degraded",
            level=logging.ERROR,
            status="timeout",
            pending_count=len(pending),
            timeout_ms=timeout * 1000,
        )
