"""Private image transcription evidence; only independently verified text is source."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypedDict

from app import pdf_ocr

ImageStatus = Literal['unverified', 'authoritative']
TranscriptionEngine = Literal['paddleocr', 'tesseract', 'ai_vision', 'markitdown_ocr']
ImageReason = Literal['no_independent_evidence', 'independent_transcription_agreement',
                      'independent_evidence_conflict']


class ImageTranscription(TypedDict):
    engine: TranscriptionEngine
    status: ImageStatus
    candidate: str
    reason: ImageReason
    evidence: list[dict]


def retain_local(attempts: list[pdf_ocr.OcrAttempt]) -> ImageTranscription | None:
    """Prefer Paddle, retaining fallback attempts elsewhere for diagnostics."""
    for attempt in attempts:
        if attempt['status'] in {'candidate', 'rejected', 'accepted'} and attempt['candidate'].strip():
            return {'engine': attempt['engine'], 'status': 'unverified',
                    'candidate': attempt['candidate'], 'reason': 'no_independent_evidence',
                    'evidence': []}
    return None


PageKind = Literal['text', 'character_sheet', 'table', 'map', 'illustration', 'unreadable']


class ImageEvidence(TypedDict):
    origin: Literal['ai_vision', 'markitdown_ocr']
    source_id: str
    candidate: str
    page_type: PageKind


_TRANSCRIPTION_TOOL = {
    'name': 'transcribe_pdf_page',
    'description': 'Classify a PDF page and transcribe its visible text faithfully, never summarize or infer.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'page_type': {'type': 'string', 'enum': ['text', 'character_sheet', 'table', 'map',
                                                  'illustration', 'unreadable']},
            'text': {'type': 'string', 'description':
                     'Verbatim original-language text, left column then right; preserve labels, values, dice, '
                     'percentages and blank fields. No invented values, translation or interpretation. '
                     'Use [unreadable] for uncertain text. Only a wholly text-free illustration has empty text.'},
        },
        'required': ['page_type', 'text'],
    },
}


def reserve_verification(budget: dict, page_number: int,
                         checkpoint: Callable[[dict], None] | None = None) -> bool:
    """Persist dispatch consumption before a verification call, including failed calls."""
    if budget['remaining_requests'] <= 0:
        return False
    visited = budget['visited_pages']
    if page_number not in visited:
        if budget['remaining_pages'] <= 0:
            return False
        visited.append(page_number)
        budget['remaining_pages'] -= 1
    budget['remaining_requests'] -= 1
    budget['consumed_requests'] += 1
    if checkpoint is not None:
        checkpoint(budget)
    return True


def analyze(png: bytes) -> ImageEvidence | None:
    """Use an independent image request containing no local OCR candidate."""
    import hashlib

    from app import config
    from app.providers.registry import analysis_provider

    provider = analysis_provider()
    if provider is None:
        return None
    result = provider.analyze_image(png, _TRANSCRIPTION_TOOL, '請忠實轉錄完整圖片，不推測缺漏。',
                                    timeout=config.PDF_LAYOUT_IMAGE_TIMEOUT_SECONDS, max_retries=0)
    if (not isinstance(result, dict) or result.get('page_type') not in
            {'text', 'character_sheet', 'table', 'map', 'illustration', 'unreadable'}
            or not isinstance(result.get('text'), str)):
        return None
    return {'origin': 'ai_vision', 'source_id': config.ANALYSIS_PROVIDER + ':' + hashlib.sha256(png).hexdigest(),
            'candidate': result['text'].strip(), 'page_type': result['page_type']}


def verify(row: dict, evidence: ImageEvidence) -> str:
    """Retain all independent evidence; select only a compatible local candidate."""
    from app import pdf_quality

    record = row.get('image_transcription')
    if record is None and not evidence['candidate']:
        return ''
    if record is None:
        record = {'engine': evidence['origin'], 'status': 'unverified',
                  'candidate': evidence['candidate'], 'reason': 'no_independent_evidence', 'evidence': []}
        row['image_transcription'] = record
    record['evidence'].append(dict(evidence))
    if not evidence['candidate'] or evidence['page_type'] in {'map', 'illustration', 'unreadable'}:
        return ''
    candidates = row.get('page_ocr_attempts', [])
    for attempt in candidates:
        candidate = attempt['candidate']
        if attempt['status'] not in {'candidate', 'rejected', 'accepted'} or not candidate.strip():
            continue
        if not all(pdf_quality.accept_independent_transcription(candidate, item['candidate'])
                   for item in record['evidence']):
            continue
        native = row['candidates']['native']
        # A short native caption/heading remains an anchor, not the full image authority.
        if not pdf_quality.preserves_image_native_anchor(native, candidate, row['numeric_pairs']):
            continue
        record.update(engine=attempt['engine'], status='authoritative', candidate=candidate,
                      reason='independent_transcription_agreement')
        return candidate
    if any(a['candidate'].strip() for a in candidates):
        record['reason'] = 'independent_evidence_conflict'
    return ''


def metrics(pages: list[dict]) -> dict[str, int]:
    """Counts refer to engine attempts, accepted regions and final page authority."""
    result: dict[str, int] = {}
    for engine, prefix in [('paddleocr', 'paddle'), ('tesseract', 'tesseract')]:
        region_attempts = [a for row in pages for repair in row['local_repairs']
                           for a in repair.get('ocr_attempts', []) if a['engine'] == engine]
        attempts = region_attempts + [a for row in pages for a in row.get('page_ocr_attempts', [])
                                      if a['engine'] == engine]
        records = [row['image_transcription'] for row in pages
                   if row.get('image_transcription', {}).get('engine') == engine]
        result[prefix + ('_region_attempts' if prefix == 'paddle' else '_attempts')] = (
            len(region_attempts) if prefix == 'paddle' else len(attempts))
        result[prefix + '_text_repairs_accepted'] = sum(a['status'] == 'accepted' for a in region_attempts)
        result[prefix + '_page_transcriptions_authoritative'] = sum(
            r['status'] == 'authoritative' for r in records)
        if prefix == 'paddle':
            result['paddle_page_attempts'] = len(attempts) - len(region_attempts)
            result['paddle_page_transcriptions_unverified'] = sum(r['status'] == 'unverified' for r in records)
            result['paddle_rejected'] = sum(a['status'] == 'rejected' for a in attempts)
            result['paddle_failed'] = sum(a['status'] in {'unavailable', 'error', 'empty'} for a in attempts)
    records = [row['image_transcription'] for row in pages if row.get('image_transcription')]
    for origin, prefix in [('markitdown_ocr', 'markitdown'), ('ai_vision', 'ai')]:
        result[prefix + '_transcription_agreements'] = sum(
            r['status'] == 'authoritative' and any(e['origin'] == origin for e in r['evidence']) for r in records)
        result[prefix + '_transcription_conflicts'] = sum(
            r['reason'] == 'independent_evidence_conflict' and any(e['origin'] == origin for e in r['evidence'])
            for r in records)
    result['provider_transcription_agreements'] = result['ai_transcription_agreements']
    result['provider_transcription_conflicts'] = result['ai_transcription_conflicts']
    image_pages = [row for row in pages if row.get('source_kind') == 'native_text_absent'
                   and row.get('graphic_evidence') and not row.get('verified_illustration')]
    result['image_only_pages'] = len(image_pages)
    result['image_only_authoritative'] = sum(
        r.get('image_transcription', {}).get('status') == 'authoritative' for r in image_pages)
    result['image_only_unverified'] = len(image_pages) - result['image_only_authoritative']
    result['manual_approvals'] = 0  # This route creates no implicit operator approval.
    return result
