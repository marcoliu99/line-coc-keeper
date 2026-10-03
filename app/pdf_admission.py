"""Compose gameplay authority before any derived source consumers run.

Classification is discovery, not proof. Unresolved image candidates are excluded;
immutable native core text and independently verified transcription are authority.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Literal, TypedDict

from app import pdf_quality
from app.pdf_page_criticality import reference_compatible

VERSION = 2
SourceAuthority = Literal['VERIFIED', 'QUARANTINED', 'UNRESOLVED_CORE']
SourceRole = Literal['CORE_PLAYABLE_SOURCE', 'NON_AUTHORITATIVE_REVIEW']


class SourceSelection(TypedDict):
    source_authority: SourceAuthority
    source_role: SourceRole

_NONCORE_HEADING = re.compile(
    r'^(?:credits|copyright|acknowledg(?:e)?ments|legal notices|character sheet|'
    r'pre[- ]generated investigators|ready[- ]made investigators|reference appendix)\b', re.IGNORECASE)
_CORE = re.compile(r'\b(?:keeper|scenario|investigators?|clue|sanity|damage|monster|NPC|'
                   r'scene|event|opening|setup|rule|adventure)\b', re.IGNORECASE)
_REQUIRED = re.compile(r'\b(?:must|required|necessary|essential)\b', re.IGNORECASE)


def _noncore(row: dict) -> bool:
    if row.get('page_criticality', {}).get('source_critical') is False:
        return True
    native = row.get('candidates', {}).get('native', '')
    return any(_NONCORE_HEADING.match(line.strip()) for line in native.splitlines()[:6])


def _has_body(text: str, row: dict, *, unread_body: bool) -> bool:
    """Titles and authority placeholders cannot stand in for unread instructions.

    Use existing block roles and prose structure, not a minimum document size:
    a complete one-sentence instruction is a legitimate short playable source.
    Unpunctuated wrapped body text also remains eligible.
    """
    if not text.strip() or text.lstrip().startswith('[PDF_'):
        return False
    blocks = row.get('layout_decision', {}).get('blocks', [])
    # A native paragraph can start inside the top margin and extend into the
    # body; the layout role alone does not make that paragraph decorative.
    body = [block.get('text', '') for block in blocks if block.get('role') != 'heading']
    if blocks and not body:
        return False
    if not unread_body or row.get('image_transcription', {}).get('status') == 'authoritative':
        return True
    pairs = row.get('numeric_pairs', [])
    if pairs and all(check['status'] == 'matched' for check in pdf_quality.check_pairs(pairs, text)):
        return True
    candidates = body if blocks else [text]
    return any(re.search(r'\S.{1,}[.!?。！？](?:\s|$)', candidate)
               or (blocks and len([line for line in candidate.splitlines() if line.strip()]) > 1)
               for candidate in candidates)


def _required_missing(row: dict, native_pages: dict[int, str]) -> bool:
    """A candidate label cannot substitute for a literal canonical requirement.

    A receipt must identify a missing image region and bind the requirement to
    safe canonical text on another page. Public booleans/confidence are ignored.
    """
    proof = row.get('required_source_evidence')
    if not isinstance(proof, dict):
        return False
    page = proof.get('requirement_page')
    quote = proof.get('requirement_quote')
    fragment = proof.get('missing_fragment')
    region = proof.get('region_bbox')
    if (not isinstance(page, int) or page == row['page'] or not isinstance(quote, str)
            or not quote.strip() or not _REQUIRED.search(quote) or not isinstance(fragment, str)
            or not fragment.strip() or not isinstance(region, list) or len(region) != 4
            or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in region)
            or region[0] >= region[2] or region[1] >= region[3]):
        return False
    if quote not in native_pages.get(page, ''):
        return False
    if proof.get('requirement_sha256') != hashlib.sha256(quote.encode()).hexdigest():
        return False
    # Exact complete counterparts, never matching just dice/numbers, remove uniqueness.
    return not any(reference_compatible(fragment, text) for text in native_pages.values())


def compose(texts: list[str], rows: list[dict]) -> list[str]:
    """Return source with uncertain pieces removed; retain private evidence.

    Unsafe gameplay prevented by retained core gates: wrong rules, reordered
    instructions, or a concretely required image instruction missing from source.
    """
    safe_native = {row['page']: row.get('candidates', {}).get('native', '') for row in rows
                   if not row.get('requires_image_transcription')
                   and row.get('layout_decision', {}).get('status') != 'needs_review'
                   and 'source_mechanics_unresolved' not in row.get('source_blocking_reasons', [])}
    result = list(texts)
    for index, row in enumerate(rows):
        reasons = list(row.get('source_blocking_reasons', []))
        if row.get('source_authority') == 'QUARANTINED' and not reasons:
            result[index] = ''
            continue
        if not reasons:
            row['source_authority'] = 'VERIFIED'
            continue
        native = row.get('candidates', {}).get('native', '')
        noncore = _noncore(row)
        core_requirement = bool(re.search(r'\b(?:Keeper|scenario)\b.{0,80}\b(?:must|required|essential)\b', native, re.IGNORECASE))
        core = core_requirement or (not noncore and bool(_CORE.search(native) or (
            not row.get('requires_image_transcription') and len(native.split()) >= 40)))
        retained = []
        # A source-bound requirement outranks a coarse heading/classification.
        if _required_missing(row, safe_native):
            retained.append('source_image_transcription_unverified')
        if not noncore or core_requirement:
            if 'source_mechanics_unresolved' in reasons:
                retained.append('source_mechanics_unresolved')
            if core and 'source_ordering_unverified' in reasons:
                retained.append('source_ordering_unverified')
            if 'canonical_playable_source_missing' in reasons:
                retained.append('canonical_playable_source_missing')
        if retained:
            selection: SourceSelection = {'source_role': 'CORE_PLAYABLE_SOURCE',
                                          'source_authority': 'UNRESOLVED_CORE'}
            row.update(selection)
            row['source_blocking_reasons'] = retained
            continue
        row['quarantine_evidence'] = {'selected_text': texts[index],
                                      'selected_sha256': row.get('selected_sha256'),
                                      'review_reasons': reasons}
        selection = {'source_role': 'NON_AUTHORITATIVE_REVIEW', 'source_authority': 'QUARANTINED'}
        row.update(selection)
        row['source_blocking_reasons'] = []
        row['disposition'] = 'soft_review'
        row['publication_severity'] = 'SOFT_REVIEW'
        row.setdefault('derived_feature_warnings', []).append('SOURCE_REVIEW_QUARANTINED')
        # No candidate text or placeholder mechanics may reach RAG/index/topology.
        result[index] = ''
        row['selected_text'] = ''
        row['selected_sha256'] = hashlib.sha256(b'').hexdigest()
        row['extracted_chars'] = 0
    unread_body = any(row.get('source_authority') == 'QUARANTINED'
                      and row.get('requires_image_transcription') for row in rows)
    has_core = any(_has_body(text, row, unread_body=unread_body) and not _noncore(row)
                   and row.get('source_authority') == 'VERIFIED'
                   for text, row in zip(result, rows, strict=True))
    existing_core_defect = any(row.get('source_authority') == 'UNRESOLVED_CORE' for row in rows)
    if not has_core and not existing_core_defect:
        for row in rows:
            if row.get('quarantine_evidence'):
                row['source_blocking_reasons'] = row['quarantine_evidence']['review_reasons']
            row['disposition'] = 'needs_review'
            row['publication_severity'] = 'HARD_BLOCK'
            if 'canonical_playable_source_missing' not in row['source_blocking_reasons']:
                row['source_blocking_reasons'].append('canonical_playable_source_missing')
    return result
