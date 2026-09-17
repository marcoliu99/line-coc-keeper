"""Self-learning bilingual dictionary (see docs/character_and_dictionary_system_spec.md's
Module 5) — app/models.py's BASE_SKILLS gives this project a closed, known set
of 46 official skill names, but an English scenario calls each of them
something else ("Spot Hidden" for 偵查, "Firearms (Handgun)" for 射擊（手槍）...).
Rather than re-translating at LLM-extraction time on every single upload (or
maintaining a large hand-written table that still misses anything
unanticipated), this keeps a small persisted lookup table that starts seeded
with the common/official skill names and grows via the learn_* functions
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

import threading

from app import db
from app.models import BASE_SKILLS

_TABLE = "dictionary"
_KEY = "global"

# Official COC7e skill names this project already has Chinese defaults for
# (see BASE_SKILLS) mapped to their common English names — seeded once so an
# English scenario's very first upload doesn't have to pay an LLM call (or go
# untranslated) for any of these. Keys are lowercased/whitespace-normalized;
# see _normalize below, which every lookup and learn call goes through.
_SEED_SKILLS: dict[str, str] = {
    "accounting": "會計",
    "anthropology": "人類學",
    "appraise": "鑑定",
    "archaeology": "考古學",
    "charm": "魅惑",
    "climb": "攀爬",
    "credit rating": "信用評級",
    "cthulhu mythos": "克蘇魯神話",
    "disguise": "偽裝",
    "drive auto": "汽車駕駛",
    "electrical repair": "電氣維修",
    "fast talk": "快速交談",
    "fighting (brawl)": "格鬥（鬥毆）",
    "brawl": "格鬥（鬥毆）",
    "firearms (handgun)": "射擊（手槍）",
    "handgun": "射擊（手槍）",
    "firearms (rifle/shotgun)": "射擊（步槍/霰彈槍）",
    "rifle/shotgun": "射擊（步槍/霰彈槍）",
    "first aid": "急救",
    "history": "歷史",
    "intimidate": "恐嚇",
    "jump": "跳躍",
    "language (other)": "其他語言",
    "law": "法律",
    "library use": "圖書館使用",
    "listen": "聆聽",
    "locksmith": "開鎖",
    "mechanical repair": "機械維修",
    "medicine": "醫學",
    "natural world": "自然學",
    "navigate": "導航",
    "occult": "神秘學",
    "operate heavy machinery": "重型機械操作",
    "persuade": "說服",
    "pilot": "駕駛",
    "psychology": "心理學",
    "psychoanalysis": "精神分析",
    "ride": "騎術",
    "sleight of hand": "妙手",
    "spot hidden": "偵查",
    "stealth": "潛行",
    "survival": "生存",
    "swim": "游泳",
    "throw": "投擲",
    "track": "追蹤",
    "computer use": "電腦使用",
    "science (biology)": "科學（生物）",
    "science (chemistry)": "科學（化學）",
    "science (physics)": "科學（物理）",
    "dodge": "閃避",
    "language (own)": "母語",
    "own language": "母語",
}

# occupations/character_aliases deliberately start empty — unlike skills,
# these aren't a closed official list (a scenario can invent any occupation
# or character name), so there's nothing sensible to pre-seed; they grow
# purely from what callers actually learn while resolving real uploads.
_INITIAL: dict[str, dict[str, str]] = {
    "skills": dict(_SEED_SKILLS),
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


def _normalize(term: str) -> str:
    return " ".join((term or "").strip().lower().split())


def _load() -> dict:
    data = db.get_json(_TABLE, _KEY)
    if data is None:
        data = {k: dict(v) for k, v in _INITIAL.items()}
        db.set_json(_TABLE, _KEY, data)
    return data


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


def lookup_occupation(name: str) -> str | None:
    return _lookup("occupations", name)


def learn_occupation(name: str, canonical: str) -> None:
    _learn("occupations", name, canonical)


def lookup_character_alias(name: str) -> str | None:
    return _lookup("character_aliases", name)


def learn_character_alias(name: str, canonical: str) -> None:
    _learn("character_aliases", name, canonical)
