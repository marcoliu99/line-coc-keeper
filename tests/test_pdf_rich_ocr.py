"""Synthetic source-preservation regressions for low-text OCR rescue."""
from __future__ import annotations

from unittest.mock import patch

import pymupdf
import pytest

from app import pdf_loader, pdf_quality

FILLER = 'Additional background and form details. ' * 9


def _evidence(*, vertical: bool = False, folio: bool = False) -> dict:
    words = []
    if vertical:
        words.extend({'text': letter, 'bbox': [20, 40 + i * 20, 25, 50 + i * 20],
                      'block': 0, 'line': i, 'word': 0}
                     for i, letter in enumerate('Name'))
    if folio:
        words.append({'text': '31', 'bbox': [20, 740, 30, 750],
                      'block': 1, 'line': 0, 'word': 0})
    return {'height': 774, 'words': words, 'blocks': []}


def _select(baseline: str, candidate: str, *, evidence: dict | None = None) -> dict:
    return pdf_quality.select_rich_ocr_candidate(baseline, candidate, [], evidence or _evidence(), 200)


def test_structurally_proven_fragments_do_not_veto_rich_candidate():
    result = _select('31\n<!-- Start of picture text -->\nN a m e',
                     'Name: Alice\nSTR 60\nLUCK 50\n' + FILLER,
                     evidence=_evidence(vertical=True, folio=True))
    assert result['status'] == 'accepted'
    assert result['baseline_strength'] == 'weak'
    assert set(result['excluded_fragments']) == {'folio', 'picture_markup', 'vertical_spacing'}
    assert result['candidate_extra_content_verified'] is False


def test_unproven_heading_is_required_even_on_short_page():
    assert _select('31\nUnknown Heading', 'STR 60\n' + FILLER,
                   evidence=_evidence(folio=True))['status'] == 'source_content_loss'


def test_short_source_phrase_stays_required_when_folio_is_excluded():
    assert _select('31\nUnusual Keeper instruction', 'STR 60\n' + FILLER,
                   evidence=_evidence(folio=True))['status'] == 'source_content_loss'


def test_vertical_name_is_not_discarded_with_its_spacing():
    assert _select('N a m e', 'STR 60\n' + FILLER,
                   evidence=_evidence(vertical=True))['status'] == 'source_content_loss'


def test_ambiguous_folio_number_is_not_discarded():
    evidence = _evidence(folio=True)
    evidence['words'].append({'text': '31', 'bbox': [50, 300, 60, 310]})
    assert _select('31', 'STR 60\n' + FILLER, evidence=evidence)['status'] == 'strong_baseline'


@pytest.mark.parametrize(('baseline', 'candidate', 'status'), [
    ('The creature attacks twice.', 'STR 60\n' + FILLER, 'strong_baseline'),
    ('STR 60', 'STR 50\nLUCK 50\n' + FILLER, 'mechanic_loss'),
    ('STR 60', 'STR 60\nSTR 50\nLUCK 50\n' + FILLER, 'pair_mismatch'),
    ('STR 60', 'STR 60\nLUCK 50\n' + FILLER, 'accepted'),
    ('Damage 1d6+2', 'Damage 1d6\n' + FILLER, 'mechanic_loss'),
    ('SAN 1/1d6', 'SAN 1d6/1\n' + FILLER, 'mechanic_loss'),
    ('Unusual Keeper instruction', 'STR 60\n' + FILLER, 'strong_baseline'),
    ('STR 60', 'Unrelated descriptive prose. ' * 12, 'mechanic_loss'),
    ('STR 60', 'STR 60\nSAN 1/\n' + FILLER, 'mechanic_loss'),
    ('Normal narrative source. ' * 15, 'STR 60\n' + FILLER, 'not_low_text_source'),
    ('STR 60', 'short', 'candidate_too_short'),
])
def test_source_preservation(baseline: str, candidate: str, status: str):
    assert _select(baseline, candidate)['status'] == status


def test_pair_binding_cannot_be_overridden_by_rich_candidate():
    pair = {'label': 'STR', 'value': '60', 'status': 'same_row_candidate', 'block': 0}
    result = pdf_quality.select_rich_ocr_candidate(
        'STR 60', 'STR 50\n' + FILLER, [pair], _evidence(), 200,
    )
    assert result['status'] == 'pair_mismatch'


def test_long_narrative_keeps_strict_ordinary_comparison():
    baseline = 'The keeper saw the strange light and went to the beach. ' * 6
    candidate = 'A different long account. ' * 15
    assert pdf_quality.select_text(baseline, candidate)[1] == 'native'
    assert _select(baseline, candidate)['status'] == 'not_low_text_source'


def test_loader_promotes_without_new_paddle_vote_and_keeps_review_history():
    with pymupdf.open() as doc:
        doc.new_page()
        payload = doc.tobytes()
    candidate = 'STR 60\nLUCK 50\n' + FILLER
    report = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(pdf_loader.pdf_quality, 'native_text', return_value=('31\nSTR 60', [])), \
         patch.object(pdf_loader.pdf_quality, 'block_evidence', return_value=_evidence(folio=True)), \
         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle',
                      return_value=pdf_loader.pdf_layout.LayoutResult(reason='incomplete_mapping')), \
         patch.object(pdf_loader, '_page_has_graphic_content', return_value=True), \
         patch.object(pdf_loader, '_render_page_png', return_value=b'png'), \
         patch.object(pdf_loader, '_markitdown_page_texts', return_value={1: candidate}), \
         patch.object(pdf_loader.pdf_ai_repair, 'repair_page',
                      side_effect=lambda _page, _row, text, _budget: (text, {'regions': [], 'unresolved_labels': []})), \
         patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle') as paddle:
        text, reviews, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
    assert candidate.strip() in text
    row = report['pages'][0]
    assert row['method'] == 'markitdown'
    assert row['rich_candidate_selection']['status'] == 'accepted'
    assert row['rich_candidate_selection']['candidate_extra_content_verified'] is False
    assert 'low_text' in row['warnings']
    assert 'rich_candidate_unverified' in row['warnings']
    assert reviews == [1]
    paddle.assert_not_called()
