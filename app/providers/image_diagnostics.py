"""Context-local failure metadata; never store provider inputs or error bodies."""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal, TypedDict

FailureKind = Literal['MissingCredentials', 'MissingToolCall', 'InvalidToolArguments', 'EmptyResponse']


class ImageFailure(TypedDict):
    provider: str
    stage: str
    error_type: str
    status_code: int | None
    timeout: bool
    elapsed_seconds: float


@dataclass
class _Capture:
    stage: str
    started: float = field(default_factory=time.perf_counter)
    failures: list[ImageFailure] = field(default_factory=list)


_current: ContextVar[_Capture | None] = ContextVar('image_failure_capture', default=None)


@contextmanager
def capture(stage: str) -> Iterator[list[ImageFailure]]:
    """Isolate each dispatch, including concurrent map worker threads."""
    state = _Capture(stage)
    token = _current.set(state)
    try:
        yield state.failures
    finally:
        _current.reset(token)


def record(provider: str, error: Exception | FailureKind) -> None:
    state = _current.get()
    if state is None:
        return
    error_type = error if isinstance(error, str) else type(error).__name__
    status = getattr(error, 'status_code', None)
    if type(status) is not int:
        status = getattr(error, 'code', None)  # Gemini exposes an integer HTTP code.
    state.failures.append({'provider': provider, 'stage': state.stage,
        'error_type': error_type, 'status_code': status if type(status) is int and 100 <= status <= 599 else None,
        'timeout': isinstance(error, TimeoutError) or 'Timeout' in error_type,
        'elapsed_seconds': round(time.perf_counter() - state.started, 3)})
