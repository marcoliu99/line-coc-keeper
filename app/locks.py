"""Per-group locks so two LINE messages for the same group can't race on
data/groups/*.json.

app/state.py does a full read-modify-write (load_state -> mutate -> save_state)
with no locking of its own. Without serialization, two webhook events for the
same group arriving close together can each load the same on-disk snapshot,
mutate their own in-memory copy, and save — the second save silently discards
whatever the first one wrote (e.g. an HP/SAN change from a skill check that
happened "at the same time" as another player's).

The fix here is coarse but correct: one asyncio.Lock per group_id, held for the
entire duration of handling one message (from the initial load_state through
every save_state it triggers, including while awaiting a slow Keeper LLM call).
Different groups still run fully concurrently — only messages within the same
group queue up behind each other.
"""
from __future__ import annotations

import asyncio

_locks: dict[str, asyncio.Lock] = {}


def get_group_lock(group_id: str) -> asyncio.Lock:
    lock = _locks.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[group_id] = lock
    return lock
