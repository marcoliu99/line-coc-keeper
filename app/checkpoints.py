"""Per-group checkpoints and atomic rollback operations."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from app import db
from app import locks
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
    # The in-process lock protects callers sharing this Python process; the
    # immediate SQLite transaction makes event-id deduplication atomic for a
    # second worker/process using the same database.
    with locks.get_state_lock(state.group_id):
        with db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event_id:
                rows = conn.execute(
                    "SELECT data FROM state_checkpoints WHERE key LIKE ?",
                    (f"{state.group_id}:%",),
                ).fetchall()
                existing = next(
                    (json.loads(row[0]) for row in rows if json.loads(row[0]).get("event_id") == event_id),
                    None,
                )
                if existing is not None:
                    _logger.info(
                        "checkpoint_skipped_duplicate group_id=%s checkpoint_id=%s event_id=%s",
                        state.group_id, existing["checkpoint_id"], event_id,
                    )
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


def _get_checkpoint_tx(conn, group_id: str, identifier: str) -> dict:
    """Resolve a checkpoint while holding the rollback transaction."""
    direct = conn.execute(
        "SELECT data FROM state_checkpoints WHERE key = ?",
        (_checkpoint_key(group_id, identifier),),
    ).fetchone()
    if direct is not None:
        return json.loads(direct[0])
    rows = conn.execute(
        "SELECT data FROM state_checkpoints WHERE key LIKE ? ORDER BY updated_at, key",
        (f"{group_id}:%",),
    ).fetchall()
    matches = [json.loads(row[0]) for row in rows if json.loads(row[0]).get("label") == identifier]
    if not matches:
        raise KeyError(identifier)
    if len(matches) != 1:
        raise ValueError("checkpoint label is ambiguous; use its ID")
    return matches[0]


def clean_checkpoint(group_id: str, identifier: str) -> None:
    started = time.monotonic()
    with locks.get_state_lock(group_id):
        with db.transaction() as conn:
            checkpoint = _get_checkpoint_tx(conn, group_id, identifier)
            checkpoint_id = checkpoint["checkpoint_id"]
            db.delete_json_tx(conn, "state_checkpoints", _checkpoint_key(group_id, checkpoint_id))
    _logger.info(
        "checkpoint_clean_success group_id=%s checkpoint_id=%s duration_ms=%s",
        group_id, checkpoint_id, int((time.monotonic() - started) * 1000),
    )


def _restore_page_images(state: GroupState) -> None:
    """Rebuild the on-disk derived image cache for a restored scenario."""
    from app import scenario_library
    from app.repositories.group_state import clear_page_images, save_page_image

    clear_page_images(state.group_id)
    if not state.scenario_library_id:
        return
    try:
        context = scenario_library.load_context(
            state.scenario_library_id, state.active_chapter_id
        )
        scenario_library.copy_context_images(
            state.scenario_library_id,
            context["page_numbers"],
            lambda page, image: save_page_image(state.group_id, page, image),
        )
    except FileNotFoundError:
        _logger.warning(
            "rollback_image_restore_skipped group_id=%s scenario_id=%s reason=library_missing",
            state.group_id, state.scenario_library_id,
        )


def rollback(group_id: str, identifier: str, *, actor_id: str) -> tuple[GroupState, dict, dict]:
    """Atomically create pre-rollback, restore the checkpoint, and return both metadata records."""
    started = time.monotonic()
    # Conversation locks serialize Discord commands, while this synchronous
    # lock also coordinates with background maintenance and Keeper worker
    # threads that do not hold the asyncio conversation lock.
    with locks.get_state_lock(group_id):
        with db.transaction() as conn:
            checkpoint = _get_checkpoint_tx(conn, group_id, identifier)
            if checkpoint.get("group_id") != group_id:
                raise ValueError("checkpoint belongs to another group")
            state_data = checkpoint.get("state") or {}
            _validate_state_schema(state_data)
            restored = GroupState.from_dict(state_data)
            if restored.group_id != group_id:
                raise ValueError("checkpoint belongs to another group")
            restored.timeline_id = f"timeline-{uuid4().hex[:8]}"

            current_row = conn.execute(
                "SELECT data FROM group_states WHERE key = ?", (group_id,)
            ).fetchone()
            current = json.loads(current_row[0]) if current_row is not None else None
            _validate_state_schema(current or {"schema_version": 1})
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
            db.set_json_tx(conn, "state_checkpoints", _checkpoint_key(group_id, pre["checkpoint_id"]), pre)
            db.set_json_tx(conn, "group_states", group_id, restored_payload)
            restored_owner_ids = set(restored.characters)
            restored_character_ids = {char.character_id for char in restored.all_characters() if char.character_id}
            stale_rows = conn.execute("SELECT key, data FROM characters").fetchall()
            for owner_id, raw_entry in stale_rows:
                entry = json.loads(raw_entry)
                same_group = entry.get("conversation_id") == group_id
                character_id = entry.get("character_id") or entry.get("sheet", {}).get("character_id", "")
                is_stale = (
                    owner_id not in restored_owner_ids
                    if not character_id
                    else character_id not in restored_character_ids
                )
                if same_group and is_stale:
                    db.delete_json_tx(conn, "characters", owner_id)
            mirror_entries = {f"{group_id}:{owner_id}": char for owner_id, char in restored.characters.items()}
            mirror_entries.update({f"{group_id}:{char.character_id}": char for char in restored.all_characters() if char.character_id})
            for mirror_key, char in mirror_entries.items():
                db.set_json_tx(conn, "characters", mirror_key, {
                    "conversation_id": group_id,
                    "character_id": char.character_id,
                    "owner_id": char.owner_id,
                    "name": char.name,
                    "occupation": char.occupation,
                    "sheet": char.to_dict(),
                })
    _restore_page_images(restored)
    _logger.info(
        "rollback_success group_id=%s checkpoint_id=%s pre_rollback_id=%s revision=%s timeline_id=%s reason=rollback duration_ms=%s",
        group_id, checkpoint["checkpoint_id"], pre["checkpoint_id"], restored.state_revision,
        restored.timeline_id, int((time.monotonic() - started) * 1000),
    )
    return restored, checkpoint, pre
