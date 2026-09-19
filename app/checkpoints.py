"""Per-group checkpoints and atomic rollback operations."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from app import db
from app.models import GroupState

_logger = logging.getLogger(__name__)


def _checkpoint_key(group_id: str, checkpoint_id: str) -> str:
    return f"{group_id}:{checkpoint_id}"


def _checkpoint_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{uuid4().hex[:4]}"


def _validate_state_schema(data: dict) -> None:
    version = int(data.get("schema_version", 1))
    if version > GroupState.CURRENT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported checkpoint schema_version={version}")


def create_checkpoint(
    state: GroupState,
    *,
    label: str = "",
    created_by: str = "system",
    reason: str = "manual",
    event_id: str = "",
) -> dict:
    started = time.monotonic()
    if event_id:
        existing = next((item for item in list_checkpoints(state.group_id) if item.get("event_id") == event_id), None)
        if existing is not None:
            _logger.info("checkpoint_skipped_duplicate group_id=%s checkpoint_id=%s event_id=%s", state.group_id, existing["checkpoint_id"], event_id)
            return existing
    checkpoint_id = _checkpoint_id()
    payload = state.to_dict()
    entry = {
        "group_id": state.group_id,
        "checkpoint_id": checkpoint_id,
        "label": label or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        "event_id": event_id,
        "timeline_id": payload["timeline_id"],
        "state_revision": payload.get("state_revision", 0),
        "schema_version": payload.get("schema_version", 1),
        "state": payload,
    }
    with db.transaction() as conn:
        db.set_json_tx(conn, "state_checkpoints", _checkpoint_key(state.group_id, checkpoint_id), entry)
    _logger.info(
        "checkpoint_success group_id=%s checkpoint_id=%s reason=%s revision=%s timeline_id=%s duration_ms=%s",
        state.group_id, checkpoint_id, reason, entry["state_revision"], entry["timeline_id"],
        int((time.monotonic() - started) * 1000),
    )
    return entry


def list_checkpoints(group_id: str) -> list[dict]:
    return [value for _, value in db.list_json("state_checkpoints", prefix=f"{group_id}:")]


def get_checkpoint(group_id: str, identifier: str) -> dict:
    direct = db.get_json("state_checkpoints", _checkpoint_key(group_id, identifier))
    if direct is not None:
        return direct
    matches = [c for c in list_checkpoints(group_id) if c.get("label") == identifier]
    if len(matches) != 1:
        if not matches:
            raise KeyError(identifier)
        raise ValueError("checkpoint label is ambiguous; use its ID")
    return matches[0]


def clean_checkpoint(group_id: str, checkpoint_id: str) -> None:
    started = time.monotonic()
    if db.get_json("state_checkpoints", _checkpoint_key(group_id, checkpoint_id)) is None:
        raise KeyError(checkpoint_id)
    with db.transaction() as conn:
        db.delete_json_tx(conn, "state_checkpoints", _checkpoint_key(group_id, checkpoint_id))
    _logger.info(
        "checkpoint_clean_success group_id=%s checkpoint_id=%s duration_ms=%s",
        group_id, checkpoint_id, int((time.monotonic() - started) * 1000),
    )


def rollback(group_id: str, identifier: str, *, actor_id: str) -> tuple[GroupState, dict, dict]:
    """Atomically create pre-rollback, restore the checkpoint, and return both metadata records."""
    started = time.monotonic()
    checkpoint = get_checkpoint(group_id, identifier)
    state_data = checkpoint.get("state") or {}
    _validate_state_schema(state_data)
    restored = GroupState.from_dict(state_data)
    if restored.group_id != group_id:
        raise ValueError("checkpoint belongs to another group")
    restored.timeline_id = f"timeline-{uuid4().hex[:8]}"

    current = db.get_json("group_states", group_id)
    current_state = GroupState.from_dict(current or {"group_id": group_id})
    pre = {
        "group_id": group_id,
        "checkpoint_id": _checkpoint_id(),
        "label": "pre-rollback",
        "created_by": actor_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": "pre_rollback",
        "event_id": "",
        "timeline_id": current_state.timeline_id or f"legacy-{group_id}",
        "state_revision": current_state.state_revision,
        "schema_version": current_state.schema_version,
        "state": current_state.to_dict(),
    }
    restored.state_revision = current_state.state_revision + 1
    restored_payload = restored.to_dict()

    with db.transaction() as conn:
        db.set_json_tx(conn, "state_checkpoints", _checkpoint_key(group_id, pre["checkpoint_id"]), pre)
        db.set_json_tx(conn, "group_states", group_id, restored_payload)
        for owner_id, char in restored.characters.items():
            db.set_json_tx(conn, "characters", owner_id, {
                "conversation_id": group_id,
                "name": char.name,
                "occupation": char.occupation,
                "sheet": char.to_dict(),
            })
    _logger.info(
        "rollback_success group_id=%s checkpoint_id=%s pre_rollback_id=%s revision=%s timeline_id=%s duration_ms=%s",
        group_id, checkpoint["checkpoint_id"], pre["checkpoint_id"], restored.state_revision,
        restored.timeline_id, int((time.monotonic() - started) * 1000),
    )
    return restored, checkpoint, pre
