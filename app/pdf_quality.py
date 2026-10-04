"""Conservative, local PDF evidence checks; these do not prove semantic fidelity."""
from __future__ import annotations

import difflib
import hashlib
import re
import statistics
from collections import Counter
from typing import Any, Literal

VERSION = 'ai-import-repair-v6'
_NUMBER = re.compile(r'\b\d+(?:[dD]\d+(?:[+-]\d+)?|\.\d+)?%?\b')
_WORD = re.compile(r'[\w]+', re.UNICODE)
_MECHANIC_ATOM = r'(?:\d+\s*[dD]\s*\d+(?:\s*[+-]\s*(?:\d+|[dD][bB]))?|\d+)'
_MECHANIC_EXPRESSION = re.compile(
    rf'(?<![\w%+/\-])(?:{_MECHANIC_ATOM}\s*/\s*{_MECHANIC_ATOM}|'
    rf'\d+\s*[dD]\s*\d+(?:\s*[+-]\s*(?:\d+|[dD][bB]))?|'
    r'[+-][ \t]*\d+(?:\.\d+)?[ \t]*%?|\d+(?:\.\d+)?\s*%)'
    r'(?![\w%+/\-]|[ \t]*[+/\-][ \t]*(?:\d|[dD]))',
    re.IGNORECASE,
)
_SOURCE_TOKEN = re.compile(rf'{_MECHANIC_EXPRESSION.pattern}|[\w]+', re.IGNORECASE | re.UNICODE)
_VERTICAL_LETTERS = re.compile(r'(?:[A-Za-z]\s+){2,}[A-Za-z]')
_RICH_ATOM = r'(?:\d+[dD]\d+(?:\s*[+-]\s*(?:\d+|[dD][bB]))?|\d+)'
_RICH_VALUE = rf'[+-]?{_RICH_ATOM}(?:\s*/\s*{_RICH_ATOM})?%?'
_RICH_STAT = re.compile(
    rf'(?<!\w)(STR|CON|SIZ|DEX|APP|INT|POW|EDU|HP|MP|SAN|LUCK|MOV|BUILD|ARMOR|DB|DAMAGE)'
    rf'\s*[:：|]?\s*({_RICH_VALUE})(?![\w%+/\-]|[ \t]*[+/\-][ \t]*(?:\d|[dD]))', re.IGNORECASE,
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
    return [re.sub(r'\s+', '', match.group()).casefold() for match in _SOURCE_TOKEN.finditer(value)]


def _mechanic_tokens(value: str) -> Counter[str]:
    return Counter(re.sub(r'\s+', '', match.group()).casefold()
                   for match in _MECHANIC_EXPRESSION.finditer(value))


def _token_occurrences(haystack: list[str], needle: list[str]) -> int:
    return sum(haystack[index:index + len(needle)] == needle
               for index in range(len(haystack) - len(needle) + 1))


def _vertical_words(line: str, page_evidence: dict) -> list[dict] | None:
    letters = line.split()
    if not _VERTICAL_LETTERS.fullmatch(line):
        return None
    words = page_evidence.get('words', [])
    matches = []
    for start in range(len(words) - len(letters) + 1):
        part = words[start:start + len(letters)]
        if [word['text'] for word in part] == letters:
            matches.append(part)
    return matches[0] if len(matches) == 1 else None


def _vertical_fragment(line: str, page_evidence: dict) -> bool:
    """Reconstruct semantic text only inside one coherent text container."""
    part = _vertical_words(line, page_evidence)
    if not part or len({word.get('block') for word in part}) != 1:
        return False
    containers = [block for block in page_evidence.get('blocks', []) if block['id'] == part[0]['block']]
    if len(containers) != 1:
        return False
    container = containers[0]
    if [item['text'].strip() for item in container['lines']] != [word['text'] for word in part]:
        return False
    fonts = {word.get('font') for word in part}
    sizes = [word.get('font_size') for word in part]
    if len(fonts) != 1 or None in fonts or any(not isinstance(size, (int, float)) or size <= 0
                                               for size in sizes):
        return False
    size_values = [float(size) for size in sizes if isinstance(size, (int, float))]
    if max(size_values) > min(size_values) * 1.1:
        return False
    if container['bbox'][2] - container['bbox'][0] > max(size_values) * 2:
        return False
    return (max(word['bbox'][0] for word in part) - min(word['bbox'][0] for word in part)
            <= max(4, min(size_values) * .2)
            and all(part[index + 1]['line'] > part[index]['line']
                    and 0 <= part[index + 1]['bbox'][1] - part[index]['bbox'][3] <= min(size_values)
                    for index in range(len(part) - 1)))


def _decorative_vertical_fragment(line: str, page_evidence: dict) -> bool:
    """Require repeated margin placement, isolated display font and source binding."""
    part = _vertical_words(line, page_evidence)
    if not part:
        return False
    repeated = set(page_evidence.get('repeated_vertical_signatures', []))
    joined = ''.join(line.split()).casefold()
    for span in page_evidence.get('vertical_spans', []):
        if (span['signature'] not in repeated or span['joined'] != joined
                or not span['margin'] or not span['large'] or span['body_font_used']):
            continue
        x0, y0, x1, y1 = span['bbox']
        if all(word.get('font') == span['font']
               and isinstance(word.get('font_size'), (int, float))
               and abs(word['font_size'] - span['size']) <= span['size'] * .1
               and x0 - 2 <= word['bbox'][0] and word['bbox'][2] <= x1 + 2
               and y0 - 2 <= word['bbox'][1] and word['bbox'][3] <= y1 + 2 for word in part):
            return True
    return False


def _vertical_fragment_role(
    line: str, page_evidence: dict,
) -> Literal['decorative', 'semantic', 'ambiguous'] | None:
    if not _VERTICAL_LETTERS.fullmatch(line):
        return None
    if _decorative_vertical_fragment(line, page_evidence):
        return 'decorative'
    return 'semantic' if _vertical_fragment(line, page_evidence) else 'ambiguous'


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
            or re.search(r'(?<!\w)[+-][ \t]*[+-][ \t]*\d', candidate)
            or re.search(r'\bSAN\s+(?:\d+[dD]\d+|\d+)\s*/\s*(?=$|\D)', candidate, re.IGNORECASE)):
        return reject('mechanic_loss')

    required: list[str] = []
    ambiguous_vertical = False
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
        else:
            role = _vertical_fragment_role(line, page_evidence)
            if role == 'decorative':
                result['excluded_fragments'].append('decorative_vertical_glyph')
            elif role == 'semantic':
                # Geometry proves the spacing is an artifact, not that the joined
                # word (which may be a name) is dispensable source.
                result['excluded_fragments'].append('vertical_spacing')
                required.append(''.join(line.split()))
            elif role == 'ambiguous':
                ambiguous_vertical = True
            else:
                required.append(line)

    if ambiguous_vertical:
        return reject('insufficient_source_evidence')

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
    required_mechanics = _mechanic_tokens('\n'.join(required))
    missing_mechanics = required_mechanics - _mechanic_tokens(candidate)
    if missing_mechanics:
        result['mechanics_conflicts'] = missing_mechanics.total()
        return reject('mechanic_loss')
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
    line_styles = {}
    for block in page.get_text('dict', flags=0)['blocks']:
        if block.get('type') != 0:
            continue
        lines = []
        for index, line in enumerate(block.get('lines', [])):
            styles = {(span['font'], round(span['size'], 2)) for span in line['spans']}
            if len(styles) == 1:
                line_styles[(block['number'], index)] = next(iter(styles))
            lines.append({'bbox': list(line['bbox']), 'text': ''.join(span['text'] for span in line['spans'])})
        blocks.append({'id': block['number'], 'bbox': list(block['bbox']), 'lines': lines})
    words = [{'bbox': list(w[:4]), 'text': w[4], 'block': w[5], 'line': w[6], 'word': w[7],
              'font': line_styles.get((w[5], w[6]), (None, None))[0],
              'font_size': line_styles.get((w[5], w[6]), (None, None))[1]}
             for w in page.get_text('words')]
    try:
        traces = page.get_texttrace()
    except (AttributeError, RuntimeError, ValueError):
        traces = []
    width, height = page.cropbox.width, page.cropbox.height
    body = [span for span in traces if span['bbox'][0] >= width * .12
            and span['bbox'][2] <= width * .88]
    body_fonts = {span['font'] for span in body}
    body_size = statistics.median(span['size'] for span in body) if body else None
    vertical_spans = []
    for span in traces:
        raw = ''.join(chr(char[0]) for char in span['chars'] if 0 <= char[0] <= 0x10ffff).strip()
        if not _VERTICAL_LETTERS.fullmatch(raw):
            continue
        x0, y0, x1, y1 = span['bbox']
        if y1 - y0 < (x1 - x0) * 2:
            continue
        joined = ''.join(raw.split()).casefold()
        position = tuple(round(value / bound, 3) for value, bound in zip(
            (x0, y0, x1, y1), (width, height, width, height), strict=True))
        identity = f"{joined}|{span['font']}|{span['size']:.2f}|{position}"
        vertical_spans.append({'joined': joined, 'font': span['font'], 'size': span['size'],
                               'bbox': list(span['bbox']),
                               'signature': hashlib.sha256(identity.encode()).hexdigest()[:20],
                               'margin': x1 <= width * .12 or x0 >= width * .88,
                               'large': bool(body_size and span['size'] >= body_size * 1.5),
                               'body_font_used': span['font'] in body_fonts})
    return {'width': page.cropbox.width, 'height': page.cropbox.height, 'rotation': page.rotation,
            'coordinate_space': 'unrotated PyMuPDF page coordinates', 'blocks': blocks, 'words': words,
            'vertical_spans': vertical_spans}


