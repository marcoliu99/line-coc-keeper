"""Self-learning bilingual dictionary (see docs/specs/feature/character_and_dictionary_system_spec.md) — app/models.py's BASE_SKILLS gives this project a closed, known set
of 46 official skill names, but an English scenario calls each of them
something else ("Spot Hidden" for 偵查, "Firearms (Handgun)" for 射擊（手槍）...).
Rather than re-translating at LLM-extraction time on every single upload (or
maintaining a large hand-written table that still misses anything
unanticipated), this keeps a persisted lookup table that starts from the CoC7
glossary in app/data/coc7_glossary/ (data files, one per category) and grows via the learn_* functions
whenever a caller resolves something new for the first time — an occupation,
a skill, or (app/character_matcher.py's identity gates) a cross-lingual
character name pair — so that translation only ever has to be figured out
once, the first time it's seen, for the whole deployment.

Persisted via app/db.py (one JSON blob under its own "dictionary" table)
rather than a standalone data/dictionary.json file, for the same atomicity
reason the rest of this project moved off flat files onto SQLite — see
db.py's own module docstring. There's exactly one row ("global"): this
dictionary isn't per-group data, it's a shared vocabulary the whole
deployment benefits from learning once.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app import db

_TABLE = "dictionary"
_KEY = "global"

def _normalize(term: str) -> str:
    return " ".join((term or "").strip().lower().split())


# The built-in vocabulary is data, not code: app/data/coc7_glossary/ holds one JSON file per category (skills,
# characteristics, rules, mythos, tomes_places, one entry per line) and sources.json says where each translation came
# from. Every spelling a skill entry carries (English, Traditional and Simplified Chinese, and the alternatives) resolves
# to its "canonical", this project's own skill name, so a player who types 侦查, 話術 or 喬裝 reaches 偵查, 快速交談 or
# 偽裝; the other categories form the "terms" table, each spelling to the one Traditional name this project writes. To
# add a term, add a line to the right file.
_GLOSSARY = Path(__file__).with_name("data") / "coc7_glossary"


def _spellings(entry: dict) -> list[str]:
    return [str(s) for s in (entry.get("en"), *(entry.get("alt_en") or []), entry.get("zh_tw"), entry.get("zh_cn"),
                             *(entry.get("alt_zh_tw") or []), *(entry.get("alt_zh_cn") or [])) if s]


def glossary_entries() -> list[dict]:
    """Every entry of the built-in glossary, the category files in name order; a file that cannot be read is skipped."""
    entries: list[dict] = []
    for path in sorted(_GLOSSARY.glob("*.json")):
        if path.name == "sources.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            entries.extend(e for e in data if isinstance(e, dict))
    return entries


def _glossary_tables() -> dict[str, dict[str, str]]:
    """The glossary's spellings by table. A spelling of one character (斧, 劍) or one that two entries claim is left
    out: neither can name one thing for certain, and a skill's spellings never include a characteristic's."""
    entries = glossary_entries()
    characteristics = {_normalize(s) for e in entries if e.get("category") == "characteristic" for s in _spellings(e)}
    tables: dict[str, dict[str, str]] = {"skills": {}, "terms": {}}
    claimed: dict[str, set[str]] = {"skills": set(), "terms": set()}
    ambiguous: dict[str, set[str]] = {"skills": set(), "terms": set()}
    for entry in entries:
        canonical = str(entry.get("canonical") or "")
        table = "skills" if entry.get("category") == "skill" else "terms"
        if not canonical:
            continue
        for spelling in _spellings(entry):
            key = _normalize(spelling)
            if len(key) < 2:
                continue
            if table == "skills" and key in characteristics:
                continue
            if key in claimed[table] and tables[table][key] != canonical:
                ambiguous[table].add(key)
            claimed[table].add(key)
            tables[table][key] = canonical
    for table, keys in ambiguous.items():
        for key in keys:
            tables[table].pop(key, None)
    return tables


# occupations/character_aliases deliberately start empty — unlike skills,
# these aren't a closed official list (a scenario can invent any occupation
# or character name), so there's nothing sensible to pre-seed; they grow
# purely from what callers actually learn while resolving real uploads.
_GLOSSARY_TABLES = _glossary_tables()
_INITIAL: dict[str, dict[str, str]] = {
    "skills": dict(_GLOSSARY_TABLES["skills"]),
    "terms": dict(_GLOSSARY_TABLES["terms"]),
    "occupations": {},
    "character_aliases": {},
}

# Guards the load-modify-save in every learn_* call below — without it, two
# concurrent learns (e.g. two different groups' PDF uploads both teaching the
# dictionary a new skill name at the same moment) could each load the same
# snapshot and the second save would silently discard the first one's entry.
# A single process-wide lock is fine here (unlike app/locks.py's per-
# conversation locks): this dictionary is shared, not per-group, and every
# learn call is a small, fast, in-memory dict update plus one db.set_json.
_lock = threading.Lock()


def _load() -> dict:
    data = db.get_json(_TABLE, _KEY)
    if not isinstance(data, dict):
        # Missing, or not the mapping this module writes: the built-in table still answers, since roll lines on
        # every reply look skill names up here.
        # Lookups must stay read-only: character matching also runs inside
        # state transactions, where a second connection cannot initialize
        # this table without contending for the writer lock. _learn persists
        # the initial dictionary when an alias is actually added.
        data = {}
    # The built-in tables sit under what this deployment has stored or learned, which wins: a dictionary saved before
    # the glossary existed still answers its spellings.
    return {k: {**_INITIAL.get(k, {}), **(v if isinstance(v, dict) else {})}
            for k, v in {**{k: {} for k in _INITIAL}, **data}.items()}


def _lookup(category: str, term: str) -> str | None:
    return _load().get(category, {}).get(_normalize(term))


def _learn(category: str, term: str, canonical: str) -> None:
    key = _normalize(term)
    if not key or not canonical:
        return
    with _lock:
        data = _load()
        data.setdefault(category, {})[key] = canonical
        db.set_json(_TABLE, _KEY, data)


def lookup_skill(name: str) -> str | None:
    return _lookup("skills", name)


def learn_skill(name: str, canonical: str) -> None:
    _learn("skills", name, canonical)


def lookup_term(name: str) -> str | None:
    """The one Traditional Chinese name this project writes for a CoC7 term, characteristic, Mythos being, tome or
    place, from any spelling the glossary has (Necronomicon, 死灵之书 → 死靈之書)."""
    return _lookup("terms", name)


def lookup_occupation(name: str) -> str | None:
    return _lookup("occupations", name)


def learn_occupation(name: str, canonical: str) -> None:
    _learn("occupations", name, canonical)


def lookup_character_alias(name: str) -> str | None:
    return _lookup("character_aliases", name)


def learn_character_alias(name: str, canonical: str) -> None:
    _learn("character_aliases", name, canonical)
