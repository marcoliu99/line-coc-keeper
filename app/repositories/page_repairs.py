"""Group-owned, scenario-scoped page repairs: corrected pages that are laid over the library text on every load.

Like the manual pregen cards, a repair belongs to the conversation and the scenario it was uploaded for, so loading
the scenario again from the library keeps it. It is bound to the scenario's source hash: when the library entry itself
changes (a reparse, a new upload) the stored pages no longer describe it and are ignored.
"""
from __future__ import annotations

import json
from sqlite3 import Connection

from app import db, scenario_page_repair

_TABLE = "scenario_page_repairs"


def _key(group_id: str, scenario_id: str) -> str:
    return json.dumps([group_id, scenario_id], ensure_ascii=False, separators=(",", ":"))


def load(group_id: str, scenario_id: str, source_hash: str) -> dict[int, str]:
    data = db.get_json(_TABLE, _key(group_id, scenario_id)) or {}
    if data.get("source_hash") != source_hash:
        return {}
    return {int(page): text for page, text in data.get("pages", {}).items()}


def save(conn: Connection, group_id: str, scenario_id: str, source_hash: str, pages: dict[int, str]) -> None:
    """Overwrite these pages, keeping the others stored for the same source."""
    row = conn.execute(f"SELECT data FROM {_TABLE} WHERE key = ?", (_key(group_id, scenario_id),)).fetchone()  # nosec B608
    stored = json.loads(row[0]) if row else {}
    merged = {str(p): t for p, t in (stored.get("pages", {}) if stored.get("source_hash") == source_hash else {}).items()}
    merged.update({str(page): text for page, text in pages.items()})
    db.set_json_tx(conn, _TABLE, _key(group_id, scenario_id), {"source_hash": source_hash, "pages": merged})


def apply_saved(group_id: str, scenario_id: str, source_hash: str, text: str) -> str:
    """The library text with this conversation's saved pages laid over it."""
    pages = load(group_id, scenario_id, source_hash)
    if not pages:
        return text
    try:
        return scenario_page_repair.apply_pages(text, pages)
    except scenario_page_repair.PageRepairError:
        return text  # the library text no longer has those pages; play the original rather than fail the load
