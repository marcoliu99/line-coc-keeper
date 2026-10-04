"""Conservative, local PDF evidence checks; these do not prove semantic fidelity."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

VERSION = 'ai-import-repair-v5'
_NUMBER = re.compile(r'\b\d+(?:[dD]\d+(?:[+-]\d+)?|\.\d+)?%?\b')
_WORD = re.compile(r'[\w]+', re.UNICODE)
_RICH_ATOM = r'(?:\d+[dD]\d+(?:\s*[+-]\s*(?:\d+|[dD][bB]))?|\d+)'
_RICH_VALUE = rf'[+-]?{_RICH_ATOM}(?:\s*/\s*{_RICH_ATOM})?%?'
_RICH_STAT = re.compile(
    rf'(?<!\w)(STR|CON|SIZ|DEX|APP|INT|POW|EDU|HP|MP|SAN|LUCK|MOV|BUILD|ARMOR|DB|DAMAGE)'
    rf'\s*[:：|]?\s*({_RICH_VALUE})(?!\w)', re.IGNORECASE,
)
_RICH_FIELD = re.compile(rf'^\s*([A-Za-z][A-Za-z ]{{1,40}}?)\s*[:：|]?\s+({_RICH_VALUE})\s*$', re.IGNORECASE)


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
    if count and coverage < .85:
        warnings.append('layout_text_loss')
    if warnings:
        return native, 'native', warnings
    return layout, 'layout', []


def rich_ocr_mechanics(text: str) -> Counter[tuple[str, str]]:
    """Extract source-bound mechanics for preserving observable baseline values."""
    result: Counter[tuple[str, str]] = Counter()
    for line in text.splitlines():
        clean = re.sub(r'[*_`]', '', line).strip().strip('|').strip()
        if '|' in clean:
            cells = [cell.strip() for cell in clean.split('|')]
            if len(cells) >= 3 and any(char.isalpha() for char in cells[0]):
                label = cells[0].upper()
                for base in re.finditer(r'\((\d+%)\)', label):
                    result[(re.sub(r'\(\d+%\)', '', label).strip(), base.group(1).casefold())] += 1
                for column, cell in enumerate(cells[1:], 1):
                    if re.fullmatch(_RICH_VALUE, cell, re.IGNORECASE):
                        result[(f'{label}[{column}]', re.sub(r'\s', '', cell).casefold())] += 1
                continue
        matches = list(_RICH_STAT.finditer(clean))
        if matches:
            result.update((match.group(1).upper(), re.sub(r'\s', '', match.group(2)).casefold())
                          for match in matches)
            continue
        match = _RICH_FIELD.fullmatch(clean)
        if match:
            result[(match.group(1).strip().upper(), re.sub(r'\s', '', match.group(2)).casefold())] += 1
    return result


_PICTURE_MARKUP = re.compile(r'<!--\s*(?:start|end) of picture text\s*-->|<br\s*/?>', re.IGNORECASE)


def _source_tokens(value: str) -> list[str]:
    return _WORD.findall(value.casefold())


def _token_occurrences(haystack: list[str], needle: list[str]) -> int:
    return sum(haystack[index:index + len(needle)] == needle
               for index in range(len(haystack) - len(needle) + 1))


def _vertical_fragment(line: str, page_evidence: dict) -> bool:
    letters = line.split()
    if len(letters) < 3 or not all(len(letter) == 1 and letter.isalpha() for letter in letters):
        return False
    words = page_evidence.get('words', [])
    for start in range(len(words) - len(letters) + 1):
        part = words[start:start + len(letters)]
        if [word['text'] for word in part] != letters:
            continue
        if (max(word['bbox'][0] for word in part) - min(word['bbox'][0] for word in part) < 4
                and all(part[index + 1]['bbox'][1] > part[index]['bbox'][3]
                        for index in range(len(part) - 1))):
            return True
    return False


def _isolated_folio(line: str, page_evidence: dict) -> bool:
    if not line.isdecimal():
        return False
    height = page_evidence.get('height', 0)
    matches = [word for word in page_evidence.get('words', []) if word['text'] == line]
    return bool(height and len(matches) == 1 and matches[0]['bbox'][1] >= height * .92)


def select_rich_ocr_candidate(baseline: str, candidate: str, pairs: list[dict],
                              page_evidence: dict, threshold: int) -> dict:
    """Rescue a weak selected source without discarding any observable source."""
    result: dict[str, Any] = {'attempted': True, 'status': 'accepted', 'reason': 'weak_baseline_source_preserved',
              'baseline_strength': 'weak', 'baseline_chars': len(baseline), 'candidate_chars': len(candidate),
              'excluded_fragments': [], 'required_source_items': 0, 'preserved_source_items': 0,
              'numeric_conflicts': 0, 'mechanics_conflicts': 0, 'source_preserved': False,
              'candidate_extra_content_verified': False}

    def reject(reason: str) -> dict:
        result.update(status=reason, reason=reason)
        return result

    if len(baseline) >= threshold:
        result['baseline_strength'] = 'strong'
        return reject('not_low_text_source')
    if len(candidate) < threshold:
        return reject('candidate_too_short')
    if ('\ufffd' in candidate or re.search(r'(?<!\w)[lI|][dD]\d', candidate)
            or re.search(r'\bSAN\s+(?:\d+[dD]\d+|\d+)\s*/\s*(?=$|\D)', candidate, re.IGNORECASE)):
        return reject('mechanic_loss')

    required: list[str] = []
    for original in baseline.splitlines():
        line = _PICTURE_MARKUP.sub('', original).strip().strip('#*_`| ').strip()
        if line != original.strip() and _PICTURE_MARKUP.search(original):
            result['excluded_fragments'].append('picture_markup')
        if not line:
            continue
        if not _source_tokens(line):
            result['excluded_fragments'].append('decoration')
        elif _isolated_folio(line, page_evidence):
            result['excluded_fragments'].append('folio')
        elif _vertical_fragment(line, page_evidence):
            # Geometry proves the spacing is an artifact, not that the joined
            # word (which may be a name) is dispensable source.
            result['excluded_fragments'].append('vertical_spacing')
            required.append(''.join(line.split()))
        else:
            required.append(line)

    # A short coherent sentence is source, not a weak parser remnant.
    if any(len(_source_tokens(line)) >= 4 and re.search(r'[a-z][a-z]', line) for line in required):
        result['baseline_strength'] = 'strong'
        return reject('strong_baseline')

    result['required_source_items'] = len(required)
    if not required and not result['excluded_fragments']:
        return reject('insufficient_source_evidence')
    old = rich_ocr_mechanics('\n'.join(required))
    if not result['excluded_fragments'] and not old:
        result['baseline_strength'] = 'strong'
        return reject('strong_baseline')
    checks = check_pairs(pairs, candidate)
    if any(check['status'] != 'matched' for check in checks):
        result['numeric_conflicts'] = sum(check['status'] != 'matched' for check in checks)
        return reject('pair_mismatch')
    new = rich_ocr_mechanics(candidate)
    if old - new:
        result['mechanics_conflicts'] = (old - new).total()
        return reject('mechanic_loss')
    known_labels = {label for label, _ in old}
    known_values = {label: {value for old_label, value in old if old_label == label}
                    for label in known_labels}
    conflicting = sum(count for (label, value), count in new.items()
                      if label in known_labels and value not in known_values[label])
    if conflicting:
        result['mechanics_conflicts'] = conflicting
        return reject('pair_mismatch')
    candidate_tokens = _source_tokens(candidate)
    seen_lines: Counter[tuple[str, ...]] = Counter()
    for line in required:
        tokens = _source_tokens(line)
        seen_lines[tuple(tokens)] += 1
        if _token_occurrences(candidate_tokens, tokens) < seen_lines[tuple(tokens)]:
            return reject('source_content_loss')
        result['preserved_source_items'] += 1
    result['source_preserved'] = True
    return result


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


# Stat vocabulary anchors vertical tables; open skill labels are extracted separately.
_STAT_LABELS = {'STR', 'CON', 'SIZ', 'DEX', 'APP', 'INT', 'POW', 'EDU', 'HP', 'MP',
                'SAN', 'LUCK', 'MOV', 'BUILD', 'AGE', 'ARMOR', 'DB',
                '年齡', '年紀', '護甲', '幸運'}
_VALUE = re.compile(r'^[+-]?\d+(?:[dD]\d+(?:[+-]\d+)?|\.\d+)?%?(?:/\d+)*$')


def block_evidence(page: Any) -> dict:
    """Keep native coordinates independently of whichever text parser wins."""
    blocks = []
    for block in page.get_text('dict', flags=0)['blocks']:
        if block.get('type') != 0:
            continue
        lines = [{'bbox': list(line['bbox']), 'text': ''.join(span['text'] for span in line['spans'])}
                 for line in block.get('lines', [])]
        blocks.append({'id': block['number'], 'bbox': list(block['bbox']), 'lines': lines})
    words = [{'bbox': list(w[:4]), 'text': w[4], 'block': w[5], 'line': w[6], 'word': w[7]}
             for w in page.get_text('words')]
    return {'width': page.cropbox.width, 'height': page.cropbox.height, 'rotation': page.rotation,
            'coordinate_space': 'unrotated PyMuPDF page coordinates', 'blocks': blocks, 'words': words}


def numeric_pairs(evidence: dict) -> list[dict]:
    pairs = []
    words = evidence['words']
    for label in words:
        name = label['text'].strip(':：').upper()
        if name not in _STAT_LABELS:
            continue
        _x0, y0, x1, y1 = label['bbox']
        same_row = sorted([w for w in words if w is not label
                           and w['bbox'][0] >= x1 - 1
                           and w['bbox'][0] - x1 <= 80
                           and abs((w['bbox'][1] + w['bbox'][3] - y0 - y1) / 2) <= (y1 - y0) * .35],
                          key=lambda w: w['bbox'][0])
        # Stop at the first visible token; never cross another label or word.
        value = same_row[0] if same_row else None
        pair = {'label': name, 'label_bbox': label['bbox'], 'block': label['block'],
                'status': 'unresolved'}
        if value is not None and _VALUE.fullmatch(value['text']):
            tied = [w for w in same_row[1:] if abs(w['bbox'][0] - value['bbox'][0]) < 1]
            if not tied:
                pair.update(value=value['text'].casefold(), value_bbox=value['bbox'],
                            value_block=value['block'], status='same_row_candidate')
        if pair['status'] == 'unresolved':
            # A stat mention inside a sentence is not a blank character field.
            row = sorted([w for w in words if w['block'] == label['block']
                          and w['line'] == label['line']], key=lambda w: w['bbox'][0])
            tokens = [w['text'].strip(':：|') for w in row]
            field_row = all(t.upper() in _STAT_LABELS or _VALUE.fullmatch(t) or not t
                            for t in tokens)
            named_blank = (len(row) == 2 and row[-1] is label
                           and tokens[0].istitle())
            if not field_row and not named_blank:
                continue
        pairs.append(pair)
    _vertical_pairs(pairs, words)
    pairs.extend(_skill_pairs(words))
    return pairs


def _vertical_pairs(pairs: list[dict], words: list[dict]) -> None:
    proposed = []
    for pair in pairs:
        if pair['status'] != 'unresolved':
            continue
        box = pair['label_bbox']
        peers = [p for p in pairs if p is not pair and abs(p['label_bbox'][1] - box[1]) < 3]
        if not peers:
            continue
        below = [w for w in words if 0 <= w['bbox'][1] - box[3] <= 35
                 and box[0] - 3 <= (w['bbox'][0] + w['bbox'][2]) / 2 <= box[2] + 3]
        below.sort(key=lambda w: w['bbox'][1])
        if not below or not _VALUE.fullmatch(below[0]['text']):
            continue
        value = below[0]
        if any(abs(w['bbox'][1] - value['bbox'][1]) < 3 for w in below[1:]):
            continue
        proposed.append((pair, value))
    for pair, value in proposed:
        aligned = [(p, v) for p, v in proposed if abs(p['label_bbox'][1] - pair['label_bbox'][1]) < 3
                   and abs(v['bbox'][1] - value['bbox'][1]) < 3]
        if len(aligned) < 2 or sum(v['bbox'] == value['bbox'] for _, v in proposed) != 1:
            continue
        pair.update(value=value['text'].casefold(), value_bbox=value['bbox'],
                    value_block=value['block'], status='vertical_candidate')


def _skill_pairs(words: list[dict]) -> list[dict]:
    rows: dict[tuple, list[dict]] = {}
    for word in words:
        rows.setdefault((word['block'], word['line']), []).append(word)
    result = []
    for row in rows.values():
        label_words: list[dict] = []
        for word in sorted(row, key=lambda w: w['bbox'][0]):
            raw = word['text'].strip(',;，；')
            if re.fullmatch(r'\d{1,3}%', raw):
                label = ' '.join(w['text'] for w in label_words).strip(' :：,;')
                if (label and len(label_words) <= 8 and not re.search(r'\d|[.!?。！？]', label)
                        and label.upper() not in _STAT_LABELS):
                    bbox = [min(w['bbox'][0] for w in label_words), min(w['bbox'][1] for w in label_words),
                            max(w['bbox'][2] for w in label_words), max(w['bbox'][3] for w in label_words)]
                    result.append({'label': label.upper(), 'value': raw, 'label_bbox': bbox,
                                   'value_bbox': word['bbox'], 'block': word['block'],
                                   'value_block': word['block'], 'status': 'skill_candidate'})
                label_words = []
            elif re.search(r'\d', raw) or raw in {',', ';', '|'}:
                label_words = []
            else:
                label_words.append(word)
                if word['text'].endswith((',', ';', '，', '；')):
                    label_words = []
    return result


def check_pairs(pairs: list[dict], candidate: str) -> list[dict]:
    """Compare explicit pairs and header/value tables; do not guess ambiguous grids."""
    cleaned = re.sub(r'[*_`]', '', candidate)
    labels = '|'.join(re.escape(s) for s in sorted(_STAT_LABELS | {p['label'] for p in pairs}, key=len, reverse=True))
    pattern = re.compile(r'(?<!\w)(' + labels + r')(?!\w)[ \t:：|]*([+-]?\d+(?:[dD]\d+(?:[+-]\d+)?|\.\d+)?%?(?:/\d+)*)(?!\w)', re.IGNORECASE)
    found = Counter((m.group(1).upper(), m.group(2).casefold()) for m in pattern.finditer(cleaned))
    # Explicit Markdown columns: a header row, divider, then numeric cells.
    lines = cleaned.splitlines()
    for i in range(len(lines) - 2):
        headers = [c.strip().upper() for c in lines[i].strip().strip('|').split('|')]
        divider = [c.strip() for c in lines[i + 1].strip().strip('|').split('|')]
        cells = [c.strip().casefold() for c in lines[i + 2].strip().strip('|').split('|')]
        if (len(headers) >= 2 and len(headers) == len(divider) == len(cells)
                and all(re.fullmatch(r':?-+:?', c) for c in divider)):
            for label, value in zip(headers, cells, strict=True):
                if _VALUE.fullmatch(value):
                    found[(label, value)] += 1
    result = []
    for pair in pairs:
        if pair['status'] not in {'same_row_candidate', 'vertical_candidate', 'skill_candidate'}:
            result.append({'label': pair['label'], 'status': 'source_pair_unresolved', 'block': pair['block']})
            continue
        key = (pair['label'], pair['value'])
        status = 'matched'
        if found[key]:
            found[key] -= 1
        elif any(label == pair['label'] and count for (label, _), count in found.items()):
            status = 'pair_mismatch'
        else:
            status = 'candidate_pair_unverified'
        result.append({'label': pair['label'], 'value': pair['value'], 'block': pair['block'], 'status': status})
    return result


def accept_region(original: str, candidate: str, pairs: list[dict]) -> bool:
    """Only fix observable text corruption; uncertain numbers stay for review."""
    if '\ufffd' not in original or not candidate.strip() or '\ufffd' in candidate:
        return False
    # Ignore only the damaged token, not intact surrounding evidence.
    intact = re.sub(r'\S*\ufffd\S*', '', original)
    required = Counter(_WORD.findall(intact.casefold()))
    available = Counter(_WORD.findall(candidate.casefold()))
    if required - available:
        return False
    if Counter(n.casefold() for n in _NUMBER.findall(original)) != Counter(n.casefold() for n in _NUMBER.findall(candidate)):
        return False
    resolved = [p for p in pairs if p['status'] != 'unresolved']
    return all(p['status'] == 'matched' for p in check_pairs(resolved, candidate))
