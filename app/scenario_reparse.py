"""Deterministic source selection; recency and model preference grant no authority."""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from app.pdf_admission import SourceAuthority

Decision = Literal['unchanged_verified', 'upgraded', 'downgraded_candidate', 'conflict', 'still_unresolved']
_PAGE = re.compile(r'^--- 第 (\d+) 頁 ---$', re.MULTILINE)


def pages(text: str) -> dict[int, str]:
    matches = list(_PAGE.finditer(text))
    if not matches:
        return {1: text}
    return {int(match[1]): text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
            for i, match in enumerate(matches)}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _rows(report: dict) -> dict[int, dict]:
    rows = report.get('pages', [])
    if isinstance(rows, dict):
        rows = list(rows.values())
    return {row['page']: row for row in rows if isinstance(row, dict) and isinstance(row.get('page'), int)}


def _verified(text: str, row: dict, *, published: bool = False) -> bool:
    if not text.strip() or row.get('source_authority') in {'QUARANTINED', 'UNRESOLVED_CORE'}:
        return False
    if row.get('source_blocking_reasons') or row.get('publication_severity') == 'HARD_BLOCK':
        return False
    # Legacy publication is authority only on the published side.
    if not published and (row.get('source_authority') != 'VERIFIED' or not row.get('selected_sha256')
                          or row.get('verification_basis') == 'legacy_published'):
        return False
    # A supplied digest must bind exact content.
    bound = row.get('selected_text', text)
    return (isinstance(bound, str) and bound.strip() == text.strip()
            and (not row.get('selected_sha256') or row['selected_sha256'] == _hash(bound)))


def _region_upgrade(old: str, old_row: dict, new: str, new_row: dict) -> tuple[str, int] | None:
    """Only replace previously identified unknown regions, preserving every sibling.

    Complete ordered region receipts must reproduce each page's canonical text;
    unchecked region labels and partial text snippets cannot authorize a merge.
    """
    old_regions, new_regions = old_row.get('source_regions'), new_row.get('source_regions')
    order = old_row.get('ordered_region_ids')
    if (not isinstance(old_regions, list) or not isinstance(new_regions, list)
            or not isinstance(order, list) or order != new_row.get('ordered_region_ids')
            or any(not isinstance(identifier, str) for identifier in order)
            or len(set(order)) != len(order)):
        return None
    def bound(regions: list) -> dict | None:
        result = {}
        for region in regions:
            if not isinstance(region, dict) or region.get('id') not in order:
                return None
            text = region.get('text')
            if (region['id'] in result or not isinstance(text, str)
                    or region.get('sha256') != _hash(text)
                    or region.get('authority') not in {'VERIFIED', 'UNKNOWN'}):
                return None
            result[region['id']] = region
        return result if set(result) == set(order) else None
    before, after = bound(old_regions), bound(new_regions)
    if before is None or after is None:
        return None
    def render(regions: dict) -> str:
        return '\n\n'.join(regions[key]['text'] for key in order
                           if regions[key]['authority'] == 'VERIFIED' and regions[key]['text'])
    if render(before).strip() != old.strip() or render(after).strip() != new.strip():
        return None
    count = 0
    for key in order:
        if before[key]['authority'] == 'VERIFIED':
            if after[key]['authority'] != 'VERIFIED' or before[key]['text'] != after[key]['text']:
                return None
        elif after[key]['authority'] == 'VERIFIED':
            count += 1
    return (render(after), count) if count else None


@dataclass
class Merge:
    text: str
    report: dict
    selected_new_pages: set[int]


