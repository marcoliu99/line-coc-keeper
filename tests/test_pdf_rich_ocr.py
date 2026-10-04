"""Synthetic evidence for the narrow, source-bound rich OCR rescue."""
from __future__ import annotations

from unittest.mock import patch

import pymupdf
import pytest

from app import pdf_loader, pdf_ocr, pdf_quality

ROLE = ('Investigator Name: Alice\nSTR 60\nCON 55\nDEX 70\nLUCK 50\n'
        'Spot Hidden 65%\nDamage 1d6+1\n' + 'Background details. ' * 20)
BASE = '31\nScenario Header\nInvestigator'


def _stage(baseline: str = BASE, candidate: str = ROLE) -> tuple[dict, object]:
    return pdf_quality.rich_ocr_stage_one(baseline, candidate, [], 200)


def test_rich_role_sheet_ignores_folio_and_header_but_keeps_mechanics():
    evidence, added = _stage()
    assert evidence['status'] == 'accepted'
    assert added.total() == 6
    assert pdf_quality.rich_ocr_corroboration(added, ROLE) == (6, 0, 0)


@pytest.mark.parametrize(('baseline', 'candidate', 'status'), [
    ('STR 60', ROLE.replace('STR 60', 'STR 50'), 'mechanic_loss'),
    ('Damage 1d6+2', 'Damage 1d6\n' + ROLE, 'mechanic_loss'),
    ('SAN 1/1d6', 'SAN 1d6/1\n' + ROLE, 'mechanic_loss'),
    ('A unique Keeper instruction must be preserved.', ROLE, 'source_content_loss'),
    ('STR 60\nDEX 70', 'Unrelated description. ' * 20, 'mechanic_loss'),
    ('Narrative source. ' * 30, ROLE, 'not_low_text_source'),
    (BASE, 'short', 'candidate_too_short'),
])
def test_stage_one_rejects_unsafe_candidate(baseline, candidate, status):
    assert _stage(baseline, candidate)[0]['status'] == status


@pytest.mark.parametrize(('paddle', 'counts'), [
    (ROLE, (6, 0, 0)),
    (ROLE.replace('LUCK 50', 'LUCK 80'), (5, 0, 1)),
    (ROLE.replace('Spot Hidden 65%', 'Spot Hidden 50%'), (5, 0, 1)),
    (ROLE.replace('LUCK 50', ''), (5, 1, 0)),
    (ROLE.replace('Damage 1d6+1', 'Damage 1d6'), (5, 0, 1)),
])
def test_all_new_bound_mechanics_need_second_opinion(paddle, counts):
    _, added = _stage()
    assert pdf_quality.rich_ocr_corroboration(added, paddle) == counts


def test_san_order_and_die_modifier_are_whole_values():
    candidate = 'SAN 1/1d6\nDamage 1d6+2\n' + 'Background details. ' * 20
    evidence, added = _stage(candidate=candidate)
    assert evidence['status'] == 'accepted'
    assert pdf_quality.rich_ocr_corroboration(added, candidate) == (2, 0, 0)
    assert pdf_quality.rich_ocr_corroboration(added, candidate.replace('1/1d6', '1d6/1')) == (1, 0, 1)
    assert pdf_quality.rich_ocr_corroboration(added, candidate.replace('1d6+2', '1d6')) == (1, 0, 1)


def test_dice_normalization_only_repairs_known_expression():
    assert pdf_ocr.normalize_dice_ocr('Damage ld6+2', '1d6+2') == 'Damage 1d6+2'
    assert pdf_ocr.normalize_dice_ocr('Damage ld8', '1d6') == 'Damage ld8'
    assert pdf_ocr.normalize_dice_ocr('Idea library Index', '1d6') == 'Idea library Index'


def test_table_values_keep_row_and_column_binding():
    candidate = '| Skill | Value | Half | Fifth |\n|---|---:|---:|---:|\n| Unarmed | 45 | 22 | 9 |\n'
    mechanics = pdf_quality.rich_ocr_mechanics(candidate)
    assert mechanics[('UNARMED[1]', '45')] == 1
    assert mechanics[('UNARMED[2]', '22')] == 1
    assert pdf_quality.rich_ocr_corroboration(mechanics, candidate) == (3, 0, 0)
    assert pdf_quality.rich_ocr_corroboration(mechanics, candidate.replace('| 45 |', '| 55 |')) == (2, 0, 1)


def test_existing_mechanics_do_not_require_a_second_opinion():
    candidate = 'STR 60\n' + 'Background details. ' * 20
    evidence, added = _stage('STR 60', candidate)
    assert evidence['status'] == 'accepted'
    assert evidence['paddle_verification_attempted'] is False
    assert not added


def test_loader_promotes_only_after_paddle_confirms_and_keeps_warning_history():
    with pymupdf.open() as doc:
        doc.new_page()
        payload = doc.tobytes()
    report = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(pdf_loader.pdf_quality, 'native_text', return_value=(BASE, [])), \
         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle',
                      return_value=pdf_loader.pdf_layout.LayoutResult(reason='incomplete_mapping')), \
         patch.object(pdf_loader, '_page_has_graphic_content', return_value=True), \
         patch.object(pdf_loader, '_render_page_png', return_value=b'png'), \
         patch.object(pdf_loader, '_markitdown_page_texts', return_value={1: ROLE}), \
         patch.object(pdf_loader.pdf_ai_repair, 'repair_page',
                      side_effect=lambda _page, _row, text, _budget: (text, {'regions': [], 'unresolved_labels': []})), \
         patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(text=ROLE, status='accepted')) as paddle:
        text, _, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
    assert ROLE.strip() in text
    assert report['pages'][0]['method'] == 'markitdown'
    assert report['pages'][0]['rich_ocr_validation']['status'] == 'accepted'
    assert 'low_text' in report['pages'][0]['warnings']
    paddle.assert_called_once_with(b'png')


@pytest.mark.parametrize('status', ['unavailable', 'rejected', 'empty', 'error'])
def test_loader_rejects_candidate_when_paddle_cannot_verify(status):
    with pymupdf.open() as doc:
        doc.new_page()
        payload = doc.tobytes()
    report = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(pdf_loader.pdf_quality, 'native_text', return_value=(BASE, [])), \
         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle',
                      return_value=pdf_loader.pdf_layout.LayoutResult(reason='incomplete_mapping')), \
         patch.object(pdf_loader, '_page_has_graphic_content', return_value=True), \
         patch.object(pdf_loader, '_render_page_png', return_value=b'png'), \
         patch.object(pdf_loader, '_markitdown_page_texts', return_value={1: ROLE}), \
         patch.object(pdf_loader.pdf_ai_repair, 'repair_page',
                      side_effect=lambda _page, _row, text, _budget: (text, {'regions': [], 'unresolved_labels': []})), \
         patch.object(pdf_loader, '_analyze_graphic_page', return_value=('', None)), \
         patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle', return_value=pdf_ocr.OcrResult(status=status)):
        text, _, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
    assert ROLE not in text
    assert report['pages'][0]['rich_ocr_validation']['status'] == 'paddle_' + status
