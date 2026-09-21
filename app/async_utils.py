"""Small helpers for asyncio work that intentionally outlives its caller."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import observability

_logger = logging.getLogger(__name__)


def observe_background_task(task: asyncio.Task[Any], *, operation: str) -> None:
    """Consume a deliberately abandoned task's eventual result.

    ``asyncio.wait_for(asyncio.shield(...))`` lets a synchronous worker finish
    without cancelling its underlying thread.  This callback consumes its
    eventual exception and records it instead of producing an unobserved-task
    warning.
    """

    def _consume(done: asyncio.Task[Any]) -> None:
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
