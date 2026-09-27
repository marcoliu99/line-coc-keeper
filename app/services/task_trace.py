"""Per-request structural measurements from existing events; no prompt/user text.

Counts describe actual adapter requests/attempts, not an LLM accuracy score.
Discord send time and narration-ready time are deliberately separate.
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskTrace:
    started: float = field(default_factory=time.perf_counter)
    counters: Counter = field(default_factory=Counter)
    logical_requests: set[str] = field(default_factory=set)
    requests: list[dict[str, Any]] = field(default_factory=list)
    route: str = "unclassified"
    entry: str = "direct"
    stage: str = "context"
    narration_ready_ms: float | None = None
    controls_visible_ms: float | None = None
    dice_ready_ms: float | None = None
    fallback_ready_ms: float | None = None
    disposition: str | None = None
    queue_ms: float = 0
    lock: Any = field(default_factory=threading.RLock, repr=False)

    def record(self, name: str, fields: dict) -> None:
        with self.lock:
            elapsed = (time.perf_counter() - self.started) * 1000
            if name == "turn.entry":
                self.entry = fields.get("entry", "unknown")
            elif name == "turn.route":
                self.route = fields.get("route", "unclassified")
            elif name == "llm.turn.started":
                self.stage = fields.get("agent", "unknown")
                if self.stage == "guard":
                    self.counters["guard_calls"] += 1
            elif name == "llm.request.started":
                identity = fields.get("logical_request_id") or f"unidentified-{len(self.logical_requests)}"
                if identity not in self.logical_requests:
                    self.logical_requests.add(identity)
                    embedding = "embedding" in str(fields.get("api_operation", ""))
                    self.counters["embedding_requests" if embedding else "generation_requests"] += 1
                    if len(self.requests) < 200:
                        self.requests.append({key: fields.get(key) for key in
                                              ("provider", "model", "reasoning_effort", "iteration")}
                                             | {"stage": self.stage, "at_ms": round(elapsed, 3)})
            elif name == "embedding.batch.started":
                self.counters["embedding_requests"] += 1
            elif name == "llm.wrapup":
                self.counters["wrapup_requests"] += 1
            elif name == "llm.request.attempt.started":
                self.counters["provider_attempts"] += 1
                self.queue_ms += float(fields.get("admission_wait_s", 0)) * 1000
            elif name == "llm.retry":
                self.counters["retry_events"] += 1
            elif name == "llm.tool_round":
                self.counters[f"{self.stage}_tool_response_rounds"] += 1
            elif name == "llm.tool.completed":
                self.counters["tools_completed"] += 1
            elif name in {"rag.search.started", "scenario.search.started", "memory.search.started"}:
                self.counters["retrieval_calls"] += 1
            elif name == "lock.wait.completed":
                self.queue_ms += float(fields.get("duration_ms", 0))
            elif name == "turn.observed" and fields.get("dice_rolled"):
                self.counters["dice_results"] += 1
                self.dice_ready_ms = elapsed
            elif name == "executor.resolution":
                self.disposition = fields.get("disposition")
            elif name == "turn.delivery_contract":
                if fields.get("narrative_complete", True) and fields.get("status") == "passed":
                    self.narration_ready_ms = elapsed
                else:
                    self.fallback_ready_ms = elapsed
                self.counters["required_decisions"] = fields.get("control_count", 0)
                self.counters["delivery_fallbacks"] += int(fields.get("status") == "projected_fallback")
                self.counters["delivery_blocked"] += int(fields.get("status") == "blocked")
            elif name == "pending_button.send.completed" and fields.get("status") == "success":
                self.counters["controls_sent"] += 1
                self.controls_visible_ms = elapsed
            elif name == "pending_button.send.failed":
                self.counters["controls_failed"] += 1

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "route": self.route,
                "entry": self.entry,
                "counts": dict(self.counters),
                "requests": list(self.requests),
                "queue_ms": round(self.queue_ms, 3),
                "dice_ready_ms": self.dice_ready_ms,
                "narration_ready_ms": self.narration_ready_ms,
                "fallback_ready_ms": self.fallback_ready_ms,
                "disposition": self.disposition,
                "controls_visible_ms": self.controls_visible_ms,
                "request_elapsed_ms": (time.perf_counter() - self.started) * 1000,
                "mechanical_accuracy": None,
                "manual_interventions": None,
                "measurement": "runtime_structure_only",
            }


_current: ContextVar[TaskTrace | None] = ContextVar("task_trace", default=None)


def active() -> bool:
    return _current.get() is not None


def record(name: str, fields: dict) -> None:
    trace = _current.get()
    if trace is not None and name != "task.trace":
        trace.record(name, fields)


@contextmanager
def capture():
    trace = TaskTrace()
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)
