"""One admission and deadline boundary for all Codex requests."""
from __future__ import annotations

import asyncio
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
        self.active: set[asyncio.Task] = set()
        self.in_use = 0

    async def acquire(self, deadline: float) -> Lease:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        waiter = _Waiter(loop, loop.create_future())
        with self.lock:
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
                if task is not None:
                    self.active.add(task)
            return Lease(self, task)
        except BaseException:
            with self.lock:
                if waiter in self.waiters:
                    self.waiters.remove(waiter)
                elif waiter.granted:
                    self._release_locked()
            raise

    def _release_locked(self) -> None:
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
            if task is not None:
                self.active.discard(task)
            self._release_locked()

    async def shutdown(self) -> None:
        current = asyncio.current_task()
        with self.lock:
            tasks = [task for task in self.active if task is not current]
            waiters = list(self.waiters)
        for task in tasks:
            task.get_loop().call_soon_threadsafe(task.cancel)
        for waiter in waiters:
            waiter.loop.call_soon_threadsafe(waiter.future.cancel)
        local = [task for task in tasks if task.get_loop() is asyncio.get_running_loop()]
        if local:
            await asyncio.gather(*local, return_exceptions=True)


OWNER = RequestOwner()


def deadline() -> float:
    return time.monotonic() + config.CODEX_TIMEOUT


def remaining(deadline_at: float) -> float:
    left = deadline_at - time.monotonic()
    if left <= 0:
        raise TimeoutError('codex_request_deadline')
    return turn_budget.remaining(left) or left