def merge(old_text: str, old_report: dict, new_text: str, new_report: dict) -> Merge:
    old_pages, new_pages = pages(old_text), pages(new_text)
    old_rows, new_rows = _rows(old_report), _rows(new_report)
    selected, rows, changes, selected_new = {}, [], [], set()
    upgraded_regions = 0
    for page in sorted(old_pages.keys() | new_pages.keys()):
        old, new = old_pages.get(page, ''), new_pages.get(page, '')
        old_row, new_row = old_rows.get(page, {}), new_rows.get(page, {})
        old_verified, new_verified = _verified(old, old_row, published=True), _verified(new, new_row)
        legacy_published = old_verified and (old_row.get('verification_basis') == 'legacy_published'
                                            or old_row.get('source_authority') != 'VERIFIED'
                                            or not old_row.get('selected_sha256'))
        decision: Decision
        regional = (_region_upgrade(old, old_row, new, new_row)
                    if old_verified and new_verified and old_report.get('pdf_sha256')
                    and old_report.get('pdf_sha256') == new_report.get('pdf_sha256') else None)
        if regional is not None:
            decision = 'upgraded'
            selected[page] = regional[0]
            upgraded_regions += regional[1]
            selected_new.add(page)
            row = copy.deepcopy(new_row)
        elif old_verified:
            if not new_verified:
                decision = 'downgraded_candidate'
            elif ' '.join(old.split()) == ' '.join(new.split()):
                decision = 'unchanged_verified'
            else:
                decision = 'conflict'
            selected[page] = old
            row = copy.deepcopy(old_row)
        elif new_verified:
            decision = 'upgraded'
            selected[page] = new
            selected_new.add(page)
            row = copy.deepcopy(new_row)
        else:
            decision = 'still_unresolved'
            selected[page] = ''
            row = copy.deepcopy(old_row or new_row)
        authority: SourceAuthority = 'VERIFIED' if selected[page] else 'QUARANTINED'
        bound_text = row.get('selected_text', selected[page]) if selected[page] else ''
        row.update(page=page, selected_text=bound_text, selected_sha256=_hash(bound_text),
                   source_authority=authority,
                   source_blocking_reasons=[], disposition='accepted' if selected[page] else 'soft_review',
                   publication_severity=row.get('publication_severity', 'NONE') if selected[page] else 'SOFT_REVIEW')
        rows.append(row)
        # Stable publication is not proof of a PDF verification that never happened.
        if legacy_published and page not in selected_new:
            row['verification_basis'] = 'legacy_published'
        changes.append({'page': page, 'decision': decision,
                            'old_authority': 'VERIFIED' if old_verified else 'UNKNOWN',
                            'new_authority': 'VERIFIED' if new_verified else 'UNKNOWN',
                            'selected_authority': row['source_authority'], 'old_sha256': _hash(old),
                            'old_evidence': 'legacy_published' if legacy_published else 'source_verified' if old_verified else 'unknown',
                            'selected_evidence': row.get('verification_basis', 'source_verified' if selected[page] else 'unknown'),
                            'new_sha256': _hash(new), 'selected_sha256': _hash(selected[page])})
    # Keep exact legacy serialization when no source upgrade occurred, including
    # whitespace on which existing source certificates may depend.
    text = ('\n\n'.join(f"--- 第 {row['page']} 頁 ---\n{row['selected_text']}" for row in rows).strip()
            if selected_new else old_text)
    report = copy.deepcopy(old_report)
    # Preserve each consumed operation's ledger even when no candidate wins.
    # History is private, and snapshots exclude history to avoid recursive growth.
    candidate = copy.deepcopy(new_report)
    candidate.pop('reparse_attempt_history', None)
    report.setdefault('reparse_attempt_history', []).append(candidate)
    report.update(pages=rows, blocked_pages=[], hard_block_pages=[],
                  quarantined_pages=[row['page'] for row in rows if row['source_authority'] == 'QUARANTINED'])
    report['soft_review_pages'] = [row['page'] for row in rows if row['publication_severity'] == 'SOFT_REVIEW']
    report['scenario_readiness'] = 'READY_WITH_WARNINGS' if report['soft_review_pages'] else old_report.get('scenario_readiness', 'READY')
    report['reparse_diff'] = {'previous_source_sha256': _hash(old_text), 'candidate_source_sha256': _hash(new_text),
        'selected_source_sha256': _hash(text), 'changes': changes,
        'retained_verified': sum(change['old_authority'] == 'VERIFIED' for change in changes),
        'retained_legacy_published': sum(change['old_evidence'] == 'legacy_published' for change in changes),
        'upgraded': sum(change['decision'] == 'upgraded' for change in changes),
        'conflicts': sum(change['decision'] == 'conflict' for change in changes), 'downgrades_applied': 0,
        'upgraded_regions': upgraded_regions}
    return Merge(text, report, selected_new)