def bind_repeated_vertical_evidence(pages: list[dict]) -> None:
    """Bind only signatures repeated on distinct pages; never infer from one page."""
    counts: Counter[str] = Counter()
    for row in pages:
        counts.update({span['signature'] for span in row['evidence'].get('vertical_spans', [])})
    for row in pages:
        evidence = row['evidence']
        evidence['repeated_vertical_signatures'] = [span['signature'] for span in evidence.get('vertical_spans', [])
                                                    if counts[span['signature']] >= 3]


def _alnum_alignment(value: str) -> tuple[str, list[int]]:
    """Use punctuation-free characters only to locate edits, never to validate mechanics."""
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(value):
        folded = char.casefold()
        if char.isalnum():
            chars.append(folded if len(folded) == 1 else char)
            offsets.append(index)
    return ''.join(chars), offsets


def _qualified_decorative_span(evidence: dict) -> tuple[dict, list[str], list[dict]] | None:
    """Bind a repeated margin trace to isolated glyph blocks and adjacent body blocks."""
    width = evidence.get('width', 0)
    repeated = set(evidence.get('repeated_vertical_signatures', []))
    candidates = []
    for span in evidence.get('vertical_spans', []):
        if (span['signature'] not in repeated or not span['margin'] or not span['large']
                or span['body_font_used'] or not width):
            continue
        x0, y0, x1, y1 = span['bbox']
        glyph_words = sorted((word for word in evidence.get('words', [])
                              if word.get('font') == span['font']
                              and len(word['text'].strip()) == 1
                              and x0 - 2 <= word['bbox'][0] and word['bbox'][2] <= x1 + 2
                              and y0 - 2 <= word['bbox'][1] and word['bbox'][3] <= y1 + 2),
                             key=lambda word: (word['bbox'][1], word['bbox'][0]))
        glyphs = [word['text'].strip() for word in glyph_words]
        if (len(glyphs) < 3 or ''.join(glyphs).casefold() != span['joined']
                or any(abs(word.get('font_size', 0) - span['size']) > span['size'] * .1
                       for word in glyph_words)
                or any(glyph_words[i + 1]['bbox'][1] < glyph_words[i]['bbox'][1]
                       or glyph_words[i + 1]['bbox'][1] - glyph_words[i]['bbox'][3] > span['size']
                       for i in range(len(glyph_words) - 1))):
            continue
        glyph_blocks = {word['block'] for word in glyph_words}
        containers = [block for block in evidence['blocks'] if block['id'] in glyph_blocks]
        if (len(containers) != len(glyph_blocks)
                or any(any((line['bbox'][0] >= x0 - 2 and line['bbox'][2] <= x1 + 2
                            and len(_alnum_alignment(line['text'])[0]) != 1)
                           or (line['bbox'][2] > x1 + 2 and line['bbox'][0] <= x1 + width * .08)
                           for line in block['lines']) for block in containers)):
            continue
        body = sorted((block for block in evidence['blocks']
                       if block['id'] not in glyph_blocks
                       and x1 < block['bbox'][0] <= x1 + width * .08
                       and min(y1, block['bbox'][3]) > max(y0, block['bbox'][1])
                       and any(len(_alnum_alignment(line['text'])[0]) >= 20 for line in block['lines'])),
                      key=lambda block: (block['bbox'][1], block['bbox'][0]))
        if (not body or max(block['bbox'][0] for block in body) - min(block['bbox'][0] for block in body) > width * .03
                or any(body[i + 1]['bbox'][1] - body[i]['bbox'][3] > span['size'] * 2
                       for i in range(len(body) - 1))):
            continue
        candidates.append((span, glyphs, body))
    return candidates[0] if len(candidates) == 1 else None


