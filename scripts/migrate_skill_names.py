"""One-time migration: rename skill keys already persisted under this
project's OLD Chinese terminology to the official terminology table the
project owner supplied on 2026-09-17 (see app/models.py's BASE_SKILLS —
its own comment lists the exact renames this mirrors).

Renaming BASE_SKILLS itself only changes what NEW characters get; it doesn't
touch a Character or pregen dict that was already saved under the old
spelling (e.g. "估價": 45 sitting in some player's already-persisted
`skills` dict) — that key just silently stops being found once the Keeper
or a skill_check looks up the new canonical name instead. Run this once,
after deploying the rename, to bring every already-persisted skills dict up
to date:

    .venv/bin/python -m scripts.migrate_skill_names

Purely additive/idempotent and safe to re-run: renaming only fires when the
OLD key is actually present (a value under the NEW key, if one somehow
already exists, is never overwritten — see _rename_skills), and every write
here is an upsert of the same row it read, not a new row.

Touches three places skills dicts can live: each GroupState's
`characters` (currently-claimed investigators) and `pregens` (the
unclaimed pool) in the "group_states" table, and the standalone per-owner
mirror in the "characters" table (`sheet.skills`).
"""
from __future__ import annotations

from app import db

# OLD Chinese skill name -> NEW official terminology-table name. Mirrors
# app/models.py's BASE_SKILLS comment and app/skill_aliases.py's reverse
# aliases exactly — this is the one-time data-side counterpart to those
# code-side changes.
_RENAMES: dict[str, str] = {
    "估價": "鑑定",
    "話術": "快速交談",
    "領航": "導航",
    "巧手": "妙手",
    "駕駛（其他載具）": "駕駛",
    "電器維修": "電氣維修",
    "外語（其他）": "其他語言",
    "重機械操作": "重型機械操作",
}


def _rename_skills(skills: dict) -> bool:
    """Mutates `skills` in place; returns True if anything changed. Never
    overwrites a value already sitting under the new name — if that
    somehow happens (the character was already migrated, or independently
    ended up with both keys), the old key is just dropped rather than
    clobbering data that's presumably more current."""
    changed = False
    for old_name, new_name in _RENAMES.items():
        if old_name not in skills:
            continue
        value = skills.pop(old_name)
        skills.setdefault(new_name, value)
        changed = True
    return changed


def migrate() -> None:
    counts = {"group_states": 0, "characters": 0}

    for group_id in db.list_keys("group_states"):
        data = db.get_json("group_states", group_id)
        if not data:
            continue
        changed = False
        for char_data in (data.get("characters") or {}).values():
            if _rename_skills(char_data.get("skills") or {}):
                changed = True
        for pregen in data.get("pregens") or []:
            if _rename_skills(pregen.get("skills") or {}):
                changed = True
        if changed:
            db.set_json("group_states", group_id, data)
            counts["group_states"] += 1
            print(f"  group_states: {group_id} — renamed old-terminology skill keys")

    for owner_id in db.list_keys("characters"):
        data = db.get_json("characters", owner_id)
        if not data:
            continue
        sheet = data.get("sheet") or {}
        if _rename_skills(sheet.get("skills") or {}):
            db.set_json("characters", owner_id, data)
            counts["characters"] += 1
            print(f"  characters: {owner_id} — renamed old-terminology skill keys")

    print(f"Done. Updated {counts['group_states']} group_states row(s), {counts['characters']} characters row(s).")


if __name__ == "__main__":
    migrate()
