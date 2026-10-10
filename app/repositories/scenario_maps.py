"""Group-owned, scenario-scoped hand-drawn maps (``map_*.yaml`` uploads), kept the way the manual role cards are.

A map belongs to the conversation and the scenario it was uploaded for, so a new game (``/coc newgame``) or choosing the
scenario again from the library brings it back without another upload; a map was lost both ways before (the 2026-10-10
Discord session uploaded the same maps three times). A map uploaded before any scenario is loaded waits under no
scenario and joins the first one loaded, as a role card does. Unlike a page repair it is not bound to the source hash:
correcting the scenario text does not move its rooms. Uploading a file of the same name replaces the stored map.
"""
from __future__ import annotations

import json
from sqlite3 import Connection
from typing import Any

from app import db

_TABLE = "scenario_map_assets"


def is_custom(key: str) -> bool:
    """An uploaded map's key; a map read from a PDF page is keyed by the page number."""
    return str(key).startswith("custom_")


def custom(maps: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in maps.items() if is_custom(key)}


def _key(group_id: str, scenario_id: str | None) -> str:
    return json.dumps([group_id, scenario_id or None], ensure_ascii=False, separators=(",", ":"))


def load(group_id: str, scenario_id: str | None) -> dict[str, Any]:
    """The maps stored for this scenario, or the ones waiting for a scenario when ``scenario_id`` is empty."""
    data = db.get_json(_TABLE, _key(group_id, scenario_id)) or {}
    return dict(data.get("maps", {}))


def save(conn: Connection, group_id: str, scenario_id: str | None, maps: dict[str, Any]) -> None:
    """Store these maps for the scenario, replacing any of the same key and keeping the others."""
    if not maps:
        return
    row = conn.execute(f"SELECT data FROM {_TABLE} WHERE key = ?", (_key(group_id, scenario_id),)).fetchone()  # nosec B608
    stored = json.loads(row[0]).get("maps", {}) if row else {}
    db.set_json_tx(conn, _TABLE, _key(group_id, scenario_id), {"maps": {**stored, **maps}})


def drop_pending(conn: Connection, group_id: str) -> None:
    """Forget the maps that waited for a scenario, once the first one loaded has taken them."""
    db.delete_json_tx(conn, _TABLE, _key(group_id, None))
