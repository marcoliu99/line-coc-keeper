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
import logging
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app import db, observability

_TABLE = "dictionary"
_KEY = "global"
# Provenance of what was learned, kept in the same row: {category: {spelling: {"source": ..., "at": ...}}}.
_META = "_meta"

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


_WEAPONS = Path(__file__).with_name("data") / "combat_weapons.json"


def _weapon_entries() -> list[dict]:
    """The combat catalog's weapons as glossary entries: the catalog's English name, and the same name turned around
    ("Knife, Large" is also "Large Knife"), for the Chinese name the rulebook gives it. A row the rulebook names only
    as a class of weapons (name_zh_generic: 小型刀具) is left out: a text's "small knife" is better as 小刀."""
    try:
        weapons = json.loads(_WEAPONS.read_text(encoding="utf-8"))["weapons"]
    except (OSError, ValueError, KeyError):
        return []
    entries: list[dict] = []
    for weapon in weapons:
        if not isinstance(weapon, dict) or not weapon.get("name_zh") or weapon.get("name_zh_generic"):
            continue
        english = str(weapon["name"])
        head, _, rest = english.partition(", ")
        entry = {"category": "weapon", "en": english, "zh_tw": weapon["name_zh"], "zh_cn": weapon.get("name_zh_cn") or "",
                 "canonical": weapon["name_zh"], "sources": weapon.get("name_sources", [])}
        if rest and "(" not in english:
            entry["alt_en"] = [f"{rest} {head}"]
        entries.append(entry)
    return entries


def glossary_entries() -> list[dict]:
    """Every entry of the built-in glossary, the category files in name order, then the combat catalog's weapons; a
    file that cannot be read is skipped."""
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
    return [*entries, *_weapon_entries()]


def _table_of(category: object) -> str:
    return {"skill": "skills", "occupation": "occupations", "weapon": "weapons"}.get(str(category), "terms")


def _glossary_tables() -> dict[str, dict[str, str]]:
    """The glossary's spellings by table. A spelling of one character (斧, 劍) or one that two entries claim is left
    out: neither can name one thing for certain, and a skill's spellings never include a characteristic's."""
    entries = glossary_entries()
    characteristics = {_normalize(s) for e in entries if e.get("category") == "characteristic" for s in _spellings(e)}
    tables: dict[str, dict[str, str]] = {"skills": {}, "terms": {}, "occupations": {}, "weapons": {}}
    claimed: dict[str, set[str]] = {name: set() for name in tables}
    ambiguous: dict[str, set[str]] = {name: set() for name in tables}
    for entry in entries:
        canonical = str(entry.get("canonical") or "")
        table = _table_of(entry.get("category"))
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


