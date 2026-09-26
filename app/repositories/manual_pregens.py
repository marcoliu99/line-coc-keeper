"""Group-owned, scenario-scoped sources for reusable manual character cards."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from sqlite3 import Connection
from uuid import uuid4

from app import character_matcher, db, pregen_extractor


def _same_character(a: dict, b: dict) -> bool:
    # Repository callers hold the GroupState write transaction. Matching
    # cannot learn dictionary aliases through a second SQLite connection.
    return character_matcher.is_same_character(a, b, learn_aliases=False)


def _key(group_id: str, scenario_id: str | None) -> str:
    return json.dumps([group_id, scenario_id], ensure_ascii=False, separators=(",", ":"))


def _read(conn: Connection, group_id: str, scenario_id: str | None) -> dict:
    row = conn.execute(
        "SELECT data FROM manual_pregen_assets WHERE key = ?", (_key(group_id, scenario_id),)
    ).fetchone()
    return json.loads(row[0]) if row else {"schema_version": 1, "entries": [], "legacy_snapshots": []}


def _write(conn: Connection, group_id: str, scenario_id: str | None, data: dict) -> None:
    db.set_json_tx(conn, "manual_pregen_assets", _key(group_id, scenario_id), data)


def list_assets(group_id: str, scenario_id: str) -> list[dict]:
    data = db.get_json("manual_pregen_assets", _key(group_id, scenario_id)) or {}
    return [*data.get("entries", []), *data.get("legacy_snapshots", [])]


def _clean(pregen: dict) -> dict:
    return {key: deepcopy(value) for key, value in pregen.items() if key != "claimed_by"}


def _fingerprint(pregen: dict) -> str:
    return hashlib.sha256(json.dumps(_clean(pregen), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _upsert(data: dict, pregen: dict, filename: str, *, asset_id: str | None = None) -> tuple[str, str]:
    entries = data["entries"]
    for entry in entries:
        old = entry["pregen"]
        same = _same_character(old, pregen)
        if entry["filename"] == filename and not same and (old.get("name") or pregen.get("name")):
            raise ValueError("同名檔案已屬於另一位角色；請改名後再匯入。")
        if same or (entry["filename"] == filename and not old.get("name") and not pregen.get("name")):
            entry["filename"] = filename
            entry["pregen"] = _clean(pregen)
            return entry["asset_id"], "updated"
    asset_id = asset_id or uuid4().hex
    entries.append({"asset_id": asset_id, "filename": filename, "pregen": _clean(pregen)})
    return asset_id, "added"


def capture_legacy(
    conn: Connection, group_id: str, scenario_id: str | None,
    pregens: list[dict], source_hash: str = "",
) -> None:
    """Idempotently retain unclaimed cards created before this repository existed."""
    data = _read(conn, group_id, scenario_id)
    changed = False
    for pregen in pregens:
        if pregen.get("claimed_by"):
            continue
        source = pregen.get("source")
        if source == "manual":
            if any(_same_character(e["pregen"], pregen) for e in data["entries"]):
                continue
            _upsert(data, pregen, f"legacy-{_fingerprint(pregen)[:12]}.md")
            changed = True
        elif source == "merged" and scenario_id and source_hash:
            fingerprint = _fingerprint(pregen)
            if any(_same_character(e["pregen"], pregen) for e in data["entries"]):
                continue
            if any(s.get("fingerprint") == fingerprint for s in data["legacy_snapshots"]):
                continue
            data["legacy_snapshots"].append({
                "asset_id": uuid4().hex, "fingerprint": fingerprint,
                "source_hash": source_hash, "pregen": _clean(pregen),
            })
            changed = True
    if changed:
        _write(conn, group_id, scenario_id, data)


def bind_pending(conn: Connection, group_id: str, scenario_id: str) -> None:
    pending = _read(conn, group_id, None)
    if not pending["entries"]:
        return
    selected = _read(conn, group_id, scenario_id)
    for entry in pending["entries"]:
        # Apply the same filename/identity checks as a direct upload. A
        # pending card must not introduce two different characters with one
        # filename, which would make subsequent updates ambiguous.
        _upsert(selected, entry["pregen"], entry["filename"], asset_id=entry["asset_id"])
    _write(conn, group_id, scenario_id, selected)
    db.delete_json_tx(conn, "manual_pregen_assets", _key(group_id, None))


def store_upload(
    conn: Connection, group_id: str, scenario_id: str | None,
    pregen: dict, filename: str,
) -> tuple[str, str]:
    data = _read(conn, group_id, scenario_id)
    asset_id, action = _upsert(data, pregen, filename)
    data["legacy_snapshots"] = [
        item for item in data["legacy_snapshots"]
        if not _same_character(item["pregen"], pregen)
    ]
    _write(conn, group_id, scenario_id, data)
    return asset_id, action


def build_pool(
    conn: Connection, group_id: str, scenario_id: str,
    scenario_pregens: list[dict], source_hash: str,
) -> tuple[list[dict], bool]:
    data = _read(conn, group_id, scenario_id)
    pool = [_clean(p) for p in scenario_pregens]
    for entry in sorted(data["entries"], key=lambda item: item["asset_id"]):
        pool, _ = pregen_extractor.reconcile_pregen_into_pool(pool, entry["pregen"], learn_aliases=False)
    stale = False
    for item in sorted(data["legacy_snapshots"], key=lambda snapshot: snapshot["asset_id"]):
        if item["source_hash"] != source_hash:
            stale = True
            continue
        if any(_same_character(e["pregen"], item["pregen"])
               for e in data["entries"]):
            continue
        pool, _ = pregen_extractor.reconcile_pregen_into_pool(pool, item["pregen"], learn_aliases=False)
    return pool, stale


def install_pool(
    conn: Connection, group_id: str, scenario_id: str, context: dict,
    *, bind_unassigned: bool = False, claimed: list[dict] | None = None,
) -> tuple[list[dict], bool]:
    if bind_unassigned:
        bind_pending(conn, group_id, scenario_id)
    pool, stale = build_pool(
        conn, group_id, scenario_id, context["pregens"],
        context["manifest"].get("content_hash", ""),
    )
    claimed_cards = deepcopy(claimed or [])
    pool = [p for p in pool if not any(
        _same_character(p, claimed_card) for claimed_card in claimed_cards
    )]
    return [*claimed_cards, *pool], stale


def delete_asset(conn: Connection, group_id: str, scenario_id: str, asset_id: str) -> dict:
    data = _read(conn, group_id, scenario_id)
    for field in ("entries", "legacy_snapshots"):
        for item in data[field]:
            if item["asset_id"] == asset_id:
                data[field].remove(item)
                _write(conn, group_id, scenario_id, data)
                return item
    raise KeyError(asset_id)
