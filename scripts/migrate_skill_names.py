"""One-time migration: rename/merge skill keys already persisted under any
alias this project's canonicalization now understands (app/skill_aliases.py's
SKILL_ALIASES — both the 2026-09-17 terminology-table renames and every
other alias, e.g. "手槍"->"射擊（手槍）") onto their one canonical BASE_SKILLS
name.

Fixing app/pregen_extractor.py's _translate_skill_names and app/creation.py's
allocate() to canonicalize going forward (see docs/pregen_luck_roll_design_
spec.md) only changes what NEW extractions/allocations produce; it doesn't
touch a Character or pregen dict that was already saved with both an alias
and its canonical name sitting side by side as two separate entries (the
exact real symptom that led to fixing those two call sites: /coc pregens'
preview showing e.g. both "鬥毆 70%" and "格鬥（鬥毆） 70%"). Run this once,
after deploying those fixes, to bring every already-persisted skills dict
up to date:

    .venv/bin/python -m scripts.migrate_skill_names

Imports SKILL_ALIASES directly from app.skill_aliases rather than keeping a
separate hand-copied table here — the previous version of this script only
mirrored the terminology-rename subset of that table (added 2026-09-17) and
missed every general alias (手槍/鬥毆/話術/...), so a real duplicate coming
from one of those never got migrated even after this script's original run.
Importing the live table means this script can never drift out of sync with
it again.

Idempotent and safe to re-run: a name with no alias-pair present in a given
skills dict is left untouched. When both the alias and its canonical name
are present with *different* values, keeps the higher one (see
_rename_skills) instead of unconditionally dropping the alias's value —
matching the same merge discipline _translate_skill_names now uses.

Touches three places skills dicts can live: each GroupState's
`characters` (currently-claimed investigators) and `pregens` (the
unclaimed pool) in the "group_states" table, and the standalone per-owner
mirror in the "characters" table (`sheet.skills`).
"""
from __future__ import annotations

from app import db
from app.skill_aliases import SKILL_ALIASES as _RENAMES


def _rename_skills(skills: dict) -> bool:
    """Mutates `skills` in place; returns True if anything changed. When the
    canonical name is already present too, keeps whichever of the two
    values is higher (see this module's docstring for why: a lower
    duplicate is more likely leftover extraction noise than a real, lower,
    intentional value, and picking one over the other silently based on
    which key the dict happened to already have would be arbitrary)."""
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