def validated_cards(cards: list, canonical_source: str) -> list[dict]:
    """Add cards only when all supplied mechanics bind to one approved source page."""
    fields = {'str_': 'STR', 'con': 'CON', 'siz': 'SIZ', 'dex': 'DEX', 'app': 'APP',
              'int_': 'INT', 'pow_': 'POW', 'edu': 'EDU', 'hp_max': 'HP', 'mp_max': 'MP',
              'san_max': 'SAN', 'luck': 'LUCK'}
    approved = []
    supported = fields.keys() | {'name', 'skills', 'skill_translations', 'source_page', 'source_sha256'}
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get('name'), str) or not card['name'].strip():
            continue
        if any(key not in supported and value for key, value in card.items()):
            continue
        supplied = {key: card[key] for key in fields if key in card}
        if len(supplied.keys() & {'str_', 'con', 'siz', 'dex', 'app', 'int_', 'pow_', 'edu'}) < 4:
            continue
        for page, text in pages(canonical_source).items():
            if not re.search(rf'(?<!\w){re.escape(card["name"])}(?!\w)', text):
                continue
            # A page hash is not a character-scoped receipt. Multiple stat sets
            # may describe different investigators: defer this ambiguous artifact.
            if any(len(re.findall(rf'\b{fields[key]}\s*[:：]?\s*\d+', text, re.IGNORECASE)) != 1
                   for key in supplied):
                continue
            stat_positions = [re.search(rf'\b{fields[key]}\s*[:：]?\s*\d+', text, re.IGNORECASE)
                              for key in supplied]
            first_stat = min(match.start() for match in stat_positions if match is not None)
            if not re.search(rf'(?<!\w){re.escape(card["name"])}\s*[:：]?\s*$', text[:first_stat]):
                # An incidental mention elsewhere is not the stat block header.
                continue
            labels = '|'.join(fields.values())
            block = re.match(rf'(?:\b(?:{labels})\s*[:：]?\s*\d+\s*[,;]?\s*)+',
                             text[first_stat:], re.IGNORECASE)
            if block is None or any(not re.search(
                    rf'\b{fields[key]}\s*[:：]?\s*{value}(?!\d)', block[0], re.IGNORECASE)
                    for key, value in supplied.items()):
                continue
            if any(isinstance(value, str) and value.strip() and value not in block[0]
                   for key, value in card.items()
                   if key not in {'name', 'source_sha256'} and key not in fields):
                continue
            if any(not isinstance(value, int) or isinstance(value, bool) or not re.search(
                    rf'\b{fields[key]}\s*[:：]?\s*{value}(?!\d)', text, re.IGNORECASE)
                    for key, value in supplied.items()):
                continue
            translations = card.get('skill_translations') or {}
            skills = card.get('skills') or {}
            if not isinstance(skills, dict) or not isinstance(translations, dict):
                continue
            # Skill/free-form additions need a complete character receipt, which
            # the legacy extraction contract does not supply. Keep existing cards.
            if skills:
                continue
            if any(not isinstance(value, int) or not isinstance(translations.get(key, key), str)
                   or not re.search(rf'{re.escape(str(translations.get(key, key)))}\s*[:：]?\s*{value}(?!\d)', text, re.IGNORECASE)
                   for key, value in skills.items()):
                continue
            accepted = copy.deepcopy({key: value for key, value in card.items() if key in supported})
            accepted.update(source_page=page, source_sha256=_hash(text))
            approved.append(accepted)
            break
    return approved