def repair_decorative_layout(layout: str, evidence: dict, *, warnings: list[str],
                             method: str, pairs: list[dict]) -> tuple[str, dict]:
    """Remove only uniquely aligned, source-bound margin glyph insertions."""
    result = {'status': 'not_applicable', 'removed_glyphs': 0}
    if method != 'layout' or 'ambiguous_columns' not in warnings:
        return layout, result
    qualified = _qualified_decorative_span(evidence)
    if qualified is None:
        return layout, result
    span, glyphs, body = qualified
    result['status'] = 'ambiguous_alignment'
    reference = ' '.join(line['text'] for block in body for line in block['lines'])
    source, _ = _alnum_alignment(reference)
    candidate, offsets = _alnum_alignment(layout)
    if len(source) < 24 or len(candidate) < len(source):
        return layout, result
    anchor_length = 8
    start_anchor, end_anchor = source[:anchor_length], source[-anchor_length:]
    if candidate.count(start_anchor) != 1 or candidate.count(end_anchor) != 1:
        return layout, result
    start_index = candidate.index(start_anchor)
    end_index = candidate.index(end_anchor)
    if end_index <= start_index:
        return layout, result
    first_raw = offsets[start_index]
    previous_paragraph = layout.rfind('\n\n', 0, first_raw)
    start_raw = previous_paragraph + 2 if previous_paragraph >= 0 else 0
    end_raw = offsets[end_index + anchor_length - 1] + 1
    if end_raw <= start_raw or end_raw - start_raw > len(reference) * 3 + 200:
        return layout, result
    window = layout[start_raw:end_raw]
    sequence, mapping = _alnum_alignment(window)
    edits: list[int] = []
    residual_insertion = False
    expected = [glyph.casefold() for glyph in glyphs]
    for tag, source_start, source_end, target_start, target_end in difflib.SequenceMatcher(
            None, source, sequence, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        if tag != 'insert' or len(edits) >= len(expected):
            return layout, result
        inserted = sequence[target_start:target_end]
        if not inserted.startswith(expected[len(edits)]):
            return layout, result
        # An already-duplicated body prefix may share this inserted run. It
        # remains untouched; only the first, independently bound glyph is cut.
        remainder = inserted[1:]
        if remainder and (len(remainder) < anchor_length
                          or not source[source_start:].startswith(remainder)):
            return layout, result
        residual_insertion |= bool(remainder)
        proposed = mapping[target_start] + start_raw
        nearby = [position for position in range(max(start_raw, proposed - 3),
                                                 min(end_raw, proposed + 4))
                  if layout[position].casefold() == expected[len(edits)]]
        standalone = [position for position in nearby
                      if (position == 0 or not layout[position - 1].isalnum())
                      and (position + 1 == len(layout) or not layout[position + 1].isalnum())]
        if len(standalone) == 1:
            edits.append(standalone[0])
        elif len(nearby) == 1 and nearby[0] == proposed:
            edits.append(proposed)
        else:
            return layout, result
    if len(edits) != len(expected):
        return layout, result
    removed = set(edits)
    for position in edits:
        if (position + 1 < len(layout) and layout[position + 1] == ' '
                and (position == start_raw or layout[position - 1].isspace())):
            removed.add(position + 1)
    repaired = ''.join(char for index, char in enumerate(layout) if index not in removed)
    repaired_sequence, _ = _alnum_alignment(repaired[start_raw:end_raw - len(removed)])
    # Every source character must still occur in order; generic alignment
    # punctuation never authorizes a mechanics or numeric change.
    iterator = iter(repaired_sequence)
    if not all(any(char == item for item in iterator) for char in source):
        return layout, result
    if (_mechanic_tokens(layout) - _mechanic_tokens(repaired)
            or _mechanic_tokens(reference) - _mechanic_tokens(repaired)
            or rich_ocr_mechanics(layout) - rich_ocr_mechanics(repaired)
            or rich_ocr_mechanics(reference) - rich_ocr_mechanics(repaired)
            or Counter(x.casefold() for x in _NUMBER.findall(layout))
            - Counter(x.casefold() for x in _NUMBER.findall(repaired))
            or any(check['status'] == 'matched' for check in check_pairs(pairs, layout))
            and any(check['status'] != 'matched' for check in check_pairs(pairs, repaired))):
        result['status'] = 'source_loss'
        return layout, result
    result.update(status='repaired', removed_glyphs=len(edits),
                  body_blocks=[block['id'] for block in body], span_signature=span['signature'],
                  residual_source_duplicate=residual_insertion)
    return repaired, result


def _source_occurrences(text: str, source: str) -> list[tuple[int, int]]:
    """Locate an exact source run while allowing only layout whitespace changes."""
    parts = source.strip().split()
    if not parts:
        return []
    pattern = r'\s+'.join(re.escape(part) for part in parts)
    if parts[0][0].isalnum():
        pattern = r'(?<!\w)' + pattern
    if parts[-1][-1].isalnum():
        pattern += r'(?!\w)'
    return [match.span() for match in re.finditer(pattern, text)]


def _trace_binding(page: Any, span: dict, source: str) -> tuple[int, int] | None:
    """Require one painted glyph run with the same characters and origins."""
    raw_chars = span['chars']
    raw_text = ''.join(char['c'] for char in raw_chars)
    begin = raw_text.find(source)
    if begin < 0:
        return None
    glyphs = raw_chars[begin:begin + len(source)]
    matches: list[tuple[int, int]] = []
    for trace in page.get_texttrace():
        if (trace.get('type') != 0 or trace.get('opacity', 0) <= 0
                or trace.get('font') != span['font']
                or abs(trace.get('size', 0) - span['size']) > .5):
            continue
        trace_text = ''.join(chr(char[0]) for char in trace['chars'])
        for found in re.finditer(re.escape(source), trace_text):
            traced = trace['chars'][found.start():found.end()]
            if len(traced) != len(glyphs):
                continue
            if all(all(abs(raw['origin'][axis] - painted[2][axis]) <= .5
                       for axis in (0, 1))
                   and all(abs(raw['bbox'][edge] - painted[3][edge]) <= .5
                           for edge in (0, 2))
                   for raw, painted in zip(glyphs, traced, strict=True)):
                matches.append((trace['seqno'], found.start()))
    return matches[0] if len(matches) == 1 else None


def _source_fragment_sequences(raw_blocks: list[dict], source: str) -> set[tuple[tuple[int, ...], ...]] | None:
    """Find distinct raw character paths for a layout run, ignoring formatting only to veto edits.

    PDF extraction order can interleave unrelated blocks. Fragment paths therefore
    retain each block/line/span and character interval instead of flattening
    the page text. More than one path, or an excessive search, is ambiguous.
    """
    letters = ''.join(char.casefold() for char in source if char.isalnum())
    if not letters or len(letters) > 512:
        return None
    fragments: list[tuple[tuple[int, int, int], str, list[int], list[str]]] = []
    for block_index, block in enumerate(raw_blocks):
        for line_index, line in enumerate(block.get('lines', [])):
            for span_index, span in enumerate(line['spans']):
                chars = [char['c'] for char in span['chars']]
                if any(len(char) != 1 or len(char.casefold()) != 1 for char in chars):
                    return None
                normalized = ''.join(char.casefold() for char in chars if char.isalnum())
                offsets = [index for index, char in enumerate(chars) if char.isalnum()]
                if normalized:
                    fragments.append(((block_index, line_index, span_index), normalized, offsets, chars))
    by_initial: dict[str, list[int]] = {}
    for index, (_, normalized, _, _) in enumerate(fragments):
        by_initial.setdefault(normalized[0], []).append(index)
    paths: set[tuple[tuple[int, ...], ...]] = set()
    explored = 0

    def interval(identity: tuple[int, int, int], offsets: list[int], chars: list[str],
                 start: int, end: int) -> tuple[int, ...]:
        first, last = offsets[start], offsets[end - 1] + 1
        while first and not chars[first - 1].isalnum() and not chars[first - 1].isspace():
            first -= 1
        while last < len(chars) and not chars[last].isalnum() and not chars[last].isspace():
            last += 1
        return (*identity, first, last)

    def add_path(path: tuple[tuple[int, ...], ...]) -> None:
        paths.add(path)

    def continue_path(position: int, used: frozenset[int], path: tuple[tuple[int, ...], ...]) -> None:
        nonlocal explored
        if position == len(letters) or len(paths) > 1 or explored > 10000:
            if position == len(letters):
                add_path(path)
            return
        for index in by_initial.get(letters[position], []):
            if index in used:
                continue
            explored += 1
            identity, normalized, offsets, chars = fragments[index]
            remaining = letters[position:]
            if remaining.startswith(normalized):
                end = len(normalized)
            elif normalized.startswith(remaining):
                end = len(remaining)
            else:
                continue
            fragment = interval(identity, offsets, chars, 0, end)
            continue_path(position + end, used | {index}, (*path, fragment))
            if len(paths) > 1 or explored > 10000:
                return

    for index, (identity, normalized, offsets, chars) in enumerate(fragments):
        start = 0
        while start < len(normalized):
            found = normalized.find(letters[0], start)
            if found < 0:
                break
            explored += 1
            suffix = normalized[found:]
            if letters.startswith(suffix):
                fragment = interval(identity, offsets, chars, found, len(normalized))
                continue_path(len(suffix), frozenset({index}), (fragment,))
            elif suffix.startswith(letters):
                fragment = interval(identity, offsets, chars, found, found + len(letters))
                add_path((fragment,))
            if len(paths) > 1 or explored > 10000:
                break
            start = found + 1
        if len(paths) > 1 or explored > 10000:
            break
    return paths if explored <= 10000 else None


def repair_duplicate_source_layout(page: Any, layout: str, native: str, *,
                                   method: str, pairs: list[dict],
                                   decorative_status: str) -> tuple[str, dict]:
    """Remove one layout emission only when one PDF span has a unique paragraph flow."""
    result: dict = {'status': 'not_applicable'}
    if method != 'layout':
        return layout, result
    raw_blocks = page.get_text('rawdict')['blocks']
    plain_blocks = page.get_text('dict')['blocks']
    for block_index, block in enumerate(raw_blocks):
        lines = block.get('lines', [])
        for line_index in range(1, len(lines) - 1):
            previous, current, following = lines[line_index - 1:line_index + 2]
            if any(len(line['spans']) != 1 for line in (previous, current, following)):
                continue
            spans = [line['spans'][0] for line in (previous, current, following)]
            before, source, after = (''.join(char['c'] for char in span['chars']).strip()
                                     for span in spans)
            if not before or not source or not after:
                continue
            layout_ranges = _source_occurrences(layout, source)
            if len(layout_ranges) != 2:
                continue
            result = {'status': 'duplicate_source_emission_ambiguous',
                      'output_ranges': [list(pair) for pair in layout_ranges]}
            if decorative_status not in {'not_applicable', 'repaired'}:
                return layout, result
            if len(_source_occurrences(native, source)) != 1:
                return layout, result
            if sum(len(_source_occurrences(''.join(char['c'] for char in span['chars']), source))
                   for raw_block in raw_blocks for line in raw_block.get('lines', [])
                   for span in line['spans']) != 1:
                return layout, result
            raw_source = ''.join(char['c'] for char in spans[1]['chars'])
            source_start = raw_source.find(source)
            expected_fragments = ((block_index, line_index, 0, source_start,
                                   source_start + len(source)),)
            source_paths = _source_fragment_sequences(raw_blocks, source)
            if source_start < 0 or source_paths != {expected_fragments}:
                return layout, result
            trace_binding = _trace_binding(page, spans[1], source)
            if trace_binding is None:
                return layout, result
            # Raw and plain extraction must agree on this source geometry.
            plain_lines = plain_blocks[block_index].get('lines', [])
            if len(plain_lines) <= line_index + 1 or not plain_lines[line_index]['spans']:
                return layout, result
            if any(abs(spans[1]['bbox'][edge]
                       - plain_lines[line_index]['spans'][0]['bbox'][edge]) > .5
                   for edge in range(4)):
                return layout, result
            if (previous['dir'] != current['dir'] or current['dir'] != following['dir']
                    or spans[1]['font'] != spans[2]['font']
                    or abs(spans[1]['size'] - spans[2]['size']) > .5
                    or abs(current['bbox'][0] - following['bbox'][0]) > spans[1]['size'] * 1.5
                    or not previous['bbox'][1] < current['bbox'][1] < following['bbox'][1]
                    or following['bbox'][1] - current['bbox'][3] > spans[1]['size'] * 2):
                return layout, result
            before_ranges = _source_occurrences(layout, before)
            after_ranges = _source_occurrences(layout, after)
            if any(sum(len(_source_occurrences(''.join(char['c'] for char in span['chars']), item))
                       for raw_block in raw_blocks for line in raw_block.get('lines', [])
                       for span in line['spans']) != 1 for item in (before, after)):
                return layout, result
            if (len(before_ranges) != 1 or len(after_ranges) != 1
                    or not before_ranges[0][0] < layout_ranges[0][0]
                    or not layout_ranges[0][1] < after_ranges[0][0]
                    or max(layout_ranges[-1][1], after_ranges[0][1]) - before_ranges[0][1]
                    > 3 * (len(before) + 2 * len(source) + len(after))):
                return layout, result
            flows = [index for index, (_, end) in enumerate(layout_ranges)
                     if end < after_ranges[0][0]
                     and layout[end:after_ranges[0][0]].isspace()
                     and '\n\n' not in layout[end:after_ranges[0][0]]]
            if len(flows) != 1:
                return layout, result
            kept = flows[0]
            removed = layout_ranges[1 - kept]
            left, right = layout[:removed[0]], layout[removed[1]:]
            # Trim only the separator attached to the orphan emission.
            if left.endswith(' ') and right.startswith(' '):
                left = left[:-1]
                right = right[1:]
            proposed = left + right
            final_source = _source_occurrences(proposed, source)
            final_before = _source_occurrences(proposed, before)
            final_after = _source_occurrences(proposed, after)
            if (len(final_source) != 1 or len(final_before) != 1 or len(final_after) != 1
                    or not final_before[0][1] < final_source[0][0] < final_after[0][0]
                    or not proposed[final_source[0][1]:final_after[0][0]].isspace()
                    or '\n\n' in proposed[final_source[0][1]:final_after[0][0]]
                    or _mechanic_tokens(native) - _mechanic_tokens(proposed)
                    or rich_ocr_mechanics(native) - rich_ocr_mechanics(proposed)
                    or Counter(_NUMBER.findall(native)) - Counter(_NUMBER.findall(proposed))
                    or any(check['status'] == 'matched' for check in check_pairs(pairs, layout))
                    and any(check['status'] != 'matched' for check in check_pairs(pairs, proposed))):
                result['status'] = 'duplicate_source_emission_preservation_failed'
                return layout, result
            box = tuple(round(value * 4) / 4 for value in spans[1]['bbox'])
            identity = (f'{page.number}:{expected_fragments}:{source}:'
                        f'{box}:{current["dir"]}:{spans[1]["font"]}:'
                        f'{spans[1]["size"]}:{trace_binding}')
            result.update(status='duplicate_source_emission_repaired',
                          source_run=hashlib.sha256(identity.encode()).hexdigest()[:20],
                          source_fragment_count=len(expected_fragments),
                          kept_range=list(layout_ranges[kept]), removed_range=list(removed),
                          removed_chars=len(layout) - len(proposed), source_unique=True,
                          trace_unique=True, flow_unique=True, preservation='passed')
            return proposed, result
    return layout, result


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
