"""Conservative, local PDF evidence checks; these do not prove semantic fidelity."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

VERSION = 'source-preserving-v1'
_NUMBER = re.compile(r'\b\d+(?:[dD]\d+(?:[+-]\d+)?|\.\d+)?%?\b')
_WORD = re.compile(r'[\w]+', re.UNICODE)


def normalize(text: str) -> str:
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+', ' ', text)).strip()


def native_text(page: Any) -> tuple[str, list[str]]:
    blocks = [b for b in page.get_text('blocks') if len(b) >= 7 and b[6] == 0 and b[4].strip()]
    midpoint = page.rect.width / 2
    # Only move whole blocks when the body has a clean central gutter. Headers
    # and footers stay outside the body; spanning tables/headings veto reordering.
    body = [b for b in blocks if b[1] >= page.rect.height * .08 and b[3] <= page.rect.height * .92]
    left = [b for b in body if b[2] <= midpoint]
    right = [b for b in body if b[0] >= midpoint]
    warnings = []
    if len(left) >= 3 and len(right) >= 3:
        if len(left) + len(right) == len(body):
            top = sorted([b for b in blocks if b not in body and b[1] < page.rect.height * .08], key=lambda b: (b[1], b[0]))
            bottom = sorted([b for b in blocks if b not in body and b not in top], key=lambda b: (b[1], b[0]))
            ordered = top + sorted(left, key=lambda b: (b[1], b[0])) + sorted(right, key=lambda b: (b[1], b[0])) + bottom
            return normalize('\n\n'.join(b[4] for b in ordered)), ['native_two_columns']
        warnings.append('ambiguous_columns')
    return normalize(page.get_text('text') or ''), warnings


def select_text(native: str, layout: str) -> tuple[str, str, list[str]]:
    if not layout.strip():
        return native, 'native', ['layout_unavailable']
    warnings = []
    numbers = Counter(x.lower() for x in _NUMBER.findall(native))
    if numbers - Counter(x.lower() for x in _NUMBER.findall(layout)):
        warnings.append('layout_numeric_loss')
    words = Counter(_WORD.findall(native.casefold()))
    count = sum(words.values())
    coverage = sum((words & Counter(_WORD.findall(layout.casefold()))).values()) / max(1, count)
    if count >= 20 and coverage < .85:
        warnings.append('layout_text_loss')
    if warnings:
        return native, 'native', warnings
    return layout, 'layout', []


def continuation(previous: str, current: str) -> bool:
    # Candidate only: no text deletion/join and no claim that a heading/footer
    # heuristic can reconstruct every publisher's reading order.
    def lines(text: str) -> list[str]:
        return [s.strip() for s in text.splitlines() if s.strip() and not s.strip().isdigit()]
    before, after = lines(previous), lines(current)
    while after and (after[0].isupper() or after[0].startswith('#')):
        after.pop(0)
    return bool(before and after and re.search(r'[a-z,;–-]$', before[-1])
                and re.match(r'^[a-z]', after[0]))
