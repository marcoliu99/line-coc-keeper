"""One-time migration: rename/merge persisted skill keys using the live alias table.

Renaming BASE_SKILLS itself only changes what NEW characters get; it doesn't
touch a Character or pregen dict that was already saved under the old
spelling (e.g. "估價": 45 sitting in some player's already-persisted
`skills` dict) — that key just silently stops being found once the Keeper
or a skill_check looks up the new canonical name instead. Run this once,
after deploying the rename, to bring every already-persisted skills dict up
to date:

    .venv/bin/python -m scripts.migrate_skill_names

Idempotent and safe to re-run. If both alias and canonical keys exist with
numeric values, the higher value wins rather than silently depending on dict
order.

Touches three places skills dicts can live: each GroupState's
`characters` (currently-claimed investigators) and `pregens` (the
unclaimed pool) in the "group_states" table, and the standalone per-owner
mirror in the "characters" table (`sheet.skills`).
"""
from __future__ import annotations

from app import db
from app.skill_aliases import SKILL_ALIASES as _RENAMES


def _rename_skills(skills: dict) -> bool:
    """Mutate one skills dict and return whether any key was changed."""
    changed = False
    for old_name, new_name in _RENAMES.items():
        if old_name not in skills:
            continue
        value = skills.pop(old_name)
        if new_name in skills and isinstance(skills[new_name], (int, float)) and isinstance(value, (int, float)):
            skills[new_name] = max(skills[new_name], value)
        else:
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
