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

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from app import observability
from app.config import LLM_MAX_RETRIES, LLM_RETRY_BASE_DELAY_SECONDS

T = TypeVar("T")

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
    return isinstance(status_code, int) and status_code >= 500


def is_retryable(exc: Exception) -> bool:
    """True for a failure worth retrying: a connection/timeout error by
    class name (including httpx's own transport exceptions, see above), or
    an HTTP 5xx status (openai/anthropic's APIStatusError family exposes
    this as `.status_code`; a 5xx is a transient server-side problem
    regardless of what the exception class itself is named).

    Also walks `__cause__`/`__context__`: some SDKs wrap the actual
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

    Always runs synchronously in the caller's thread. Every provider's
    run_conversation is invoked via asyncio.to_thread (see app/commands.py),
    so a plain time.sleep() here blocks only that worker thread, never the
    event loop."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if attempt > LLM_MAX_RETRIES or not is_retryable(exc):
                raise
            delay = LLM_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
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
            )
            time.sleep(delay)
