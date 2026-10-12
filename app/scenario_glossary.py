"""Merging a scenario's glossary of proper names (see app/scenario_index.py, which extracts it). Kept apart from the
extraction so that scenario activation can merge without importing the LLM provider."""
from __future__ import annotations

from typing import Any


def merge_glossary(kept: list[dict[str, Any]], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``kept`` with what a rebuild ``found`` added: a rebuild from part of a scenario never drops the names it did not
    see, and a term it found again keeps the Chinese name it already had (the point is that a name reads the same every
    time) and gains the new spellings. A term is the same one when its Chinese name is already a spelling of that term,
    or every original spelling it has is; one shared alias (「John」, 「Chief Investigator」) never makes two people one."""
    def key(text: Any) -> str:
        return str(text or "").strip().casefold()

    merged = [dict(t) for t in kept if isinstance(t, dict) and key(t.get("name"))]
    for term in found:
        if not isinstance(term, dict) or not key(term.get("name")):
            continue
        originals = {key(a) for a in term.get("aliases") or [] if key(a)}

        def spellings(t: dict[str, Any]) -> set[str]:
            return {key(t.get("name")), *(key(a) for a in t.get("aliases") or [])}
        same = next((t for t in merged if key(term.get("name")) in spellings(t)
                     or (originals and originals <= spellings(t))), None)
        if same is None:
            merged.append(dict(term))
            continue
        aliases = [str(a) for a in same.get("aliases") or []]
        aliases += [str(a) for a in (term.get("name"), *(term.get("aliases") or []))
                    if key(a) and key(a) != key(same.get("name")) and key(a) not in {key(b) for b in aliases}]
        same["aliases"] = aliases
    return merged
