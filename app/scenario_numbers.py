"""Shared numeric contract for authoring inventories and translation checks."""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

# ASCII identifier boundaries admit Chinese adjacency without interpreting Chinese
# number words. Only horizontal dice-modifier spacing is normalized, never values.
NUMBER = re.compile(r'(?i)(?<![a-z0-9_])(?:\d+d\d+(?:[ \t]*[+-][ \t]*\d+)?|\d+(?:\.\d+)?%?)(?![a-z0-9_])')


_CORE = r'(?:\d+d\d+(?:[ \t]*[+-][ \t]*\d+)?|\d+(?:\.\d+)?%?)'
# A mechanics token keeps a sign written directly before the number, even glued to a word ("STR+10"), and joins operands
# separated by / - en dash or minus, so "+10%" differs from "-10%" and "1/1d6" from "1 1d6", which `NUMBER` cannot tell
# apart. A hyphenated label such as "A-10" therefore yields "-10"; unchanged text never produces a delta.
MECHANICS = re.compile(
    r'(?i)(?:[+\-\u2212])?(?<![a-z0-9_])' + _CORE
    + r'(?:[ \t]*[/\-\u2013\u2212][ \t]*' + _CORE + r')*(?![a-z0-9_])')
# The label of a token: up to two words (letters or CJK characters) directly before it on the same line, separated only
# by blanks, and an optional ":" or "=" between the last word and the number. A number or other punctuation ends it.
_LABEL = re.compile(r'(?:([^\W\d_]+)[ \t]+)?([^\W\d_]+)[ \t:\uff1a=]*$')


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


def mechanics_tokens(text: str) -> list[str]:
    return [_canonical(match.group()) for match in MECHANICS.finditer(text)]


def mechanics_counts(text: str) -> Counter[str]:
    return Counter(mechanics_tokens(text))


_LABEL_WINDOW = 120  # a label is at most two words on one line; looking further back is never needed


def mechanics_contexts(text: str) -> list[tuple[str, str]]:
    """Every mechanics token in order of appearance, paired with its casefolded label ('' when it has none)."""
    pairs = []
    for match in MECHANICS.finditer(text):
        window_start = max(0, match.start() - _LABEL_WINDOW)
        line_start = max(text.rfind('\n', window_start, match.start()) + 1, window_start)
        label = _LABEL.search(text, line_start, match.start())
        context = ' '.join(word for word in label.groups() if word).casefold() if label else ''
        pairs.append((context, _canonical(match.group())))
    return pairs


def ordered_diff(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """What an ordered comparison removes from ``old`` and adds in ``new``; a block of swapped values is both.

    When a pair is both removed and added, a value moved and difflib may have matched the moved copy as unchanged, so
    the whole region from the first to the last change is listed instead of dropping it.
    """
    changes = [op for op in SequenceMatcher(None, old, new).get_opcodes() if op[0] != 'equal']
    if not changes:
        return [], []
    removed = [pair for _, a0, a1, _, _ in changes for pair in old[a0:a1]]
    added = [pair for _, _, _, b0, b1 in changes for pair in new[b0:b1]]
    if set(removed) & set(added):
        return old[changes[0][1]:changes[-1][2]], new[changes[0][3]:changes[-1][4]]
    return removed, added
