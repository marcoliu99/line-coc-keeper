"""One-time migration: import the old data/groups/*.json flat files into the
new SQLite database (see app/db.py, app/state.py, app/scenario_rag.py,
app/memory_rag.py).

Run once, before restarting the bot on the new SQLite-backed code:

    .venv/bin/python -m scripts.migrate_json_to_sqlite

Purely additive and safe to re-run: every write here is an upsert (last run
wins), and none of the original *.json files are touched or deleted — once
you've confirmed the bot works correctly against the database, you can
archive or delete data/groups/*.json (and data/groups/characters/*.json)
yourself; this script won't do that for you.

Does NOT touch data/groups/<id>_images/ (scenario page PNGs) — those stay
exactly where they are, unaffected by this migration.
"""
from __future__ import annotations

import json
import sys

from app import db
from app.config import DATA_DIR


def _iter_group_state_files():
    for path in DATA_DIR.glob("*.json"):
        stem = path.stem
        # Skip the two other *.json shapes that live directly in DATA_DIR
        # alongside plain group-state files — a group_id never legitimately
        # ends in these suffixes (see app/scenario_rag.py / app/memory_rag.py's
        # old _index_path/_memory_path), so this is an unambiguous filter.
        if stem.endswith("_scenario_index") or stem.endswith("_memory"):
            continue
        yield stem, path


def migrate() -> None:
    counts = {"group_states": 0, "characters": 0, "scenario_indexes": 0, "memory_chunks": 0}

    for group_id, path in _iter_group_state_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        db.set_json("group_states", group_id, data)
        counts["group_states"] += 1
        print(f"  group_states: {group_id} <- {path}")

    characters_dir = DATA_DIR / "characters"
    if characters_dir.exists():
        for path in characters_dir.glob("*.json"):
            owner_id = path.stem
            data = json.loads(path.read_text(encoding="utf-8"))
            db.set_json("characters", owner_id, data)
            counts["characters"] += 1
            print(f"  characters: {owner_id} <- {path}")

    for path in DATA_DIR.glob("*_scenario_index.json"):
        group_id = path.stem[: -len("_scenario_index")]
        data = json.loads(path.read_text(encoding="utf-8"))
        db.set_json("scenario_indexes", group_id, data)
        counts["scenario_indexes"] += 1
        print(f"  scenario_indexes: {group_id} <- {path}")

    for path in DATA_DIR.glob("*_memory.json"):
        group_id = path.stem[: -len("_memory")]
        data = json.loads(path.read_text(encoding="utf-8"))
        db.set_json("memory_chunks", group_id, data)
        counts["memory_chunks"] += 1
        print(f"  memory_chunks: {group_id} <- {path}")

    print()
    print("Migration summary:", counts)


def verify() -> bool:
    """Reads every migrated row back and compares it against the source file
    it came from, byte-for-byte after JSON parsing (so key order / whitespace
    differences don't cause a false mismatch) — confirms nothing was dropped
    or altered in transit before anyone deletes the original files."""
    ok = True
    for group_id, path in _iter_group_state_files():
        expected = json.loads(path.read_text(encoding="utf-8"))
        actual = db.get_json("group_states", group_id)
        if actual != expected:
            print(f"MISMATCH group_states/{group_id}")
            ok = False

    characters_dir = DATA_DIR / "characters"
    if characters_dir.exists():
        for path in characters_dir.glob("*.json"):
            owner_id = path.stem
            expected = json.loads(path.read_text(encoding="utf-8"))
            actual = db.get_json("characters", owner_id)
            if actual != expected:
                print(f"MISMATCH characters/{owner_id}")
                ok = False

    for path in DATA_DIR.glob("*_scenario_index.json"):
        group_id = path.stem[: -len("_scenario_index")]
        expected = json.loads(path.read_text(encoding="utf-8"))
        actual = db.get_json("scenario_indexes", group_id)
        if actual != expected:
            print(f"MISMATCH scenario_indexes/{group_id}")
            ok = False

    for path in DATA_DIR.glob("*_memory.json"):
        group_id = path.stem[: -len("_memory")]
        expected = json.loads(path.read_text(encoding="utf-8"))
        actual = db.get_json("memory_chunks", group_id)
        if actual != expected:
            print(f"MISMATCH memory_chunks/{group_id}")
            ok = False

    return ok


if __name__ == "__main__":
    print(f"Migrating data/groups/*.json -> {db.DB_PATH}")
    migrate()
    print()
    print("Verifying...")
    if verify():
        print("OK: every migrated row matches its source file exactly.")
    else:
        print("MISMATCHES FOUND — see above. Do not delete the original *.json files.")
        sys.exit(1)
