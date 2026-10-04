"""Recording a settled check: the audit event and its consequence plan.

One implementation for every path that settles a check (Keeper autoroll, a
player's roll, a Luck decision). A settled check is recorded under the action id
``check-event:<event_id>``, so a continuation or narration retry that reaches
this point again records nothing a second time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app import observability, resolved_check_consequences
from app.check_identity import new_check_id
from app.keeper_tools import resource_bridge
from app.models import Character, GroupState
from app.repositories import state_transaction

ATTRIBUTE_NAMES = {"hp": "HP", "san": "SAN", "mp": "MP", "luck": "Luck"}
_HISTORY_LIMIT = 20


def character_attribute_snapshot(char: Character) -> dict[str, int]:
    return {name: int(getattr(char, name)) for name in ATTRIBUTE_NAMES}


def event_seed(
    *, check_id: str, timeline_id: str, owner_id: str, character_id: str, investigator: str,
    skill: str, skill_value: int, roll: int, difficulty: str, outcome: str,
    before: dict[str, int], tracked_roll_fields: tuple[str, ...] | None = None,
    check_context: dict | None = None, opposed_outcome: dict | None = None,
    success: bool | None = None, include_medical_context: bool = True, detail: bool = True,
    caused_by_check_id: str = "",
) -> dict[str, Any]:
    """What the settlement knew, before the attribute changes it caused are measured.

    ``tracked_roll_fields`` names attributes the roll itself changed (SAN, Luck);
    the keys it adds are left out when ``None``. ``detail=False`` keeps only the
    identity, the roll and the outcome (a SAN check has no consequence plan,
    opposed result or declaration to carry). ``caused_by_check_id`` links a check that
    a settled one made necessary (the INT check after a 5-point SAN loss, or a
    triggered Dodge, whose pending entry names its ``source_check_id``) back to it.
    """
    context = check_context or {}
    seed: dict[str, Any] = {
        "event_id": check_id or new_check_id(),
        "check_id": check_id,
        "timeline_id": timeline_id,
        "owner_id": owner_id,
        "character_id": character_id,
        "investigator": investigator,
        "skill": skill,
        "skill_value": int(skill_value),
        "roll": int(roll),
        "difficulty": str(difficulty),
        "outcome": outcome,
        "state_before": dict(before),
    }
    if detail:
        seed.update({
            "success": success,
            "consequences": context.get("consequences", []),
            "opposed_outcome": opposed_outcome,
            "player_declaration": context.get("player_declaration", ""),
            "action_basis": context.get("action_basis", ""),
        })
    cause = caused_by_check_id or str(context.get("source_check_id") or "")
    if cause:
        seed["caused_by_check_id"] = cause
    if tracked_roll_fields is not None:
        seed["tracked_roll_fields"] = list(tracked_roll_fields)
    if include_medical_context:
        seed["medical_context"] = dict(context.get("medical_context") or {})
    return seed


def _record(ctx: state_transaction.TxContext, seed: dict[str, Any], *, with_origin: bool) -> None:
    latest: GroupState = ctx.state
    current_timeline_id = latest.timeline_id or f"legacy-{latest.group_id}"
    if current_timeline_id != seed["timeline_id"]:
        observability.event(
            "check.event.stale", level=logging.INFO, reason="timeline_mismatch",
            check_id=seed.get("check_id") or None,
        )
        ctx.skip_save()
        return
    if any(
        event.get("event_id") == seed["event_id"]
        for event in latest.resolved_check_events
        if isinstance(event, dict)
    ):
        ctx.skip_save()
        return
    char = latest.get_active_character(seed["owner_id"])
    if char is None or char.name != seed["investigator"] or char.character_id != seed["character_id"]:
        ctx.skip_save()
        return
    if with_origin:
        resolved_check_consequences.persist_origin(latest, seed)
    after = character_attribute_snapshot(char)
    effects = [
        {"field": ATTRIBUTE_NAMES[field], "before": before, "after": after[field], "delta": after[field] - before}
        for field, before in seed["state_before"].items()
        if before != after[field]
    ]
    event = {key: value for key, value in seed.items() if key != "state_before"}
    if resource_bridge.participating(latest, char) or seed.get("provisional"):
        event["provisional"] = True
        event["combat_id"] = seed.get("combat_id") or latest.combat.combat_id
        effective_after = character_attribute_snapshot(resource_bridge.effective(latest, char))
        event["provisional_state_effects"] = [
            {"field": ATTRIBUTE_NAMES[field], "before": before, "after": effective_after[field],
             "delta": effective_after[field] - before}
            for field, before in seed["state_before"].items() if before != effective_after[field]
        ]
        effects = []
    event["state_effects"] = effects
    event.pop("tracked_roll_fields", None)
    event["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    latest.resolved_check_events.append(event)
    del latest.resolved_check_events[:-_HISTORY_LIMIT]
    ctx.stage_event(
        "check_resolved", event_id=str(seed["event_id"]), causation_id=str(seed.get("check_id") or ""),
    )


def persist_resolved_event(
    conversation_id: str, seed: dict[str, Any], *, with_origin: bool = False,
    snapshot: GroupState | None = None,
) -> None:
    """Record the settled check and only the attribute changes present in committed state.

    ``with_origin`` also publishes the consequence plan in the same commit (a
    Keeper autoroll has nothing between settlement and this call). A player's
    roll publishes it earlier through :func:`persist_consequence_origin`, so the
    follow-up turn can already act on it. ``snapshot`` is refreshed afterwards.
    """
    event_id = str(seed["event_id"])
    options: dict[str, Any] = {
        "reason": "resolved_check_event",
        "action_id": f"check-event:{event_id}",
        "request_fingerprint": state_transaction.request_fingerprint({
            "event_id": event_id, "timeline_id": seed["timeline_id"],
            "owner_id": seed["owner_id"], "character_id": seed["character_id"],
        }),
    }

    def record(ctx: state_transaction.TxContext) -> None:
        _record(ctx, seed, with_origin=with_origin)

    if snapshot is not None:
        state_transaction.commit_for_snapshot(snapshot, record, expected_timeline=None, **options)
    else:
        state_transaction.mutate(conversation_id, record, **options)


def persist_consequence_origin(conversation_id: str, seed: dict[str, Any]) -> None:
    """Publish the settled check's source-bound plan before its follow-up turn."""
    def publish(ctx: state_transaction.TxContext) -> None:
        if not resolved_check_consequences.persist_origin(ctx.state, seed):
            ctx.skip_save()

    state_transaction.mutate(conversation_id, publish, reason="resolved_check_consequence_origin")
