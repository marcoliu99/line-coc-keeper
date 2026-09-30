"""Conservative reading order from immutable native PDF block evidence."""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

PIPELINE_VERSION = 'native-columns-v1'
# Pinned using the three supplied native-text golden pages. Formatting-only
# candidates score 1; deliberately ambiguous repeated passages have zero margin.
ALIGNMENT_MIN_SCORE = .94
ALIGNMENT_MIN_MARGIN = .08
_TOKEN = re.compile(r"\w+(?:['’]\w+)?", re.UNICODE)
_CRITICAL = re.compile(r"\b\d+(?:[dD]\d+(?:[+-]\d+)?|\.\d+)?%?|\b(?:not|no|never|without|cannot|don't|don’t)\b|[不無未沒]", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def _align(blocks: list[dict], text: str) -> dict:
    """Match source passages one to one, without rewriting candidate prose."""
    original = '\n'.join(b['text'] for b in blocks)
    critical = lambda value: Counter(x.casefold() for x in _CRITICAL.findall(value))
    if critical(original) != critical(text):
        return {'status': 'unresolved', 'reason': 'numeric_dice_or_negation_change'}
    tokens = _tokens(text)
    claimed: set[int] = set()
    matches = []
    for block in blocks:
        source = _tokens(block['text'])
        if not source:
            continue
        size = len(source)
        exact = [i for i in range(max(0, len(tokens) - size + 1)) if tokens[i:i + size] == source]
        scores = [(1., i) for i in exact]
        starts = [] if exact else range(max(0, len(tokens) - size + 1))
        for start in starts:
            window = tokens[start:start + size]
            score = SequenceMatcher(None, source, window, autojunk=False).ratio()
            scores.append((score, start))
        scores.sort(reverse=True)
        if not scores:
            return {'status': 'unresolved', 'reason': 'missing_text'}
        score, start = scores[0]
        # Adjacent windows describe the same passage; only disjoint passages
        # are competing alignments.
        runner = max((s for s, i in scores[1:] if abs(i - start) >= size), default=0.)
        positions = set(range(start, start + size))
        if score < ALIGNMENT_MIN_SCORE or score - runner < ALIGNMENT_MIN_MARGIN or claimed & positions:
            return {'status': 'unresolved', 'reason': 'ambiguous_alignment'}
        claimed.update(positions)
        matches.append({'id': block['id'], 'start': start, 'score': score, 'margin': score - runner})
    if Counter(_tokens(original)) != Counter(tokens):
        return {'status': 'unresolved', 'reason': 'source_text_change'}
    return {'status': 'aligned', 'ordered_ids': [m['id'] for m in sorted(matches, key=lambda m: m['start'])], 'matches': matches}


def apply_order(decision: dict, ordered_ids: list[str]) -> str:
    """Render a complete permutation of source blocks; reject lost/added IDs."""
    blocks = decision['blocks']
    ids = [b['id'] for b in blocks]
    if len(ids) != len(set(ids)) or Counter(ids) != Counter(ordered_ids):
        raise ValueError('Reading order must contain each source block exactly once')
    by_id = {b['id']: b['text'] for b in blocks}
    return '\n\n'.join(by_id[i].strip() for i in ordered_ids)


def analyze_page(page: Any, candidates: dict[str, str]) -> dict:
    """Accept clean native columns, or retain all evidence for bounded repair."""
    raw = [b for b in page.get_text('blocks') if len(b) >= 7 and b[6] == 0 and b[4].strip()]
    blocks = [{'id': f'b{i}', 'text': b[4], 'bbox': list(b[:4]), 'column': None, 'role': 'body'} for i, b in enumerate(raw)]
    width, height = page.cropbox.width, page.cropbox.height
    decision = {'status': 'not_applicable', 'selected_text': '', 'blocks': blocks, 'ordered_ids': [],
                'diagnostics': [], 'layout_kind': 'other', 'pipeline_version': PIPELINE_VERSION,
                'coordinate_space': 'unrotated PyMuPDF page coordinates', 'rotation': page.rotation,
                'dimensions': [width, height], 'candidate_alignment': {}}
    if not blocks:
        decision['diagnostics'].append('no_native_text_geometry')
        return decision
    # Text coordinates remain unrotated even when the displayed page is rotated.
    words = page.get_text('words')
    decision['words'] = [{'bbox': list(w[:4]), 'text': w[4], 'block': w[5], 'line': w[6], 'word': w[7]} for w in words]
    body_tokens = _tokens(' '.join(b['text'] for b in blocks if b['bbox'][1] < height * .92))
    numeric_density = sum(bool(re.match(r'^\d', t)) for t in body_tokens) / max(1, len(body_tokens))
    if numeric_density > .35:
        decision['diagnostics'].append('table_or_character_grid')
        return decision
    midpoint = width / 2
    left, right, spanning, margins = [], [], [], []
    for block in blocks:
        x0, y0, x1, _y1 = block['bbox']
        if y0 < height * .075 or y0 > height * .92 or x1 < width * .105 or x0 > width * .9:
            block['role'] = 'margin'
            margins.append(block)
        elif x1 <= midpoint:
            block['column'] = 'left'
            left.append(block)
        elif x0 >= midpoint:
            block['column'] = 'right'
            right.append(block)
        else:
            block['role'] = 'spanning'
            spanning.append(block)
    if len(left) < 2 or len(right) < 2:
        return decision
    decision['layout_kind'] = 'two_columns'
    decision['status'] = 'needs_review'
    # A real gutter must separate body text, with overlapping vertical ranges.
    gutter = min(b['bbox'][0] for b in right) - max(b['bbox'][2] for b in left)
    overlap = min(max(b['bbox'][3] for b in left), max(b['bbox'][3] for b in right)) - max(min(b['bbox'][1] for b in left), min(b['bbox'][1] for b in right))
    if gutter < width * .015 or overlap <= 0:
        decision['diagnostics'].append('ambiguous_gutter')
        return decision
    # Long spanning paragraphs/tables and overlapping floating regions cannot
    # be confidently promoted to headings.
    if any(len(_tokens(b['text'])) > 24 or any(c['bbox'][1] < b['bbox'][3] - 2 and c['bbox'][3] > b['bbox'][1] + 2 for c in left + right) for b in spanning):
        decision['diagnostics'].append('unsupported_spanning_region')
        return decision
    key = lambda b: (b['bbox'][1], b['bbox'][0], b['id'])
    ordered = sorted([b for b in margins if b['bbox'][1] < height * .075], key=key)
    remaining = left + right
    for heading in sorted(spanning, key=key):
        before = [b for b in remaining if b['bbox'][3] <= heading['bbox'][1] + 2]
        ordered.extend(sorted([b for b in before if b['column'] == 'left'], key=key))
        ordered.extend(sorted([b for b in before if b['column'] == 'right'], key=key))
        ordered.append(heading)
        remaining = [b for b in remaining if b not in before]
    ordered.extend(sorted([b for b in remaining if b['column'] == 'left'], key=key))
    ordered.extend(sorted([b for b in remaining if b['column'] == 'right'], key=key))
    ordered.extend(sorted([b for b in margins if b not in ordered], key=key))
    decision['ordered_ids'] = [b['id'] for b in ordered]
    decision['selected_text'] = apply_order(decision, decision['ordered_ids'])
    decision['status'] = 'accepted'
    decision['selected_candidate'] = 'native_geometry'
    for name, text in candidates.items():
        alignment = _align(blocks, text)
        decision['candidate_alignment'][name] = alignment
        if alignment['status'] != 'aligned':
            decision['diagnostics'].append(f'{name}:{alignment["reason"]}')
        elif alignment['ordered_ids'] != decision['ordered_ids']:
            decision['diagnostics'].append(f'{name}:reading_order_mismatch')
    return decision
