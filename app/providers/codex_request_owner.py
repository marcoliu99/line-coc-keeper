"""One admission and deadline boundary for all Codex requests."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections import deque
from dataclasses import dataclass

from app import config
from app.providers import turn_budget


@dataclass
class _Waiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[None]
    granted: bool = False


class Lease:
    def __init__(self, owner: RequestOwner, task: asyncio.Task | None) -> None:
        self.owner = owner
        self.task = task
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.owner.release(self.task)


class RequestOwner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiters: deque[_Waiter] = deque()
        self.active: dict[asyncio.Task, concurrent.futures.Future[None]] = {}
        self.in_use = 0
        self.closing = False

    async def acquire(self, deadline: float) -> Lease:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        waiter = _Waiter(loop, loop.create_future())
        with self.lock:
            if self.closing:
                raise RuntimeError('codex_request_owner_closing')
            if self.in_use < config.CODEX_MAX_CONCURRENCY and not self.waiters:
                self.in_use += 1
                waiter.granted = True
                waiter.future.set_result(None)
            else:
                self.waiters.append(waiter)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('codex_request_deadline')
            await asyncio.wait_for(waiter.future, turn_budget.remaining(remaining) or remaining)
            with self.lock:
                if self.closing:
                    raise asyncio.CancelledError
                if task is not None:
                    self.active[task] = concurrent.futures.Future()
            return Lease(self, task)
        except BaseException:
            with self.lock:
                if waiter in self.waiters:
                    self.waiters.remove(waiter)
                elif waiter.granted:
                    self._release_locked()
            raise

    def _release_locked(self) -> None:
        if self.closing:
            self.in_use -= 1
            return
        while self.waiters:
            waiter = self.waiters.popleft()
            if waiter.future.done():
                continue
            waiter.granted = True
            waiter.loop.call_soon_threadsafe(self._wake, waiter)
            return
        self.in_use -= 1

    @staticmethod
    def _wake(waiter: _Waiter) -> None:
        if not waiter.future.done():
            waiter.future.set_result(None)

    def release(self, task: asyncio.Task | None) -> None:
        with self.lock:
            completed = self.active.pop(task, None) if task is not None else None
            self._release_locked()
        if completed is not None:
            completed.set_result(None)

    async def shutdown(self) -> None:
        current = asyncio.current_task()
        with self.lock:
            self.closing = True
            tasks = [(task, done) for task, done in self.active.items() if task is not current]
            waiters = list(self.waiters)
        for task, _done in tasks:
            task.get_loop().call_soon_threadsafe(task.cancel)
        for waiter in waiters:
            waiter.loop.call_soon_threadsafe(waiter.future.cancel)
        if tasks:
            await asyncio.gather(*(asyncio.wrap_future(done) for _task, done in tasks))


OWNER = RequestOwner()


def deadline() -> float:
    return time.monotonic() + config.CODEX_TIMEOUT


def remaining(deadline_at: float) -> float:
    left = deadline_at - time.monotonic()
    if left <= 0:
        raise TimeoutError('codex_request_deadline')
    return turn_budget.remaining(left) or left
