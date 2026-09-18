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

import re
import shutil
from pathlib import Path

from app import db
from app.config import DATA_DIR
from app.models import GroupState

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


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
    return GroupState.from_dict(data)


def save_state(state: GroupState) -> None:
    # Batched into one connection/transaction (db.transaction/set_json_tx)
    # rather than a separate db.set_json call per write — a party of N
    # characters used to mean N+1 independent SQLite connections (the group
    # state, plus one per character mirror below), each paying its own
    # connect+PRAGMA overhead for what is logically one atomic save.
    with db.transaction() as conn:
        db.set_json_tx(conn, "group_states", state.group_id, state.to_dict())

        # A per-owner_id mirror, independent of which group this character
        # belongs to — separate from the group blob above so looking up one
        # player's sheet doesn't require knowing (or loading) the whole
        # conversation's state.
        for owner_id, char in state.characters.items():
            index_entry = {
                "conversation_id": state.group_id,
                "name": char.name,
                "occupation": char.occupation,
                "sheet": char.to_dict(),
            }
            db.set_json_tx(conn, "characters", owner_id, index_entry)


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