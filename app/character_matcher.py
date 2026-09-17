"""Cross-lingual "is this the same investigator" matcher (see docs/
character_and_dictionary_system_spec.md's Module 3) — used by app/commands.py's
pregen reconciliation (Module 4) to tell whether a newly-parsed pregen dict
(from either app/pregen_extractor.py's parse_role_sheet_text or
extract_pregens) describes the same character as one already sitting in
state.pregens, even when one side is in English and the other in Chinese.

Three independent gates, ANY of which passing counts as a match — matching
docs/character_and_dictionary_system_spec.md's design exactly:

1. Name: an exact/substring match between the two names (after stripping any
   "中文(English)" bracket-annotation into a separate alias to compare), OR a
   hit in app/dictionary.py's character_aliases from a PREVIOUS gate-2/3
   match. This deliberately does NOT attempt general phonetic transliteration
   ("Malcolm" vs "馬爾科姆") — extract_pregens' own schema instructs the LLM
   to keep a proper noun's original spelling rather than translate it, so two
   unrelated-looking scripts have no shared substring to find algorithmically
   without either a maintained phonetic table (high upkeep, still leaky) or
   an LLM call. Gate 2 is what actually carries a first-time, no-bracket
   cross-lingual name pair; this gate mainly exists for the reliable
   explicit-bracket case and for memoized pairs gate 2/3 already resolved.
2. Numeric fingerprint: >=7 of the 9 base attributes (STR/CON/SIZ/DEX/APP/
   INT/POW/EDU/LUCK) are exactly equal between records — language-independent
   by construction, since these are always plain integers regardless of which
   language extracted them. Compared only over attributes BOTH records
   actually have a value for (a missing attribute is not treated as "0" or
   any other stand-in) — this is what keeps two mostly-empty pregens that
   both merely happen to be missing the same fields from matching on
   coincidence; since a match requires 7 exact equalities, at least 7
   attributes have to be genuinely present and identical in both, not just
   absent in both.
3. Occupation + skills: occupations equal (directly, or via a previously-
   learned app/dictionary.py occupations pairing) AND at least 3 skills with
   identical values on both sides (skill names canonicalized via
   app/skill_aliases.py first, matching Chinese spelling variants — not yet
   wired to translate a still-English skill name at match time; see that
   module's own scope note).

On a gate-2 or gate-3 match, the name pair gets persisted into
app/dictionary.py's character_aliases (if it isn't a trivial gate-1 hit
already) — so the NEXT time either of these exact names shows up, gate 1
resolves it immediately without re-running the full comparison.
"""
from __future__ import annotations

import re
from typing import Any

from app import dictionary
from app.skill_aliases import canonical_skill_name

_ATTR_KEYS = ("str_", "con", "siz", "dex", "app", "int_", "pow_", "edu", "luck")
_FINGERPRINT_THRESHOLD = 7
_SKILL_MATCH_THRESHOLD = 3
_BRACKET_RE = re.compile(r"[（(]([^）)]+)[）)]")


def _name_variants(name: str) -> set[str]:
    """The name itself (with any bracket-alias suffix stripped) plus whatever
    text sits inside a bracket, if any — "卡特(Carter)" -> {"卡特", "Carter"}."""
    name = (name or "").strip()
    if not name:
        return set()
    variants = {name}
    base = _BRACKET_RE.sub("", name).strip()
    if base:
        variants.add(base)
    for m in _BRACKET_RE.finditer(name):
        alias = m.group(1).strip()
        if alias:
            variants.add(alias)
    return variants


def _variants_overlap(variants_a: set[str], variants_b: set[str]) -> bool:
    for va in variants_a:
        for vb in variants_b:
            if va == vb:
                return True
            shorter, longer = (va, vb) if len(va) <= len(vb) else (vb, va)
            # Require the shorter side to be at least 2 characters before
            # trusting a substring hit — a single shared CJK character (a
            # common surname, say) is far too weak a signal on its own.
            if len(shorter) >= 2 and shorter in longer:
                return True
    return False


def _names_match(name_a: str, name_b: str) -> bool:
    variants_a, variants_b = _name_variants(name_a), _name_variants(name_b)
    if not variants_a or not variants_b:
        return False
    if _variants_overlap(variants_a, variants_b):
        return True
    for va in variants_a:
        learned = dictionary.lookup_character_alias(va)
        if learned and _variants_overlap({learned}, variants_b):
            return True
    for vb in variants_b:
        learned = dictionary.lookup_character_alias(vb)
        if learned and _variants_overlap({learned}, variants_a):
            return True
    return False


def _fingerprint_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    matches = 0
    for key in _ATTR_KEYS:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            continue
        if va == vb:
            matches += 1
    return matches >= _FINGERPRINT_THRESHOLD


def _occupations_match(occ_a: str, occ_b: str) -> bool:
    a, b = (occ_a or "").strip(), (occ_b or "").strip()
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    learned_a, learned_b = dictionary.lookup_occupation(a), dictionary.lookup_occupation(b)
    return (bool(learned_a) and learned_a == b) or (bool(learned_b) and learned_b == a)


def _occupation_and_skills_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if not _occupations_match(a.get("occupation", ""), b.get("occupation", "")):
        return False
    skills_a = {canonical_skill_name(k): v for k, v in (a.get("skills") or {}).items()}
    skills_b = {canonical_skill_name(k): v for k, v in (b.get("skills") or {}).items()}
    matches = sum(1 for k, v in skills_a.items() if k in skills_b and skills_b[k] == v)
    return matches >= _SKILL_MATCH_THRESHOLD


def _remember_name_pair(a: dict[str, Any], b: dict[str, Any]) -> None:
    name_a, name_b = (a.get("name") or "").strip(), (b.get("name") or "").strip()
    if not name_a or not name_b or name_a == name_b:
        return
    # Store both directions — the dictionary's lookups are one-way, and
    # either name could be the one a future pregen shows up under first.
    dictionary.learn_character_alias(name_a, name_b)
    dictionary.learn_character_alias(name_b, name_a)


def is_same_character(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True if pregen dicts `a` and `b` are judged to describe the same
    investigator — see this module's docstring for the three gates. Any gate
    passing is sufficient; a gate-2/3 match (not already resolvable by gate 1
    alone) gets memoized into app/dictionary.py so the same name pair
    resolves instantly next time."""
    if _names_match(a.get("name", ""), b.get("name", "")):
        return True
    if _fingerprint_match(a, b):
        _remember_name_pair(a, b)
        return True
    if _occupation_and_skills_match(a, b):
        _remember_name_pair(a, b)
        return True
    return False
