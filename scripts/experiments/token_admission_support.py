"""Isolated evaluation helpers; never imported by the application runtime."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path


def recent_history(history, user_turns=5):
    starts = [i for i, item in enumerate(history) if item.get('role') == 'user']
    return history[starts[-user_turns]:] if len(starts) > user_turns else history


class WindowBudget:
    """Single sequential trial process ledger, shared across case subprocesses.

    Reservations age out on time, never when an HTTP response completes.
    Not a production multi-process/atomic limiter.
    """
    def __init__(self, path, limit=180000, window=60):
        self.path = Path(path)
        self.limit = limit
        self.window = window

    def reserve_or_delay(self, tokens, now):
        if tokens > self.limit:
            raise ValueError('request exceeds trial token budget')
        entries = json.loads(self.path.read_text()) if self.path.exists() else []
        entries = [e for e in entries if now - e[0] < self.window]
        total = sum(e[1] for e in entries)
        if total + tokens <= self.limit:
            entries.append([now, tokens])
            self.path.write_text(json.dumps(entries))
            return 0.0
        for timestamp, amount in entries:
            total -= amount
            if total + tokens <= self.limit:
                return max(0.001, timestamp + self.window - now)
        raise RuntimeError('invalid budget ledger')

    async def acquire(self, tokens, deadline):
        waited = 0.0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('trial admission deadline exceeded')
            delay = self.reserve_or_delay(tokens, time.time())
            if not delay:
                return waited
            if time.monotonic() + delay >= deadline:
                raise TimeoutError('trial admission wait would exceed deadline')
            started = time.monotonic()
            await asyncio.sleep(delay)
            waited += time.monotonic() - started


class InputMeter:
    def __init__(self, encoding):
        self.encoding = encoding
        self.conversation = None
        self.responses = {}

    def count(self, obj):
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, separators=(',', ':'), default=str)
        return len(self.encoding.encode(text, disallowed_special=()))

    def begin(self, static, dynamic, history, message):
        self.conversation = (static, dynamic, history, message)

    def measure(self, kwargs):
        static, dynamic, history, message = self.conversation
        parts = {'static': self.count(static), 'dynamic': self.count(dynamic),
                 'tools': self.count(kwargs.get('tools', [])), 'history': 0, 'current': 0,
                 'tool_results': 0, 'prior_outputs': 0}
        previous = kwargs.get('previous_response_id')
        if previous:
            if previous not in self.responses:
                raise ValueError('unknown inherited context in trial')
            parts.update(self.responses[previous])
            parts['tool_results'] += self.count(kwargs.get('input', []))
        else:
            parts['history'] = self.count(history)
            parts['current'] = self.count(message)
        return parts

    def record(self, response, parts):
        inherited = {k: parts[k] for k in ('history', 'current', 'tool_results', 'prior_outputs')}
        inherited['prior_outputs'] += self.count([x.model_dump() for x in response.output])
        self.responses[response.id] = inherited
