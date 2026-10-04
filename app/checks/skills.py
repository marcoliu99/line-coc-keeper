"""Resolving a skill or attribute name to the value a check rolls against.

Lives with the check engine because every check (tool, command, button, scripted
opening) must read a character's target the same way; the Keeper re-exports it.
"""
from __future__ import annotations

from app.models import BASE_SKILLS, Character
from app.skill_aliases import canonical_skill_name

ATTR_ALIASES = {
    "STR": "str_", "力量": "str_", "CON": "con", "體質": "con", "SIZ": "siz", "體型": "siz",
    "DEX": "dex", "敏捷": "dex", "APP": "app", "外貌": "app", "INT": "int_", "智力": "int_",
    "POW": "pow_", "意志": "pow_", "精神力": "pow_", "EDU": "edu", "教育": "edu",
    "LUCK": "luck", "幸運": "luck",
}


def resolve_skill_value(char: Character, skill_name: str, *, register_unknown: bool = True) -> int:
    key = skill_name.strip()
    if key in char.skills:
        return char.skills[key]
    if key.upper() in ATTR_ALIASES:
        return getattr(char, ATTR_ALIASES[key.upper()])

    # Canonicalize both the query and every existing key (see app/skill_aliases.py)
    # before comparing — catches e.g. "手槍" vs char.skills' own "射擊（手槍）",
    # which used to silently miss each other and fall through to the substring
    # fallback below (or worse, register a brand new duplicate skill).
    canonical_query = canonical_skill_name(key)
    if canonical_query in char.skills:
        return char.skills[canonical_query]
    for k, v in char.skills.items():
        if canonical_skill_name(k) == canonical_query:
            return v

    norm = key.replace(" ", "").lower()
    for k, v in char.skills.items():
        kk = k.replace(" ", "").lower()
        if norm == kk or norm in kk or kk in norm:
            return v

    # Unknown skill: calculate its canonical base rate first. Registration
    # callers defer writing the character card until check admission succeeds.
    default_value = BASE_SKILLS.get(canonical_query, 20)
    if register_unknown:
        char.skills[canonical_query] = default_value
    return default_value
