"""Low-overhead structured performance events and request context.

The performance channel is deliberately separate from ordinary Python logger
calls.  When LOG_ENABLED is false, span/event calls return before allocating
event payloads or starting timers; developers can still use normal
``logger.debug/info/warning/error`` calls through LOG_TEXT_ENABLED.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import logging
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from app import config

_logger = logging.getLogger(__name__)
_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "coc_log_context", default=None
)
_METRICS: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "coc_log_metrics", default=None
)
_NULLABLE_EVENT_FIELDS = frozenset({
    "reasoning_effort", "input_tokens", "cached_input_tokens",
    "output_tokens", "reasoning_tokens",
})


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def new_id(prefix: str) -> str:
    """Create a correlation id for an outer lifecycle owner."""
    return _new_id(prefix)


def _safe_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    if not config.LOG_HASH_IDENTIFIERS:
        return str(value)
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def safe_identifier(value: str | None) -> str | None:
    """Return the configured redacted form for an identifier-bearing field."""
    return _safe_identifier(value)


def current_context() -> dict[str, str]:
    """Return a copy so callers cannot mutate the context shared by a task."""
    return dict(_CONTEXT.get() or {})


def current_metrics() -> dict[str, int]:
    return dict(_METRICS.get() or {})


@contextlib.contextmanager
def metrics_context(metrics: dict[str, int]) -> Iterator[dict[str, int]]:
    inherited = dict(_METRICS.get() or {})
    inherited.update(metrics)
    token = _METRICS.set(inherited)
    try:
        yield inherited
    finally:
        metrics.update(inherited)
        _METRICS.reset(token)


def increment_metric(name: str, amount: int = 1) -> None:
    if config.LOG_ENABLED:
        values = _METRICS.get()
        if values is None:
            values = {}
            _METRICS.set(values)
        values[name] = values.get(name, 0) + amount


def mark_request_error() -> None:
    """Mark a request as handled-error while preserving its outer lifecycle."""
    bound = dict(_CONTEXT.get() or {})
    bound["request_status"] = "error"
    _CONTEXT.set(bound)


@contextlib.contextmanager
def context(**values: str | None) -> Iterator[dict[str, str]]:
    """Temporarily add correlation values to the current async/thread context."""
    previous = _CONTEXT.get() or {}
    merged = dict(previous)
    for key, value in values.items():
        if value is not None:
            merged[key] = str(value)
    token = _CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _CONTEXT.reset(token)


@contextlib.contextmanager
def detached_context(**values: str | None) -> Iterator[dict[str, str]]:
    """Start a context without inheriting the parent request context."""
    token = _CONTEXT.set({})
    metrics_token = _METRICS.set({})
    try:
        if "conversation_id" in values:
            values = {
                **values,
                "conversation_id": _safe_identifier(values["conversation_id"]),
            }
        with context(**values) as bound:
            yield bound
    finally:
        _CONTEXT.reset(token)
        _METRICS.reset(metrics_token)


@contextlib.contextmanager
def request_context(
    *,
    conversation_id: str | None = None,
    request_id: str | None = None,
    turn_id: str | None = None,
    maintenance_id: str | None = None,
) -> Iterator[dict[str, str]]:
    """Bind IDs for one request, turn, or detached maintenance task.

    IDs are omitted only when both channels are disabled, preserving the
    zero-observation fast path.  Text-only debugging still receives the same
    correlation fields as structured events.
    """
    if not config.LOG_ENABLED and not config.LOG_TEXT_ENABLED:
        yield {}
        return
    metrics_token = _METRICS.set({})
    with context(
        request_id=request_id or _new_id("req"),
        turn_id=turn_id,
        maintenance_id=maintenance_id,
        conversation_id=_safe_identifier(conversation_id),
    ) as bound:
        try:
            yield bound
        finally:
            _METRICS.reset(metrics_token)


def _record_context() -> dict[str, str]:
    return current_context()


def event(
    name: str,
    *,
    level: int = logging.INFO,
    slow_threshold_ms: int | None = None,
    **fields: Any,
) -> None:
    """Emit one structured event, doing no work when the channel is disabled."""
    if not config.LOG_ENABLED or not _logger.isEnabledFor(level):
        return
    payload: dict[str, Any] = {"event": name, **_record_context()}
    payload.update({
        key: value for key, value in fields.items()
        if value is not None or key in _NULLABLE_EVENT_FIELDS
    })
    if slow_threshold_ms is not None and "duration_ms" in payload:
        payload["slow_threshold_ms"] = slow_threshold_ms
        payload["slow"] = payload["duration_ms"] >= slow_threshold_ms
    _logger.log(level, name, extra={"structured_event": payload})


@contextlib.contextmanager
def span(
    name: str,
    *,
    level: int = logging.INFO,
    slow_threshold_ms: int | None = None,
    metrics: dict[str, Any] | None = None,
    slow_event: str | None = None,
    **fields: Any,
) -> Iterator[None]:
    """Measure one synchronous or async-compatible operation."""
    # A normal span is filtered by its requested level.  A timed INFO span
    # still needs to run when WARNING is enabled so a slow completion can be
    # promoted to WARNING without losing the measurement.
    enabled = config.LOG_ENABLED and (
        _logger.isEnabledFor(level)
        or (slow_threshold_ms is not None and _logger.isEnabledFor(logging.WARNING))
    )
    if not enabled:
        yield
        return

    def merged_fields() -> dict[str, Any]:
        """Merge inherited, operation, and explicit fields without collisions.

        Explicit span fields are the most specific values.  In particular,
        this prevents a metric inherited from a parent span from being passed
        twice as the same keyword argument when a child operation reports its
        own value.
        """
        merged: dict[str, Any] = dict(_METRICS.get() or {})
        merged.update(metrics or {})
        merged.update(fields)
        return merged

    event(name + ".started", level=level, **fields)
    started = time.perf_counter()
    try:
        yield
    except asyncio.CancelledError:
        duration_ms = (time.perf_counter() - started) * 1000
        event(
            name + ".cancelled",
            level=logging.WARNING,
            duration_ms=duration_ms,
            status="cancelled",
            error_type="CancelledError",
            slow_threshold_ms=slow_threshold_ms,
            **merged_fields(),
        )
        raise
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        event(
            name + ".failed",
            level=logging.ERROR,
            duration_ms=duration_ms,
            status="timeout" if isinstance(exc, TimeoutError) else "error",
            error_type=type(exc).__name__,
            slow_threshold_ms=slow_threshold_ms,
            **merged_fields(),
        )
        raise
    else:
        duration_ms = (time.perf_counter() - started) * 1000
        output_level = level
        if slow_threshold_ms is not None and duration_ms >= slow_threshold_ms:
            output_level = logging.WARNING
            if slow_event:
                event(
                    slow_event, level=logging.WARNING, duration_ms=duration_ms,
                    slow_threshold_ms=slow_threshold_ms, **fields,
                )
        event(
            name + ".completed",
            level=output_level,
            duration_ms=duration_ms,
            status="success",
            slow_threshold_ms=slow_threshold_ms,
            **merged_fields(),
        )


def usage_fields(response: Any) -> dict[str, Any]:
    """Normalize common OpenAI-style usage objects without requiring an SDK."""
    if not config.LOG_INCLUDE_USAGE:
        return {}
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None, "reasoning_tokens": None}

    def get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    input_details = get(usage, "input_tokens_details")
    output_details = get(usage, "output_tokens_details")
    return {
        "input_tokens": get(usage, "input_tokens"),
        "cached_input_tokens": get(input_details, "cached_tokens"),
        "output_tokens": get(usage, "output_tokens"),
        "reasoning_tokens": get(output_details, "reasoning_tokens"),
    }


def tool_name(name: str) -> str:
    """Keep dynamic tool identifiers bounded and safe for log aggregation."""
    return name[:100]


def llm_reasoning_effort(provider: str) -> str | None:
    """Return the configured reasoning effort only for providers that use it."""
    if provider != "openai":
        return None
    return config.KEEPER_REASONING_EFFORT or None
