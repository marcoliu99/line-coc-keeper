"""Shadow routing: ask a local Laya sidecar how it would route a player's line, and log the answer.

Nothing here changes a turn. The guess runs beside the Executor and lands in the runtime log as a `laya.shadow`
event carrying the turn's id, so scripts/experiments/laya_router_eval can score it against the tools the Executor
called in the same turn. Off unless LAYA_SHADOW_URL is set.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Any

from app import config, observability

_PENDING: set[asyncio.Task[None]] = set()  # fire-and-forget tasks are kept alive until they finish
_TEXT_LIMIT = 300


def start(text: str, *, character: str, in_combat: bool) -> asyncio.Task[None] | None:
    """Begin the guess for one player line; the turn does not wait for it."""
    if not config.LAYA_SHADOW_URL or not text.strip():
        return None
    task = asyncio.create_task(_ask(text.strip()[:_TEXT_LIMIT], character=character, in_combat=in_combat))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


def _post(state: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        config.LAYA_SHADOW_URL, data=json.dumps({"state": state}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.LAYA_SHADOW_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


async def _ask(text: str, *, character: str, in_combat: bool) -> None:
    started = time.perf_counter()
    state = {"玩家行動": text, "角色": character, "戰鬥中": in_combat}
    try:
        answer = await asyncio.wait_for(asyncio.to_thread(_post, state), config.LAYA_SHADOW_TIMEOUT)
    # A down, slow or malformed sidecar is a missing data point, never a broken turn.
    except (OSError, TimeoutError, ValueError) as error:
        observability.event("laya.shadow", status="error", error_type=type(error).__name__,
                            duration_ms=(time.perf_counter() - started) * 1000)
        return
    observability.event(
        "laya.shadow", status="success", duration_ms=(time.perf_counter() - started) * 1000,
        model_ms=answer.get("ms"), route=answer.get("route"), probabilities=answer.get("probabilities"),
        needs=answer.get("needs"), in_combat=in_combat, text=text,
    )
