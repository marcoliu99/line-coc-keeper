"""Shared transient-error retry/backoff for the three LLM provider adapters
(anthropic_provider.py, gemini_provider.py, openai_provider.py).

None of the three retried a network-level failure before this module
existed — client.messages.create/generate_content/responses.create each ran
with no try/except of their own, so a single connection drop or transient
5xx raised straight out of run_conversation and surfaced to the player as a
bare "發生錯誤了：..." with their whole turn lost, even though the failure
had nothing to do with their input and often would have succeeded a second
later.
"""
from __future__ import annotations

import asyncio
import enum
import inspect
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app import observability
from app.config import (
    LLM_MAX_RETRIES,
    LLM_RETRY_BASE_DELAY_SECONDS,
    LLM_TIMEOUT_RETRIES,
    OPENAI_MAX_CONCURRENT_REQUESTS,
)

T = TypeVar("T")

# Per-provider admission gate limiting concurrent in-flight API attempts for
# this process (see docs/specs/enhancement-llm-rate-limit-and-turn-latency.md
# 3.1). Only OpenAI is gated today — no confirmed evidence Anthropic/Gemini
# need one, so they stay ungated rather than picking an arbitrary cap for
# them too. Built lazily, inside async_call_with_retry (i.e. only once an
# event loop is actually running it), rather than at import time.
_admission_semaphores: dict[str, asyncio.Semaphore] = {}


def _admission_semaphore_for(provider: str) -> asyncio.Semaphore | None:
    if provider != "openai":
        return None
    semaphore = _admission_semaphores.get(provider)
    if semaphore is None:
        semaphore = asyncio.Semaphore(OPENAI_MAX_CONCURRENT_REQUESTS)
        _admission_semaphores[provider] = semaphore
    return semaphore


def _full_jitter_delay(computed_delay: float) -> float:
    """AWS-style "full jitter": sleep a random duration between 0 and the
    computed exponential-backoff delay, instead of always sleeping the exact
    same amount. Spreads out retries that would otherwise collide again when
    several requests fail around the same time and all follow the same fixed
    schedule."""
    return random.uniform(0, computed_delay)


def _extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Best-effort, duck-typed search of exc's cause/context chain for a
    Retry-After (or equivalent rate-limit-reset) value the SDK or HTTP
    response exposes. Returns None — never a guess parsed out of the error
    message — when nothing usable is found, so callers fall back to the
    normal computed backoff."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        retry_after = getattr(current, "retry_after", None)
        if isinstance(retry_after, int | float) and retry_after >= 0:
            return float(retry_after)
        headers = getattr(getattr(current, "response", None), "headers", None)
        if headers is not None:
            for key in ("retry-after", "Retry-After"):
                try:
                    value = headers.get(key)
                except AttributeError:
                    value = None
                if value is not None:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        pass
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _extract_safe_error_fields(exc: BaseException) -> dict[str, Any]:
    """Best-effort, duck-typed extraction of the handful of provider-error
    fields that are safe to put in structured logs: HTTP status, the
    provider's own error code, and an API-side request id if the SDK exposes
    one. Never the prompt, response body, API key, or the exception's own
    str() — a field this can't confidently identify is simply left out
    rather than guessed at."""
    seen: set[int] = set()
    current: BaseException | None = exc
    fields: dict[str, Any] = {}
    while current is not None and id(current) not in seen:
        if "status_code" not in fields:
            status_code = getattr(current, "status_code", None)
            if isinstance(status_code, int):
                fields["status_code"] = status_code
        if "provider_error_code" not in fields:
            code = getattr(current, "code", None)
            if isinstance(code, str):
                fields["provider_error_code"] = code
        if "provider_request_id" not in fields:
            request_id = getattr(current, "request_id", None)
            if isinstance(request_id, str) and request_id:
                fields["provider_request_id"] = request_id
            else:
                headers = getattr(getattr(current, "response", None), "headers", None)
                for key in ("x-request-id", "X-Request-Id", "request-id"):
                    try:
                        value = headers.get(key) if headers is not None else None
                    except AttributeError:
                        value = None
                    if value:
                        fields["provider_request_id"] = value
                        break
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return fields


