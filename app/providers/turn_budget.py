"""A shared deadline for LLM work, without cancelling committed game tools."""
from __future__ import annotations

import asyncio
import contextvars
import functools
import time
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from app import config

P = ParamSpec('P')
T = TypeVar('T')
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar('llm_deadline', default=None)


class TurnDeadlineExceeded(TimeoutError):
    pass


def remaining(limit: float | None = None) -> float | None:
    end = _deadline.get()
    if end is None:
        return limit
    left = end - time.monotonic()
    if left <= 0:
        raise TurnDeadlineExceeded('LLM turn deadline exceeded')
    return left if limit is None else min(left, limit)


async def sleep(delay: float) -> None:
    left = remaining()
    if left is not None and delay >= left:
        raise TurnDeadlineExceeded('Required wait exceeds LLM turn deadline')
    await asyncio.sleep(delay)


def with_turn_deadline(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @functools.wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        if _deadline.get() is not None or config.LLM_TURN_DEADLINE_SECONDS <= 0:
            return await fn(*args, **kwargs)
        token = _deadline.set(time.monotonic() + config.LLM_TURN_DEADLINE_SECONDS)
        try:
            return await fn(*args, **kwargs)
        finally:
            _deadline.reset(token)
    return wrapped
