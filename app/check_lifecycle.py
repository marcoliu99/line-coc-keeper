"""Admission and identity for newly registered player-owned checks.

Call only while holding the conversation state lock, against the freshly loaded
GroupState. The caller owns the encompassing state transaction and game rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app import observability
from app.check_identity import (
    PendingCheckBlocker,
    effective_check_id,
    new_check_id,
    pending_check_blocker,
)
from app.models import GroupState
from app.services import opposed_checks

RegistrationStatus = Literal["admitted", "identical", "blocked"]
DuplicatePolicy = Literal["none", "identical", "npc_melee"]


@dataclass(frozen=True)
class CheckRegistration:
    status: RegistrationStatus
    pending: dict[str, Any] | None = None
    blocker: PendingCheckBlocker | None = None
    check_id: str = ""
    timeline_id: str = ""

    @property
    def should_save(self) -> bool:
        return self.status == "admitted"


def _same_check(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if existing.get("type") != candidate.get("type"):
        return False
    if candidate.get("type") == "skill":
        fields = ("skill", "skill_value", "bonus_dice", "penalty_dice", "difficulty", "pushed")
        return (
            all(existing.get(key) == candidate.get(key) for key in fields)
            and opposed_checks.request_part(existing.get("opposed"))
            == opposed_checks.request_part(candidate.get("opposed"))
            and existing.get("action_basis", "") == candidate.get("action_basis", "")
        )
    if candidate.get("type") == "sanity":
        return all(existing.get(key) == candidate.get(key) for key in ("loss_success", "loss_failure"))
    if candidate.get("type") == "choice":
        return sorted(str(option) for option in existing.get("options", [])) == sorted(
            str(option) for option in candidate.get("options", [])
        )
    return False


def _same_npc_melee(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if existing.get("type") != "choice" or existing.get("attacker_roll") is None:
        return False
    fields = ("attacker_skill_value", "attacker_bonus_dice", "attacker_penalty_dice", "is_ranged")
    return (
        all(existing.get(key, 0) == candidate.get(key, 0) for key in fields)
        and existing.get("raw_option_labels") == candidate.get("raw_option_labels")
    )


def admit(
    state: GroupState,
    owner_id: str,
    candidate: dict[str, Any] | None = None,
    *,
    duplicate: DuplicatePolicy = "none",
) -> CheckRegistration:
    """Decide once against fresh state, before any roll or state mutation."""
    timeline_id = state.timeline_id or f"legacy-{state.group_id}"
    # Luck takes precedence even if corrupt older state contains both entries.
    if owner_id in state.pending_luck_decisions:
        return CheckRegistration("blocked", blocker="pending_luck_decision", timeline_id=timeline_id)
    existing = state.pending_checks.get(owner_id)
    if existing is not None:
        same = candidate is not None and (
            (duplicate == "identical" and _same_check(existing, candidate))
            or (duplicate == "npc_melee" and _same_npc_melee(existing, candidate))
        )
        if same:
            return CheckRegistration(
                "identical", pending=existing,
                check_id=effective_check_id(owner_id, existing, timeline_id), timeline_id=timeline_id,
            )
        return CheckRegistration("blocked", blocker="pending_check", timeline_id=timeline_id)
    return CheckRegistration("admitted", timeline_id=timeline_id)


def metadata(state: GroupState, owner_id: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create metadata only after admission; generating it may initialize a timeline."""
    source = source or {}
    if not state.timeline_id:
        state.timeline_id = f"timeline-{uuid4().hex[:8]}"
    context = str(source.get("action_context", "")).strip()
    if len(context) > 240:
        context = context[:237] + "..."
    current = observability.current_context()
    return {
        "check_id": new_check_id(),
        "timeline_id": state.timeline_id,
        "origin_revision": state.state_revision + 1,
        "origin_turn_id": str(current.get("turn_id", "")),
        "origin_request_id": str(current.get("request_id", "")),
        "action_context": context,
        "player_declaration": str(source.get("_player_action", ""))[:1000],
        "action_basis": str(source.get("action_basis", ""))[:600],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def register(
    state: GroupState,
    owner_id: str,
    candidate: dict[str, Any],
    *,
    duplicate: DuplicatePolicy = "none",
    source: dict[str, Any] | None = None,
) -> CheckRegistration:
    """Admit and persist a pending entry in the caller's existing transaction."""
    decision = admit(state, owner_id, candidate, duplicate=duplicate)
    if not decision.should_save:
        return decision
    entry = dict(candidate)
    entry.update(metadata(state, owner_id, source))
    state.pending_checks[owner_id] = entry
    return CheckRegistration(
        "admitted", pending=entry, check_id=entry["check_id"], timeline_id=entry["timeline_id"]
    )


def blocker(state: GroupState, owner_id: str) -> PendingCheckBlocker | None:
    """Return a blocker for a required new check before its parent mutation."""
    return pending_check_blocker(state, owner_id)


def reusable_cached_result(
    state: GroupState, owner_id: str, cached: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Return a retry result only when its persisted decision still belongs to it."""
    if cached is None or cached.get("timeline_id") != state.timeline_id:
        return None
    if owner_id in state.pending_checks:
        return None
    decision = state.pending_luck_decisions.get(owner_id)
    if decision is not None and (
        not cached.get("pending_luck") or decision.get("check_id") != cached.get("check_id")
    ):
        return None
    if decision is None and cached.get("pending_luck"):
        return None
    return cached


def register_many(
    state: GroupState, candidates: dict[str, dict[str, Any]], *, source: dict[str, Any] | None = None
) -> dict[str, CheckRegistration]:
    """Register a scripted group check all-or-nothing in one state transaction."""
    blocked = {
        owner_id: decision
        for owner_id, candidate in candidates.items()
        if (decision := admit(state, owner_id, candidate)).status == "blocked"
    }
    if blocked:
        return blocked
    return {
        owner_id: register(state, owner_id, candidate, source=source)
        for owner_id, candidate in candidates.items()
    }
