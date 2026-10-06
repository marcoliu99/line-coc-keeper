"""Turn-owned ids for repeatable mutations, so a re-emitted call replays instead of doing the work twice.

The id is derived, not stored: ``<kind>:<turn id>:<digest of the operation fingerprint>``. The turn id comes from the
turn context and the fingerprint is built by the caller from server-resolved values, so the same operation in the same
turn always gets the same id, whatever the process has cached, and a refused call consumes nothing. The model never
supplies any part of it. Without a turn id (a direct call outside a player turn) there is no id and therefore no replay
protection, as for the check cache.
"""
from __future__ import annotations

from hashlib import sha256

from app import observability


def allocate(kind: str, fingerprint: str) -> str | None:
    turn_id = str(observability.current_context().get("turn_id", "")).strip()
    if not turn_id:
        return None
    return f"{kind}:{turn_id}:{sha256(fingerprint.encode()).hexdigest()[:12]}"
