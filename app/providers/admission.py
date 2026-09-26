"""Header-informed, process-local OpenAI admission. No guessed TPM window."""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from app import config, observability
from app.providers import turn_budget


def seconds(value: Any) -> float | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    parts = re.findall(r'([0-9]+(?:\.[0-9]+)?)(ms|s|m|h|d)', value)
    if not parts or ''.join(n + u for n, u in parts) != value:
        return None
    result = sum(float(n) * {'ms': .001, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}[u] for n, u in parts)
    return result if math.isfinite(result) and result > 0 else None


@dataclass
class Quota:
    limit: float
    remaining: float
    at: float
    reset: float

    def available(self, now: float) -> float:
        if now >= self.reset:
            return self.limit
        rate = (self.limit - self.remaining) / max(.001, self.reset - self.at)
        return min(self.limit, self.remaining + max(0, now - self.at) * rate)

    def wait(self, cost: float, now: float) -> float:
        if cost > self.limit:
            raise ValueError('Request estimate exceeds observed rate limit')
        available = self.available(now)
        if available >= cost:
            return 0
        rate = (self.limit - available) / max(.001, self.reset - now)
        return (cost - available) / rate if rate > 0 else max(0, self.reset - now)


class Admission:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.quotas: dict[str, Quota] = {}
        self.cooldown = 0.0

    def observe(self, headers) -> None:
        if headers is None or not hasattr(headers, 'items'):
            return
        fields = {str(k).lower(): v for k, v in headers.items()}
        now = time.monotonic()
        with self.lock:
            for dimension in ('requests', 'tokens'):
                try:
                    limit = float(fields[f'x-ratelimit-limit-{dimension}'])
                    remaining = float(fields[f'x-ratelimit-remaining-{dimension}'])
                except (KeyError, ValueError, TypeError):
                    continue
                reset = seconds(fields.get(f'x-ratelimit-reset-{dimension}'))
                if not reset or not all(math.isfinite(v) for v in (limit, remaining)) or limit <= 0 or remaining < 0:
                    continue
                remaining = min(limit, remaining)
                server_remaining = remaining
                old = self.quotas.get(dimension)
                if old and now < old.reset:
                    remaining = min(remaining, old.available(now))
                rate = (limit - server_remaining) / reset
                if rate <= 0:
                    rate = (old.limit - old.remaining) / max(.001, old.reset - old.at) if old else limit / 60
                rate = max(rate, limit / 86400)
                self.quotas[dimension] = Quota(limit, remaining, now, now + (limit - remaining) / rate)

    def defer(self, delay: float) -> None:
        with self.lock:
            self.cooldown = max(self.cooldown, time.monotonic() + delay)

    def delay(self, tokens: int | None, *, reserve: bool = False) -> float:
        now = time.monotonic()
        with self.lock:
            costs = {'requests': 1, **({'tokens': tokens} if tokens is not None else {})}
            delay = max(0.0, self.cooldown - now)
            for dimension, cost in costs.items():
                quota = self.quotas.get(dimension)
                if quota:
                    delay = max(delay, quota.wait(cost, now))
            if reserve and delay <= 0:
                for dimension, cost in costs.items():
                    quota = self.quotas.get(dimension)
                    if quota:
                        available = quota.available(now)
                        # Keep a recovery rate after locally reserving a slot,
                        # even when the last server snapshot had reset already.
                        rate = (quota.limit - quota.remaining) / max(.001, quota.reset - quota.at)
                        rate = max(rate, quota.limit / 60) if rate <= 0 else rate
                        reset = now + (quota.limit - available + cost) / rate
                        self.quotas[dimension] = Quota(quota.limit, available - cost, now, reset)
            return delay

    async def wait(self, tokens: int | None) -> None:
        started = time.monotonic()
        while delay := self.delay(tokens):
            await turn_budget.sleep(delay)
        observability.event('llm.admission.ready', wait_s=time.monotonic() - started,
                            tokens_estimate=tokens, inherited_context_unknown=tokens is None)


_controllers: dict[str, Admission] = {}
_controllers_lock = threading.Lock()


def controller(model: str) -> Admission | None:
    if not config.OPENAI_ADAPTIVE_ADMISSION_ENABLED:
        return None
    scope = config.OPENAI_RATE_LIMIT_SCOPE or model
    with _controllers_lock:
        return _controllers.setdefault(scope, Admission())


async def observe_http_response(response) -> None:
    # This client is configured for one API project. Scope names can combine
    # models sharing a limit pool; do not log project IDs or credentials.
    active = controller(config.OPENAI_MODEL)
    if active:
        active.observe(response.headers)
