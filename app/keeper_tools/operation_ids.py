"""Turn-owned ids for repeatable mutations, so a re-emitted call replays instead of doing the work twice.

The id is *allocated* by this module, one per distinct operation within a turn (``<kind>:<turn id>:<n>``); the model never
supplies it and it is not computed from any argument text. The operation's fingerprint (built by the caller from
server-resolved values) only tells a re-emission of the same operation from a new one. Without a turn id (a direct call
outside a player turn) there is no id and therefore no replay protection, as for the check cache.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from app import observability

_MAX_TURNS = 256
_LOCK = threading.Lock()
_TURNS: OrderedDict[str, dict[str, str]] = OrderedDict()


def allocate(kind: str, fingerprint: str) -> str | None:
    turn_id = str(observability.current_context().get("turn_id", "")).strip()
    if not turn_id:
        return None
    with _LOCK:
        operations = _TURNS.setdefault(turn_id, {})
        _TURNS.move_to_end(turn_id)
        while len(_TURNS) > _MAX_TURNS:
            _TURNS.popitem(last=False)
        if fingerprint not in operations:
            operations[fingerprint] = f"{kind}:{turn_id}:{len(operations) + 1}"
        return operations[fingerprint]
