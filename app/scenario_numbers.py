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


def mechanics_contexts(text: str, limit: int | None = None) -> list[tuple[str, str]]:
    """Every mechanics token in order of appearance, paired with its casefolded label ('' when it has none).

    With a ``limit`` the scan stops after ``limit + 1`` tokens, so a caller can tell "too many" from "exactly the limit"
    without ever holding an unbounded inventory.
    """
    pairs: list[tuple[str, str]] = []
    for match in MECHANICS.finditer(text):
        if limit is not None and len(pairs) > limit:
            break
        window_start = max(0, match.start() - _LABEL_WINDOW)
        line_start = max(text.rfind('\n', window_start, match.start()) + 1, window_start)
        label = _LABEL.search(text, line_start, match.start())
        context = ' '.join(word for word in label.groups() if word).casefold() if label else ''
        pairs.append((context, _canonical(match.group())))
    return pairs


# Above this many comparisons the remainder is reported whole instead of aligned: difflib is quadratic on unique
# sequences too, so no heuristic setting bounds it, while a page with this many changed numbers is unreadable anyway.
_EXACT_DIFF_CELLS = 1_000_000


def ordered_diff(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """What an ordered comparison removes from ``old`` and adds in ``new``; a block of swapped values is both.

    The common head and tail are matched first, so a page with a few changes costs little however many tokens it has.
    A remainder small enough is aligned exactly. A larger one is reported whole, which is conservative: it may list
    tokens that did not change, never hides one that did, and its cost is linear.
    """
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    middle_old, middle_new = old[head:len(old) - tail], new[head:len(new) - tail]
    if len(middle_old) * len(middle_new) > _EXACT_DIFF_CELLS:
        return middle_old, middle_new
    removed: list[tuple[str, str]] = []
    added: list[tuple[str, str]] = []
    for tag, a0, a1, b0, b1 in SequenceMatcher(None, middle_old, middle_new, autojunk=False).get_opcodes():
        if tag != 'equal':
            removed.extend(middle_old[a0:a1])
            added.extend(middle_new[b0:b1])
    return removed, added
