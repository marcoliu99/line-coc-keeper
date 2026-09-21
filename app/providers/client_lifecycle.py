"""Concurrency-safe lifecycle management for provider async clients.

Provider SDK clients are tied to the event loop that created them.  This
helper keeps the client and its in-flight counter together, so a shutdown or
event-loop switch cannot reset the counter while an older request is still
using the client.  The small thread condition is intentional: it lets a
second event loop observe a retiring client without awaiting an asyncio
primitive owned by the first loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class _ClientState:
    loop: asyncio.AbstractEventLoop
    client: Any
    owner: Any = None
    inflight: int = 0
    closing: bool = False
    closed: bool = False


class AsyncClientLifecycle:
    """Own one provider client at a time without crossing loop primitives."""

    def __init__(self, provider: str, shutdown_grace_seconds: float) -> None:
        self.provider = provider
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._current: _ClientState | None = None
        self._retired: dict[int, _ClientState] = {}
        self._shutting_down = False

    def _wait_until_available(self) -> None:
        with self._changed:
            self._changed.wait_for(lambda: not self._shutting_down)

    def _new_state(self, loop: asyncio.AbstractEventLoop, create: Callable[[], Any]) -> _ClientState:
        created = create()
        if isinstance(created, tuple):
            client, owner = created
        else:
            client, owner = created, None
        state = _ClientState(loop=loop, client=client, owner=owner)
        self._current = state
        return state

    def _retire_current(self, loop: asyncio.AbstractEventLoop) -> _ClientState | None:
        with self._changed:
            state = self._current
            if state is None or state.loop is loop:
                return None
            if not state.loop.is_closed():
                raise RuntimeError(
                    f"{self.provider} client belongs to another active event loop; "
                    "shut it down from its owning loop before switching"
                )
            state.closing = True
            self._current = None
            self._retired[id(state)] = state
            self._changed.notify_all()
            return state

    async def _wait_and_close(
        self,
        state: _ClientState,
        close: Callable[[Any, Any], Awaitable[None]],
    ) -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._wait_for_drain, state),
                self.shutdown_grace_seconds,
            )
        except asyncio.TimeoutError:
            from app import observability

            observability.event(
                "provider.shutdown.degraded",
                level=logging.ERROR,
                provider=self.provider,
                status="timeout",
                inflight=state.inflight,
                timeout_ms=self.shutdown_grace_seconds * 1000,
            )
        try:
            await asyncio.wait_for(
                close(state.client, state.owner),
                self.shutdown_grace_seconds,
            )
        except asyncio.TimeoutError:
            from app import observability

            observability.event(
                "provider.shutdown.degraded",
                level=logging.ERROR,
                provider=self.provider,
                status="close_timeout",
                operation="close",
                timeout_ms=self.shutdown_grace_seconds * 1000,
            )
        except Exception as exc:
            from app import observability

            observability.event(
                "provider.shutdown.failed",
                level=logging.ERROR,
                provider=self.provider,
                status="error",
                operation="close",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            with self._changed:
                state.closed = True
                self._retired.pop(id(state), None)
                self._changed.notify_all()

    def _wait_for_drain(self, state: _ClientState) -> None:
        with self._changed:
            self._changed.wait_for(lambda: state.inflight == 0)

    async def _drain_and_close(
        self,
        state: _ClientState,
        close: Callable[[Any, Any], Awaitable[None]],
    ) -> None:
        await self._wait_and_close(state, close)

    async def _ensure_cleanup(
        self,
        state: _ClientState,
        close: Callable[[Any, Any], Awaitable[None]],
    ) -> None:
        """Finish retirement even if the caller is cancelled mid-cleanup."""
        cleanup = asyncio.create_task(self._drain_and_close(state, close))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await asyncio.shield(cleanup)
            raise

    async def get_or_create(
        self,
        create: Callable[[], Any],
        close: Callable[[Any, Any], Awaitable[None]],
    ) -> Any:
        """Get a loop-local client, retiring a client from another loop."""
        loop = asyncio.get_running_loop()
        while True:
            with self._changed:
                if self._shutting_down:
                    wait = True
                    state = None
                else:
                    wait = False
                    state = self._current
                    if state is not None and state.loop is loop and not state.closing:
                        return state.client
                    if state is not None and state.loop is not loop and not state.loop.is_closed():
                        raise RuntimeError(
                            f"{self.provider} client belongs to another active event loop; "
                            "shut it down from its owning loop before switching"
                        )
                    if state is None:
                        return self._new_state(loop, create).client
            if wait:
                await asyncio.to_thread(self._wait_until_available)
                continue
            retired = self._retire_current(loop)
            if retired is not None:
                await self._ensure_cleanup(retired, close)

    async def acquire(
        self,
        create: Callable[[], Any],
        close: Callable[[Any, Any], Awaitable[None]],
    ) -> tuple[Any, _ClientState]:
        """Atomically get a client and register one in-flight request."""
        loop = asyncio.get_running_loop()
        while True:
            with self._changed:
                if self._shutting_down:
                    wait = True
                    state = None
                else:
                    wait = False
                    state = self._current
                    if state is not None and state.loop is loop and not state.closing:
                        state.inflight += 1
                        return state.client, state
                    if state is not None and state.loop is not loop and not state.loop.is_closed():
                        raise RuntimeError(
                            f"{self.provider} client belongs to another active event loop; "
                            "shut it down from its owning loop before switching"
                        )
                    if state is None:
                        state = self._new_state(loop, create)
                        state.inflight = 1
                        return state.client, state
            if wait:
                await asyncio.to_thread(self._wait_until_available)
                continue
            retired = self._retire_current(loop)
            if retired is not None:
                await self._ensure_cleanup(retired, close)

    def release(self, state: _ClientState) -> None:
        with self._changed:
            state.inflight -= 1
            self._changed.notify_all()

    async def shutdown(
        self,
        close: Callable[[Any, Any], Awaitable[None]],
    ) -> None:
        """Block new acquisitions, drain current requests, then close."""
        loop = asyncio.get_running_loop()
        while True:
            with self._changed:
                if self._shutting_down:
                    wait = True
                    state = None
                else:
                    wait = False
                    state = self._current
                    if state is None:
                        return
                    if state.loop is not loop and not state.loop.is_closed():
                        raise RuntimeError(
                            f"{self.provider} shutdown must run on the owning event loop"
                        )
                    self._shutting_down = True
                    state.closing = True
                    self._current = None
                    self._retired[id(state)] = state
                    self._changed.notify_all()
            if wait:
                await asyncio.to_thread(self._wait_until_available)
                continue
            assert state is not None
            try:
                await self._ensure_cleanup(state, close)
            finally:
                with self._changed:
                    self._shutting_down = False
                    self._changed.notify_all()
            return

    @property
    def current_state(self) -> _ClientState | None:
        with self._changed:
            return self._current

    @property
    def retired_states(self) -> tuple[_ClientState, ...]:
        with self._changed:
            return tuple(self._retired.values())
