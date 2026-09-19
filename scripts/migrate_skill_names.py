"""Migrate persisted skill aliases to canonical skill names.

Renaming BASE_SKILLS itself only changes what NEW characters get; it doesn't
touch a Character or pregen dict that was already saved under the old
spelling (e.g. "估價": 45 sitting in some player's already-persisted
`skills` dict) — that key just silently stops being found once the Keeper
or a skill_check looks up the new canonical name instead. Run this once,
after deploying the rename, to bring every already-persisted skills dict up
to date:

    .venv/bin/python -m scripts.migrate_skill_names --dry-run
    .venv/bin/python -m scripts.migrate_skill_names --apply

Idempotent and safe to re-run. If both alias and canonical keys exist with
numeric values, the higher value wins rather than silently depending on dict
order.

Touches three places skills dicts can live: each GroupState's
`characters` (currently-claimed investigators) and `pregens` (the
unclaimed pool) in the "group_states" table, and the standalone per-owner
mirror in the "characters" table (`sheet.skills`).
The default is dry-run. Apply mode writes all changed rows in one SQLite
transaction so a failed migration cannot leave group state and character
mirrors half-updated.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from app import db
from app.skill_aliases import SKILL_ALIASES as _RENAMES


@dataclass(frozen=True)
class MigrationReport:
    group_states_seen: int = 0
    group_states_changed: int = 0
    characters_seen: int = 0
    characters_changed: int = 0
    entries_changed: int = 0
    conflicts_merged: int = 0
    dry_run: bool = False


def _rename_skills(skills: dict[str, Any]) -> tuple[bool, int, int]:
    """Mutate one skills dict and return (changed, entries, conflicts)."""
    changed = False
    entries_changed = 0
    conflicts = 0
    for old_name, new_name in _RENAMES.items():
        if old_name not in skills:
            continue
        value = skills.pop(old_name)
        if new_name in skills:
            conflicts += 1
            if isinstance(skills[new_name], (int, float)) and isinstance(value, (int, float)):
                skills[new_name] = max(skills[new_name], value)
            else:
                skills.setdefault(new_name, value)
        else:
            skills[new_name] = value
        changed = True
        entries_changed += 1
    return changed, entries_changed, conflicts


def migrate(*, dry_run: bool = False) -> MigrationReport:
    changed_rows: list[tuple[str, str, dict[str, Any]]] = []
    group_seen = group_changed = character_seen = character_changed = 0
    entries_changed = conflicts_merged = 0

    for group_id in db.list_keys("group_states"):
        group_seen += 1
        data = db.get_json("group_states", group_id)
        if not data:
            continue
        changed = False
        for char_data in (data.get("characters") or {}).values():
            row_changed, count, conflicts = _rename_skills(char_data.get("skills") or {})
            changed |= row_changed
            entries_changed += count
            conflicts_merged += conflicts
        for pregen in data.get("pregens") or []:
            row_changed, count, conflicts = _rename_skills(pregen.get("skills") or {})
            changed |= row_changed
            entries_changed += count
            conflicts_merged += conflicts
        if changed:
            group_changed += 1
            changed_rows.append(("group_states", group_id, data))

    for owner_id in db.list_keys("characters"):
        character_seen += 1
        data = db.get_json("characters", owner_id)
        if not data:
            continue
        sheet = data.get("sheet") or {}
        changed, count, conflicts = _rename_skills(sheet.get("skills") or {})
        entries_changed += count
        conflicts_merged += conflicts
        if changed:
            character_changed += 1
            changed_rows.append(("characters", owner_id, data))

    if not dry_run:
        with db.transaction() as conn:
            for table, key, data in changed_rows:
                db.set_json_tx(conn, table, key, data)

    return MigrationReport(
        group_states_seen=group_seen,
        group_states_changed=group_changed,
        characters_seen=character_seen,
        characters_changed=character_changed,
        entries_changed=entries_changed,
        conflicts_merged=conflicts_merged,
        dry_run=dry_run,
    )


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report without writing (default)")
    mode.add_argument("--apply", action="store_true", help="write all changes in one transaction")
    args = parser.parse_args()
    report = migrate(dry_run=not args.apply)
    action = "Would update" if report.dry_run else "Updated"
    print(
        f"{action} {report.group_states_changed} group_states row(s) and "
        f"{report.characters_changed} characters row(s); "
        f"{report.entries_changed} alias entrie(s), "
        f"{report.conflicts_merged} numeric conflict(s) merged."
    )
    if report.dry_run:
        print("Dry-run only. Re-run with --apply to commit these changes.")


if __name__ == "__main__":
    _main()
