"""Per-group game state persistence — SQLite-backed (see app/db.py) — plus
on-disk storage for scenario page images (kept as separate binary files
rather than inline base64 in a database blob, which would bloat every
read/write of state that doesn't even touch images).

Used to be one flat JSON file per group_id under data/groups/*.json; migrated
to SQLite for atomic writes and a single file to back up (see app/db.py's
docstring for the full rationale). Existing data/groups/*.json files from
before this migration are NOT read by this module anymore — see
scripts/migrate_json_to_sqlite.py for the one-time import that moved them
into the database.
"""
from __future__ import annotations

import json
import re
import shutil
import logging
import time
from uuid import uuid4
from pathlib import Path

from app import db
from app.config import DATA_DIR
from app.models import GroupState

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_logger = logging.getLogger(__name__)


def _safe_id(group_id: str) -> str:
    """Still used for filesystem paths (page-image directories below) — a
    group_id is only ever a LINE group id or a "discord-channel-<int>"
    string in practice, both already filesystem-safe, but this stays as a
    defensive sanitizer for that path. Not used for the SQLite key itself
    (see load_state/save_state) — a TEXT primary key has no filesystem-style
    character restrictions, so the raw group_id is used there directly."""
    return _SAFE_ID_RE.sub("_", group_id)


def load_state(group_id: str) -> GroupState:
    data = db.get_json("group_states", group_id)
    if data is None:
        return GroupState(group_id=group_id)
    state = GroupState.from_dict(data)
    if state.schema_version > GroupState.CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported GroupState schema_version={state.schema_version}; "
            f"current={GroupState.CURRENT_SCHEMA_VERSION}"
        )
    return state


def save_state(state: GroupState, *, reason: str = "command") -> None:
    started = time.monotonic()
    if not state.timeline_id:
        state.timeline_id = f"timeline-{uuid4().hex[:8]}"
    next_revision = state.state_revision + 1
    payload = state.to_dict()
    payload["state_revision"] = next_revision
    payload["timeline_id"] = state.timeline_id
    # Batched into one connection/transaction (db.transaction/set_json_tx)
    # rather than a separate db.set_json call per write — a party of N
    # characters used to mean N+1 independent SQLite connections (the group
    # state, plus one per character mirror below), each paying its own
    # connect+PRAGMA overhead for what is logically one atomic save.
    try:
        with db.transaction() as conn:
            db.set_json_tx(conn, "group_states", state.group_id, payload)

            # A per-owner_id mirror, independent of which group this character
            # belongs to — separate from the group blob above so looking up one
            # player's sheet doesn't require knowing (or loading) the whole
            # conversation's state.
            mirror_entries = {}
            for owner_id, char in state.characters.items():
                mirror_entries[f"{state.group_id}:{owner_id}"] = char
            for char in state.all_characters():
                if char.character_id:
                    mirror_entries[f"{state.group_id}:{char.character_id}"] = char
            expected_keys = set(mirror_entries)
            stale_rows = conn.execute("SELECT key, data FROM characters").fetchall()
            for mirror_key, raw_entry in stale_rows:
                try:
                    entry = json.loads(raw_entry)
                except (TypeError, json.JSONDecodeError):
                    continue
                if (
                    entry.get("conversation_id") == state.group_id
                    and mirror_key not in expected_keys
                ):
                    db.delete_json_tx(conn, "characters", mirror_key)
            for mirror_key, char in mirror_entries.items():
                index_entry = {
                    "conversation_id": state.group_id,
                    "character_id": char.character_id,
                    "owner_id": char.owner_id,
                    "name": char.name,
                    "occupation": char.occupation,
                    "sheet": char.to_dict(),
                }
                db.set_json_tx(conn, "characters", mirror_key, index_entry)
    except Exception:
        _logger.exception(
            "state_save_failure group_id=%s attempted_revision=%s timeline_id=%s duration_ms=%s transaction=rolled_back",
            state.group_id, next_revision, state.timeline_id, int((time.monotonic() - started) * 1000),
        )
        raise
    state.state_revision = next_revision
    _logger.info(
        "state_save_success group_id=%s revision=%s timeline_id=%s reason=%s duration_ms=%s",
        state.group_id, state.state_revision, state.timeline_id, reason, int((time.monotonic() - started) * 1000),
    )


def _images_dir(group_id: str) -> Path:
    return DATA_DIR / f"{_safe_id(group_id)}_images"


def save_page_image(group_id: str, page_number: int, png_bytes: bytes) -> None:
    directory = _images_dir(group_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"page_{page_number}.png").write_bytes(png_bytes)


def load_page_image(group_id: str, page_number: int) -> bytes | None:
    path = _images_dir(group_id) / f"page_{page_number}.png"
    if not path.exists():
        return None
    return path.read_bytes()


def clear_page_images(group_id: str) -> None:
    """Called whenever a new PDF is uploaded, so a scenario switch doesn't leave
    a previous scenario's page images (and their page numbers) lying around."""
    shutil.rmtree(_images_dir(group_id), ignore_errors=True)


def scenario_users(scenario_id: str) -> list[str]:
    """Return every conversation currently selecting a reusable scenario."""
    users = []
    for group_id in db.list_keys("group_states"):
        try:
            if load_state(group_id).scenario_library_id == scenario_id:
                users.append(group_id)
        except Exception:
            continue
    return users