class ProviderError(str, enum.Enum):
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSIENT_SERVER = "transient_server"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN = "unknown"

# Matched against type(exc).__name__.lower() to classify an exception as a
# transient, worth-retrying failure. Deliberately duck-typed by class name
# rather than importing each SDK's exact exception hierarchy — openai,
# anthropic, and google-genai each define their own (APIConnectionError,
# APITimeoutError, InternalServerError, ServiceUnavailable, ...), and this
# project stays SDK-version-agnostic on purpose, the same style already used
# by openai_provider.py's _create_response for its unsupported-parameter
# detection (matching on the stringified error, not an imported type).
#
# Also includes httpx's own transport-level exception names (ConnectError,
# ReadError, WriteError, RemoteProtocolError, PoolTimeout, ...) — PR review
# caught that Gemini's google-genai SDK (and potentially openai/anthropic
# depending on where a connection actually drops) can raise the underlying
# httpx transport exception directly rather than always wrapping it in the
# SDK's own APIConnectionError-style class, and none of those httpx class
# names matched the original marker list (e.g. "ConnectError" doesn't
# contain "connectionerror").
_RETRYABLE_NAME_MARKERS = (
    "connectionerror", "connectionreset", "connectionaborted", "remotedisconnected",
    "timeouterror", "readtimeout", "writetimeout", "connecttimeout",
    "internalservererror", "serviceunavailable", "overloadederror",
    "temporarilyunavailable", "servererror",
    # httpx transport exceptions (httpx._exceptions), seen unwrapped from
    # some SDK paths:
    "connecterror", "readerror", "writeerror", "closederror",
    "remoteprotocolerror", "protocolerror", "pooltimeout", "networkerror",
)


