"""Synthetic source-preservation regressions for low-text OCR rescue."""
from __future__ import annotations

from unittest.mock import patch

import pymupdf
import pytest

from app import pdf_loader, pdf_quality

FILLER = 'Additional background and form details. ' * 9


def _evidence(*, vertical: bool = False, folio: bool = False) -> dict:
    words = []
    blocks = []
    if vertical:
        words.extend({'text': letter, 'bbox': [20, 40 + i * 12, 25, 50 + i * 12],
                      'block': 0, 'line': i, 'word': 0, 'font': 'Body', 'font_size': 12}
                     for i, letter in enumerate('Name'))
        blocks.append({'id': 0, 'bbox': [20, 40, 25, 86],
                       'lines': [{'text': letter} for letter in 'Name']})
    if folio:
        words.append({'text': '31', 'bbox': [20, 740, 30, 750],
                      'block': 1, 'line': 0, 'word': 0})
    return {'height': 774, 'words': words, 'blocks': blocks}


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


def test_coherent_vertical_chapter_is_preserved_as_semantic_source():
    letters = 'CHAPTER'
    evidence = {'words': [{'text': letter, 'bbox': [20, 40 + i * 12, 25, 50 + i * 12],
                           'block': 0, 'line': i, 'font': 'Body', 'font_size': 12}
                          for i, letter in enumerate(letters)],
                'blocks': [{'id': 0, 'bbox': [20, 40, 25, 122],
                            'lines': [{'text': letter} for letter in letters]}]}
    assert pdf_quality._vertical_fragment('C H A P T E R', evidence)
    assert _select('C H A P T E R', 'STR 60\n' + FILLER,
                   evidence=evidence)['status'] == 'source_content_loss'


def test_vertical_letters_across_distant_blocks_are_not_reconstructed():
    evidence = _evidence(vertical=True)
    for index, word in enumerate(evidence['words']):
        word['block'] = index
        word['bbox'] = [20, 40 + index * 180, 25, 50 + index * 180]
    assert not pdf_quality._vertical_fragment('N a m e', evidence)


def test_vertical_letters_in_separate_nearby_blocks_are_not_reconstructed():
    evidence = _evidence(vertical=True)
    for index, word in enumerate(evidence['words']):
        word['block'] = index
    assert not pdf_quality._vertical_fragment('N a m e', evidence)


def test_vertical_letters_cannot_cross_columns_or_regions():
    evidence = _evidence(vertical=True)
    evidence['words'][2]['bbox'] = [300, 64, 305, 74]
    assert not pdf_quality._vertical_fragment('N a m e', evidence)
    evidence = _evidence(vertical=True)
    evidence['words'][2]['font'] = 'Caption'
    assert not pdf_quality._vertical_fragment('N a m e', evidence)
    evidence = _evidence(vertical=True)
    evidence['blocks'][0]['lines'].append({'text': 'Body text'})
    assert not pdf_quality._vertical_fragment('N a m e', evidence)


def test_repeated_decorative_vertical_glyphs_are_not_semantic_source():
    evidence = _evidence(vertical=True)
    for word in evidence['words']:
        word['font'] = 'Display'
    signature = 'repeated-art'
    evidence['vertical_spans'] = [{'joined': 'name', 'signature': signature,
                                   'font': 'Display', 'size': 12,
                                   'margin': True, 'large': True, 'body_font_used': False,
                                   'bbox': [18, 35, 30, 90]}]
    evidence['repeated_vertical_signatures'] = [signature]
    result = _select('N a m e', 'STR 60\n' + FILLER, evidence=evidence)
    assert result['status'] == 'accepted'
    assert result['excluded_fragments'] == ['decorative_vertical_glyph']


def test_decorative_classification_needs_all_structural_signals():
    evidence = _evidence(vertical=True)
    for word in evidence['words']:
        word['font'] = 'Display'
    evidence['vertical_spans'] = [{'joined': 'name', 'signature': 'art',
                                   'font': 'Display', 'size': 12,
                                   'margin': True, 'large': True, 'body_font_used': False,
                                   'bbox': [18, 35, 30, 90]}]
    assert _select('N a m e', 'STR 60\n' + FILLER, evidence=evidence)['status'] == 'source_content_loss'


def test_decorative_signature_must_repeat_on_distinct_pages():
    pages = [{'evidence': {'vertical_spans': [{'signature': 'art'}]}} for _ in range(2)]
    pdf_quality.bind_repeated_vertical_evidence(pages)
    assert all(not row['evidence']['repeated_vertical_signatures'] for row in pages)
    pages.append({'evidence': {'vertical_spans': [{'signature': 'art'}]}})
    pdf_quality.bind_repeated_vertical_evidence(pages)
    assert all(row['evidence']['repeated_vertical_signatures'] == ['art'] for row in pages)


def test_normal_horizontal_name_is_preserved():
    assert _select('Name', 'STR 60\n' + FILLER)['status'] == 'strong_baseline'


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


@pytest.mark.parametrize('changed', ['1d6-2', '1d6+1', '1d8+2', '2d6+2'])
def test_unbound_dice_operator_and_value_are_mechanics(changed: str):
    result = _select('31\nRoll 1d6+2!', f'Roll {changed}!\n' + FILLER,
                     evidence=_evidence(folio=True))
    assert result['status'] == 'mechanic_loss'


@pytest.mark.parametrize(('original', 'changed'), [
    ('1d6', '1d8'), ('2d6', '1d6'), ('SAN 1/1D6', 'SAN 0/1D6'),
    ('SAN 1/1D6', 'SAN 1/1D4'), ('STR 60', 'STR 50'),
])
def test_known_mechanics_changes_reject(original: str, changed: str):
    result = _select('31\n' + original, changed + '\n' + FILLER,
                     evidence=_evidence(folio=True))
    assert result['status'] in {'mechanic_loss', 'pair_mismatch'}


def test_mechanics_case_and_whitespace_are_safe_with_added_content():
    result = _select('31\nRoll 1D6 + 2!\nSAN 1 / 1D6',
                     'Roll 1d6+2!\nSAN 1/1d6\n' + FILLER,
                     evidence=_evidence(folio=True))
    assert result['status'] == 'accepted'


def test_ordinary_prose_terminal_punctuation_is_not_new_mechanic():
    result = _select('31\nDoor open.', 'Door open\n' + FILLER,
                     evidence=_evidence(folio=True))
    assert result['status'] == 'accepted'


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
