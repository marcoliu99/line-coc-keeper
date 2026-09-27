"""Shared numeric contract for authoring inventories and translation checks."""
from __future__ import annotations

import re
from collections import Counter

# ASCII identifier boundaries admit Chinese adjacency without interpreting Chinese
# number words. Only horizontal dice-modifier spacing is normalized, never values.
NUMBER = re.compile(r'(?i)(?<![a-z0-9_])(?:\d+d\d+(?:[ \t]*[+-][ \t]*\d+)?|\d+(?:\.\d+)?%?)(?![a-z0-9_])')


def _canonical(value: str) -> str:
    return re.sub(r'[ \t]', '', value).casefold()


def tokens(text: str) -> list[str]:
    return [_canonical(match.group()) for match in NUMBER.finditer(text)]


def counts(text: str) -> Counter[str]:
    return Counter(tokens(text))


def missing(source: str, translation: str) -> list[str]:
    return sorted(set(tokens(source)) - set(tokens(translation)))


def contexts(text: str, selected: list[str]) -> list[dict]:
    """Bound excerpts per token while reporting the exact occurrence count."""
    rows: dict[str, dict] = {token: {'token': token, 'occurrences': 0, 'excerpts': []} for token in selected}
    for match in NUMBER.finditer(text):
        token = _canonical(match.group())
        if token in rows:
            row = rows[token]
            row['occurrences'] += 1
            if len(row['excerpts']) < 4:
                row['excerpts'].append({'offset': match.start(),
                                        'text': text[max(0, match.start()-80):match.end()+80]})
    return list(rows.values())
