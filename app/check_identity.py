"""Stable identities for persisted player decisions.

Pending checks and Luck decisions are persisted inside the GroupState blob and
also represented by Discord buttons.  The button can outlive the state entry
that created it, so callbacks must compare an identity rather than only an
owner id and an option label.  Older snapshots do not have an identity; for
those entries we derive a deterministic legacy id instead of generating a new
random value on every load.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from app.models import GroupState

PendingCheckBlocker = Literal["pending_check", "pending_luck_decision"]


def pending_check_blocker(state: GroupState, owner_id: str) -> PendingCheckBlocker | None:
    """Why ``owner_id`` can't be given a new pending check right now, if anything.

    A player holds at most one pending check, and none while a Luck decision
    is still open (docs/specs/bug/bugfix_duplicate_pending_checks.md). Every
    path that registers a check asks this, so the two stay the same rule.
    """
    if owner_id in state.pending_checks:
        return "pending_check"
    if owner_id in state.pending_luck_decisions:
        return "pending_luck_decision"
    return None


def new_check_id() -> str:
    return f"check-{uuid4().hex}"


def new_decision_id() -> str:
    return f"decision-{uuid4().hex}"


def _legacy_id(prefix: str, owner_id: str, timeline_id: str, value: dict[str, Any]) -> str:
    # "_buttons_posted" (app/discord_transport/controls.py's duplicate-post guard) is
    # internal bookkeeping, not part of the check/decision's own identity —
    # excluding it here matters specifically for legacy entries with no
    # explicit check_id/decision_id (persisted pre-identity checks, and the
    # opening-scene checks app/commands/handlers/system.py registers
    # without one): without this exclusion, a button's embedded identity
    # token (computed from the entry BEFORE it's marked posted) would stop
    # matching the identity recomputed from the persisted entry (marked
    # AFTER) the moment a callback re-derives it, and the button would be
    # rejected as expired on the very first click.
    stable_value = {
        key: item for key, item in value.items()
        if key not in {"check_id", "decision_id", "_buttons_posted"}
    }
    encoded = json.dumps(stable_value, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{prefix}|{owner_id}|{timeline_id}|{encoded}".encode()).hexdigest()[:24]
    return f"legacy-{prefix}-{digest}"


def effective_check_id(owner_id: str, pending: dict[str, Any], timeline_id: str) -> str:
    explicit = str(pending.get("check_id", "")).strip()
    return explicit or _legacy_id("check", owner_id, timeline_id, pending)


def effective_decision_id(owner_id: str, decision: dict[str, Any], timeline_id: str) -> str:
    explicit = str(decision.get("decision_id", "")).strip()
    return explicit or _legacy_id("decision", owner_id, timeline_id, decision)


def compact_identity_token(kind: str, owner_id: str, identity: str, timeline_id: str) -> str:
    """Return a short transport token for a Discord component custom_id.

    Discord limits component ``custom_id`` values to 100 characters.  Full
    persisted check/decision IDs plus a Discord channel and owner ID can
    exceed that limit, especially for legacy IDs.  This token is only a
    transport representation: callbacks still load the current GroupState
    and compare the token against the authoritative persisted identity and
    timeline.  It must therefore never be used as the persisted ID itself.
    """
    if kind not in {"check", "decision"}:
        raise ValueError(f"unsupported identity kind: {kind!r}")
    prefix = "c" if kind == "check" else "d"
    digest = hashlib.sha256(
        f"{kind}|{owner_id}|{timeline_id}|{identity}".encode()
    ).hexdigest()[:12]
    return f"{prefix}{digest}"