# character_aliases deliberately start empty: a scenario can invent any character name, so there is nothing sensible to
# pre-seed; they grow purely from what callers actually learn while resolving real uploads. Occupations start from the
# glossary's list of CoC7 occupations and grow the same way.
_GLOSSARY_TABLES = _glossary_tables()
_INITIAL: dict[str, dict[str, str]] = {
    "skills": dict(_GLOSSARY_TABLES["skills"]),
    "terms": dict(_GLOSSARY_TABLES["terms"]),
    "occupations": dict(_GLOSSARY_TABLES["occupations"]),
    "weapons": dict(_GLOSSARY_TABLES["weapons"]),
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


def _stored() -> dict:
    data = db.get_json(_TABLE, _KEY)
    return data if isinstance(data, dict) else {}


def _learned(data: dict) -> dict[str, dict[str, str]]:
    """What this deployment has learned: the stored entries whose spelling the built-in glossary does not already
    settle. A built-in spelling always wins, whatever an older save holds for it, so a wrong translation learned
    earlier can no longer shadow the glossary and a corrected glossary entry takes effect on every deployment."""
    learned: dict[str, dict[str, str]] = {}
    for category, table in data.items():
        if category.startswith("_") or not isinstance(table, dict):
            continue
        built_in = _INITIAL.get(category, {})
        learned[category] = {k: v for k, v in table.items()
                             if isinstance(k, str) and isinstance(v, str) and v and k not in built_in}
    return learned


def _load() -> dict[str, dict[str, str]]:
    # Missing, or not the mapping this module writes: the built-in table still answers, since roll lines on every
    # reply look skill names up here.
    # Lookups must stay read-only: character matching also runs inside state transactions, where a second
    # connection cannot initialize this table without contending for the writer lock. _learn persists what was
    # learned when something is actually added.
    learned = _learned(_stored())
    return {c: {**_INITIAL.get(c, {}), **learned.get(c, {})} for c in {*_INITIAL, *learned}}


def _meta_of(data: dict) -> dict:
    meta = data.get(_META)
    return meta if isinstance(meta, dict) else {}


def _info(meta: dict, category: str, term: str, *, drop: bool = False) -> dict:
    """The provenance recorded for one learned spelling (removed from ``meta`` when ``drop``)."""
    table = meta.get(category)
    if not isinstance(table, dict):
        return {}
    info = table.pop(term, None) if drop else table.get(term)
    return info if isinstance(info, dict) else {}


def _lookup(category: str, term: str) -> str | None:
    return _load().get(category, {}).get(_normalize(term))


def _learn(category: str, term: str, canonical: str, source: str = "") -> bool:
    """Remember ``term`` → ``canonical`` for the whole deployment; True when something was stored.

    A spelling is settled once: the built-in glossary wins, then whatever was learned first, so the Chinese name
    stays the same every time. A pair that disagrees is not stored (it is logged), and a wrong pair that was stored
    can be removed with ``forget``. Only what was learned is saved; the built-in tables are not copied into the
    database, so editing the glossary files is enough to change them."""
    key, canonical = _normalize(term), (canonical or "").strip()
    if not key or not canonical or key == _normalize(canonical):
        return False
    with _lock:
        data = _stored()
        learned = _learned(data)
        built_in = _INITIAL.get(category, {}).get(key)
        known = built_in or learned.get(category, {}).get(key)
        if known:
            if known != canonical:
                observability.event(
                    "dictionary.learn.rejected", level=logging.INFO, category=category, term=key, kept=known,
                    offered=canonical, reason="built_in" if built_in else "learned_first", source=source)
            return False
        learned.setdefault(category, {})[key] = canonical
        meta = _meta_of(data)
        meta.setdefault(category, {})[key] = {"source": source, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        db.set_json(_TABLE, _KEY, {**learned, _META: meta})
    return True


def lookup_skill(name: str) -> str | None:
    return _lookup("skills", name)


def learn_skill(name: str, canonical: str, source: str = "pregen") -> bool:
    return _learn("skills", name, canonical, source)


def lookup_term(name: str) -> str | None:
    """The one Traditional Chinese name this project writes for a CoC7 term, characteristic, Mythos being, tome or
    place, from any spelling the glossary has (Necronomicon, 死灵之书 → 死靈之書)."""
    return _lookup("terms", name)


def lookup_weapon(name: str) -> str | None:
    """The rulebook's Traditional Chinese name of a weapon in the combat catalog, from any spelling. Asked for by name
    only: a weapon's English label is also a surname, a card game or a ship's bow, so it is never matched in a text."""
    return _lookup("weapons", name)


def lookup_occupation(name: str) -> str | None:
    return _lookup("occupations", name)


def learn_occupation(name: str, canonical: str, source: str = "pregen") -> bool:
    return _learn("occupations", name, canonical, source)


def lookup_character_alias(name: str) -> str | None:
    return _lookup("character_aliases", name)


def learn_character_alias(name: str, canonical: str, source: str = "match") -> bool:
    return _learn("character_aliases", name, canonical, source)


# Only lore that belongs to the game itself is learned for every scenario: a book, a spell, a group or a Mythos being
# keeps one Chinese name wherever it appears. A person or an item is the scenario's own, so its name stays there.
_LORE_KINDS = ("書籍", "法術", "組織", "神話")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
_ENGLISH_NAME = re.compile(r"[A-Za-z][A-Za-z0-9 .,'’&-]*[A-Za-z0-9]")


def learn_term(name: str, canonical: str, source: str = "scenario") -> bool:
    return _learn("terms", name, canonical, source)


def learn_terms(terms: Iterable[object], source: str = "scenario") -> int:
    """Teach the dictionary the lore terms a scenario's name list found, each under its fullest English spelling (a
    short alias such as 「John」 could belong to anyone). Returns how many were new."""
    taught = 0
    for term in terms:
        if not isinstance(term, dict):
            continue
        name, kind = str(term.get("name") or "").strip(), str(term.get("kind") or "")
        if not _HAS_CJK.search(name) or not any(word in kind for word in _LORE_KINDS):
            continue
        english = [a for a in (str(x).strip() for x in term.get("aliases") or []) if len(a) >= 4 and _ENGLISH_NAME.fullmatch(a)]
        if english:
            taught += learn_term(max(english, key=len), name, source)
    return taught


def glossary_hits(text: str, limit: int = 60) -> list[tuple[str, str]]:
    """The glossary terms (built-in and learned) that ``text`` uses, as ``(spelling in the text, the one name this
    project writes)``, in order of first use, one per name. Given to the model that reads a scenario so its
    translations start from the same names."""
    terms = _load().get("terms", {})
    # The glossary lists beings in the plural ("Deep Ones"); a scenario says "a Deep One" as often.
    singular = {k[:-1]: v for k, v in terms.items() if k.isascii() and len(k) > 5 and k.endswith("s") and not k.endswith("ss")}
    terms = {**singular, **terms}
    found: dict[str, tuple[int, str]] = {}
    english = sorted((k for k in terms if k.isascii()), key=len, reverse=True)
    if english:
        pattern = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k in english) + r")(?![A-Za-z0-9])", re.IGNORECASE)
        for match in pattern.finditer(text):
            found.setdefault(terms[_normalize(match.group(1))], (match.start(), match.group(1)))
    for spelling in (k for k in terms if not k.isascii()):
        at = text.find(spelling)
        if at >= 0 and terms[spelling] != spelling:
            found.setdefault(terms[spelling], (at, spelling))
    ordered = sorted(found.items(), key=lambda item: item[1][0])
    return [(spelling, canonical) for canonical, (_, spelling) in ordered[:limit]]


