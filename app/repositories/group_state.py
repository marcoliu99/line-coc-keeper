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

import hashlib
import json
import logging
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from sqlite3 import Connection
from uuid import uuid4

from app import config, db, locks, observability
from app.config import DATA_DIR
from app.models import GroupState

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_logger = logging.getLogger(__name__)


class StateRevisionConflict(RuntimeError):
    """Raised when a caller tries to save a snapshot older than the database."""


def _safe_id(group_id: str) -> str:
    """Still used for filesystem paths (page-image directories below) — a
    group_id is a "discord-channel-<int>" string in practice and is already
    filesystem-safe, but this stays as a
    defensive sanitizer for that path. Not used for the SQLite key itself
    (see load_state/save_state) — a TEXT primary key has no filesystem-style
    character restrictions, so the raw group_id is used there directly."""
    return _SAFE_ID_RE.sub("_", group_id)


def _log_group_id(group_id: str) -> str:
    """Use a stable, non-reversible identifier in operational logs."""
    return hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:12]


def load_state(group_id: str) -> GroupState:
    metrics: dict[str, int | bool] = {"cache_hit": False} if config.LOG_ENABLED else {}
    with observability.span("state.load", operation="load", metrics=metrics):
        data = db.get_json("group_states", group_id)
        if data is None:
            if config.LOG_ENABLED:
                metrics["state_size_bytes"] = 0
            return GroupState(group_id=group_id)
        if config.LOG_ENABLED:
            metrics["state_size_bytes"] = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        try:
            return GroupState.from_dict(data)
        except ValueError:
            _logger.exception(
                "state_load_failure group_id=%s reason=unsupported_schema",
                _log_group_id(group_id),
            )
            raise


def save_state(
    state: GroupState, *, reason: str = "command",
    mutate_tx: Callable[[Connection], None] | None = None,
) -> None:
    metrics: dict[str, int | bool] = {}
    if config.LOG_ENABLED:
        metrics["state_size_bytes"] = len(json.dumps(state.to_dict(), ensure_ascii=False).encode("utf-8"))
    with observability.span("state.save", operation=reason, metrics=metrics):
        _save_state_impl(state, reason=reason, mutate_tx=mutate_tx)


def _save_state_impl(
    state: GroupState, *, reason: str = "command",
    mutate_tx: Callable[[Connection], None] | None = None,
) -> None:
    """Persist one state snapshot under the authoritative per-group lock.

    The revision check turns a stale read-modify-write into an explicit
    conflict instead of silently discarding a newer mutation from another
    worker. Intentional replacement flows such as ``newgame`` opt out via
    their explicit reason.
    """
    started = time.monotonic()
    try:
        with locks.get_state_lock(state.group_id), db.transaction() as conn:
            # Keep the optimistic check and the complete snapshot write in one
            # IMMEDIATE transaction. The Python RLock protects threads in this
            # process; BEGIN IMMEDIATE also serializes competing processes using
            # the same SQLite database.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM group_states WHERE key = ?", (state.group_id,)
            ).fetchone()
            current = json.loads(row[0]) if row is not None else None
            if (
                reason != "newgame"
                and current is not None
                and int(current.get("state_revision", 0)) != state.state_revision
            ):
                raise StateRevisionConflict(
                    f"state revision conflict for {_log_group_id(state.group_id)}: "
                    f"loaded={state.state_revision}, current={current.get('state_revision', 0)}"
                )
            if mutate_tx is not None:
                mutate_tx(conn)
            _save_state_unlocked(state, reason=reason, conn=conn)
    except StateRevisionConflict:
        current_revision = current.get("state_revision", 0) if current else None
        _logger.warning(
            "state_save_revision_conflict group_id=%s loaded_revision=%s current_revision=%s reason=%s duration_ms=%s",
            _log_group_id(state.group_id), state.state_revision, current_revision, reason,
            int((time.monotonic() - started) * 1000),
        )
        raise


def _save_state_unlocked(
    state: GroupState, *, reason: str = "command", conn
) -> None:
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
            if not isinstance(entry, dict):
                continue
            # Modern mirrors carry conversation_id.  Older rows may not have
            # that metadata, but the group-prefixed key still gives us an
            # unambiguous ownership boundary.  Rows without either signal
            # are deliberately preserved for safety rather than guessed at.
            owned_by_group = (
                entry.get("conversation_id") == state.group_id
                or mirror_key.startswith(f"{state.group_id}:")
            )
            if (
                owned_by_group
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
            _log_group_id(state.group_id), next_revision, state.timeline_id, int((time.monotonic() - started) * 1000),
        )
        raise
    state.state_revision = next_revision
    _logger.info(
        "state_save_success group_id=%s revision=%s timeline_id=%s reason=%s duration_ms=%s",
        _log_group_id(state.group_id), state.state_revision, state.timeline_id, reason, int((time.monotonic() - started) * 1000),
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
        except Exception:  # one corrupt group must not hide other scenario users.
            _logger.debug("could not inspect group state for scenario %s", scenario_id, exc_info=True)
            continue
    return users
