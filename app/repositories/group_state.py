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
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection
from typing import Any
from uuid import uuid4

from app import config, db, locks, observability
from app.config import DATA_DIR
from app.models import GroupState
from app.services import mutation_admission

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
    """Storage primitive: persist one whole snapshot, rejecting a stale revision.

    Production code does not call this. Every game-state write goes through
    ``app.repositories.state_transaction.mutate``, which reloads the latest
    state under the conversation lock, validates timeline/action/revision and
    commits state, events and the action result together. This function stays
    for storage-level tests and fixtures; ``tests/test_architecture_state_writes.py``
    fails the build when an application module imports it.
    """
    metrics: dict[str, int | bool] = {}
    with observability.span("state.save", operation=reason, metrics=metrics):
        committed = _save_state_impl(state, reason=reason, mutate_tx=mutate_tx)
        if config.LOG_ENABLED:
            metrics["state_size_bytes"] = committed.state_size_bytes


def _save_state_impl(
    state: GroupState, *, reason: str = "command",
    mutate_tx: Callable[[Connection], None] | None = None,
) -> StateCommit:
    """Persist one state snapshot under the authoritative per-group lock.

    The revision check turns a stale read-modify-write into an explicit
    conflict instead of silently discarding a newer mutation from another
    worker. Intentional replacement flows such as ``newgame`` opt out via
    their explicit reason.
    """
    started = time.monotonic()
    try:
        with locks.get_state_lock(state.group_id):
            mutation_admission.assert_admitted(state.group_id)
            with db.transaction() as conn:
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
                committed = write_state_tx(state, reason=reason, conn=conn, previous=current)
            committed.apply(state)
    except StateRevisionConflict:
        current_revision = current.get("state_revision", 0) if current else None
        _logger.warning(
            "state_save_revision_conflict group_id=%s loaded_revision=%s current_revision=%s reason=%s duration_ms=%s",
            _log_group_id(state.group_id), state.state_revision, current_revision, reason,
            int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception:
        _logger.exception(
            "state_save_failure group_id=%s revision=%s reason=%s duration_ms=%s",
            _log_group_id(state.group_id), state.state_revision, reason,
            int((time.monotonic() - started) * 1000),
        )
        raise

    return committed


@dataclass(frozen=True)
class StateCommit:
    """Prepared transaction receipt; apply ONLY after the outer commit succeeds."""

    revision: int
    timeline_id: str
    reason: str
    mirror_reads: int
    mirror_writes: int
    mirror_deletes: int
    state_size_bytes: int

    def apply(self, state: GroupState) -> None:
        state.state_revision = self.revision
        state.timeline_id = self.timeline_id
        state.loaded_timeline_id = self.timeline_id
        _logger.info(
            "state_save_success group_id=%s revision=%s timeline_id=%s reason=%s "
            "mirror_reads=%s mirror_writes=%s mirror_deletes=%s",
            _log_group_id(state.group_id), self.revision, self.timeline_id, self.reason,
            self.mirror_reads, self.mirror_writes, self.mirror_deletes,
        )


def character_mirror_projection(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pure projection of persisted aliases, including retired/manual characters.

    Use serialized sheets directly: loading a model here could migrate a legacy
    character or assign an ID, changing the keys we are trying to identify.
    """
    group_id = payload["group_id"]
    entries: dict[str, dict[str, Any]] = {}

    def add(key: str, sheet: dict[str, Any]) -> None:
        entries[f"{group_id}:{key}"] = {
            "conversation_id": group_id,
            "character_id": sheet.get("character_id", ""),
            "owner_id": sheet.get("owner_id", ""),
            "name": sheet.get("name", ""),
            "occupation": sheet.get("occupation", ""),
            "sheet": sheet,
        }

    characters = payload.get("characters", {})
    for owner_id, sheet in characters.items():
        add(owner_id, sheet)
    seen: set[str] = set()
    for sheet in [*characters.values(), *payload.get("characters_by_id", {}).values()]:
        character_id = sheet.get("character_id", "")
        identity = character_id or f"legacy-user:{sheet.get('owner_id', '')}"
        if identity not in seen:
            seen.add(identity)
            if character_id:
                add(character_id, sheet)
    return entries


def write_state_tx(
    state: GroupState, *, reason: str = "command", conn: Connection,
    previous: dict[str, Any] | None = None,
) -> StateCommit:
    """Storage primitive: write within the caller's transaction, without updating the caller snapshot.

    Only ``app/repositories/state_transaction.py`` (the single game-state write
    boundary) and this module may call it; ``tests/test_architecture_state_writes.py``
    enforces that. Everything else goes through ``state_transaction.mutate``.

    Candidate keys come from the old/new authoritative group payloads. Unknown
    historical orphans need a separate audited migration, never a hot-path scan.
    """
    if previous is None:
        row = conn.execute("SELECT data FROM group_states WHERE key = ?", (state.group_id,)).fetchone()
        previous = json.loads(row[0]) if row else None
    old = character_mirror_projection(previous) if previous else {}
    timeline_id = state.timeline_id or f"timeline-{uuid4().hex[:8]}"
    revision = state.state_revision + 1
    payload = state.to_dict()
    payload.update(state_revision=revision, timeline_id=timeline_id)
    expected = character_mirror_projection(payload)
    keys = sorted(old.keys() | expected.keys())
    saved: dict[str, str] = {}
    for start in range(0, len(keys), 400):
        batch = keys[start:start + 400]
        placeholders = ",".join("?" for _ in batch)
        saved.update(conn.execute(
            f"SELECT key, data FROM characters WHERE key IN ({placeholders})", batch  # nosec B608
        ).fetchall())
    writes = deletes = 0
    # Validate ownership even for an exact key: group/alias delimiters can
    # collide (e.g. group 'a', alias 'b:c' and group 'a:b', alias 'c').
    for key in keys:
        raw = saved.get(key)
        try:
            actual = json.loads(raw) if raw is not None else None
        except (TypeError, ValueError):
            actual = None
        if isinstance(actual, dict) and actual.get("conversation_id") not in (None, state.group_id):
            if key in expected:
                raise ValueError("character mirror key is owned by another conversation")
            continue
        if key in expected:
            if actual != expected[key]:
                db.set_json_tx(conn, "characters", key, expected[key])
                writes += 1
        elif isinstance(actual, dict) and (
            actual.get("conversation_id") == state.group_id or actual == old[key]
        ):
            db.delete_json_tx(conn, "characters", key)
            deletes += 1
    state_size_bytes = db.set_json_tx(conn, "group_states", state.group_id, payload)
    return StateCommit(revision, timeline_id, reason, len(saved), writes, deletes, state_size_bytes)


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