@dataclass(frozen=True)
class LearnedEntry:
    category: str
    term: str
    canonical: str
    source: str
    at: str


def learned_entries() -> list[LearnedEntry]:
    """Everything this deployment has learned, newest first."""
    data = _stored()
    meta = _meta_of(data)
    entries = []
    for category, table in _learned(data).items():
        for term, canonical in table.items():
            info = _info(meta, category, term)
            entries.append(LearnedEntry(category, term, canonical, str(info.get("source") or ""), str(info.get("at") or "")))
    return sorted(entries, key=lambda e: (e.at, e.term), reverse=True)


def built_in_name(term: str) -> str | None:
    """The built-in glossary's name for ``term`` (any category), when it has one."""
    key = _normalize(term)
    return next((table[key] for table in _INITIAL.values() if key in table), None)


def forget(term: str) -> list[LearnedEntry]:
    """Remove what was learned for ``term``, given as the spelling or the name it was learned as. The built-in
    glossary is not touched; the entries removed are returned."""
    key = _normalize(term)
    if not key:
        return []
    with _lock:
        data = _stored()
        learned = _learned(data)
        meta = _meta_of(data)
        removed = []
        for category, table in learned.items():
            for spelling in [k for k, v in table.items() if k == key or _normalize(v) == key]:
                info = _info(meta, category, spelling, drop=True)
                removed.append(LearnedEntry(category, spelling, table.pop(spelling), str(info.get("source") or ""), str(info.get("at") or "")))
        if removed:
            db.set_json(_TABLE, _KEY, {**learned, _META: meta})
    return removed