def _is_retryable_one(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if any(marker in name for marker in _RETRYABLE_NAME_MARKERS):
        return True
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and (status_code == 429 or status_code >= 500)


def classify_exception(exc: Exception) -> ProviderError:
    """Map SDK- and transport-specific failures to one retry policy.

    The adapter intentionally remains duck-typed: installed SDK versions use
    different exception classes, while status codes and class names are stable
    enough for this boundary.  A cause/context chain is inspected so a wrapped
    httpx timeout is not misclassified as an unknown application error.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        name = type(current).__name__.lower()
        status_code = getattr(current, "status_code", None)
        if isinstance(status_code, int):
            if status_code == 429:
                return ProviderError.RATE_LIMITED
            if status_code >= 500:
                return ProviderError.TRANSIENT_SERVER
            if status_code in {401, 403}:
                return ProviderError.AUTH_FAILED
            if status_code == 404:
                return ProviderError.MODEL_NOT_FOUND
            if 400 <= status_code < 500:
                return ProviderError.INVALID_REQUEST
        if "timeout" in name or name in {"timeouterror", "asyncio.timeouterror"}:
            return ProviderError.TIMEOUT
        if any(marker in name for marker in ("authentication", "unauthorized", "permission")):
            return ProviderError.AUTH_FAILED
        if "modelnotfound" in name or ("notfound" in name and "model" in str(current).lower()):
            return ProviderError.MODEL_NOT_FOUND
        if any(marker in name for marker in ("invalidrequest", "badrequest", "unprocessable")):
            return ProviderError.INVALID_REQUEST
        seen.add(id(current))
        current = current.__cause__ or current.__context__

    if is_retryable(exc):
        return ProviderError.TRANSIENT_SERVER
    return ProviderError.UNKNOWN


def is_retryable(exc: Exception) -> bool:
    """True for a failure worth retrying: a connection/timeout error by
    class name (including httpx's own transport exceptions, see above), or
    an HTTP 5xx status (openai/anthropic's APIStatusError family exposes
    this as `.status_code`; a 5xx is a transient server-side problem
    regardless of what the exception class itself is named).

    Rate limits (HTTP 429) are included because the provider APIs explicitly
    signal that the same request may succeed after backoff.  Also walks
    `__cause__`/`__context__`: some SDKs wrap the actual
    transport failure inside their own exception type (e.g. `raise
    SomeSDKError(...) from httpx_error`) without the outer class's own name
    matching anything above — checking the chain catches that without
    needing to know every SDK's exact wrapping behavior."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if _is_retryable_one(current):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def call_with_retry(fn: Callable[[], T], *, provider: str, operation: str) -> T:
    """Call `fn()`, retrying up to LLM_MAX_RETRIES times (exponential
    backoff: LLM_RETRY_BASE_DELAY_SECONDS * 2**(attempt-1)) when the raised
    exception is_retryable(). Anything else — or the final attempt
    regardless — is re-raised immediately, unchanged, so callers don't need
    their own except clause for this.

    This compatibility helper remains synchronous for the synchronous vision
    and text-extraction adapters. Conversation requests use
    :func:`async_call_with_retry`, which uses cancellable asyncio.sleep."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if attempt > LLM_MAX_RETRIES or not is_retryable(exc):
                raise
            retry_after = _extract_retry_after_seconds(exc)
            computed_delay = LLM_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            delay = retry_after if retry_after is not None else _full_jitter_delay(computed_delay)
            observability.increment_metric("llm_retry_count")
            observability.event(
                "llm.request.retry",
                level=logging.WARNING,
                provider=provider,
                api_operation=operation,
                attempt=attempt,
                max_attempts=LLM_MAX_RETRIES,
                delay_s=delay,
                error_type=type(exc).__name__,
                **_extract_safe_error_fields(exc),
            )
            time.sleep(delay)


async def async_call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    provider: str,
    operation: str,
    request_id: str | None = None,
) -> T:
    """Async counterpart of :func:`call_with_retry`.

    The callback owns one provider attempt (including its per-attempt timeout).
    Backoff is cancellable, and ``CancelledError`` is deliberately not caught.
    Timeout retries are capped independently so a slow upstream cannot consume
    the full transient retry budget indefinitely.
    """
    current = observability.current_context()
    logical_request_id = request_id or current.get("provider_request_id") or observability.new_id("llm")
    attempt = 0
    timeout_attempts = 0
    semaphore = _admission_semaphore_for(provider)
    while True:
        admission_wait_s = 0.0
        if semaphore is not None:
            admission_wait_start = time.monotonic()
            await semaphore.acquire()
            admission_wait_s = time.monotonic() - admission_wait_start
        try:
            try:
                with observability.context(provider_request_id=logical_request_id), observability.span(
                    "llm.request.attempt",
                    provider=provider,
                    api_operation=operation,
                    logical_request_id=logical_request_id,
                    attempt=attempt + 1,
                    admission_wait_s=admission_wait_s,
                ):
                    result = fn()
                    if not inspect.isawaitable(result):
                        raise TypeError("async_call_with_retry callback must return an awaitable")
                    return await result
            finally:
                # Release before backoff/retry-queueing, not after — a
                # request waiting out a 429's backoff must not hold an
                # admission slot another conversation's attempt could be
                # using in the meantime (spec 3.1, point 1).
                if semaphore is not None:
                    semaphore.release()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_kind = classify_exception(exc)
            if error_kind == ProviderError.TIMEOUT:
                if timeout_attempts >= LLM_TIMEOUT_RETRIES:
                    raise
                timeout_attempts += 1
            elif error_kind not in {
                ProviderError.RATE_LIMITED,
                ProviderError.TRANSIENT_SERVER,
            }:
                raise

            if attempt >= LLM_MAX_RETRIES:
                raise
            attempt += 1
            retry_after = _extract_retry_after_seconds(exc)
            computed_delay = LLM_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            delay = retry_after if retry_after is not None else _full_jitter_delay(computed_delay)
            observability.increment_metric("llm_retry_count")
            observability.event(
                "llm.request.retry",
                level=logging.WARNING,
                provider=provider,
                api_operation=operation,
                logical_request_id=logical_request_id,
                attempt=attempt,
                max_attempts=LLM_MAX_RETRIES,
                delay_s=delay,
                error_type=type(exc).__name__,
                error_kind=error_kind.value,
                **_extract_safe_error_fields(exc),
            )
            await asyncio.sleep(delay)
