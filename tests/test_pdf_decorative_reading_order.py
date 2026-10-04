"""A margin glyph must not become a word inside canonical PDF prose."""

import copy
from unittest.mock import patch

import pymupdf
import pytest

from app import pdf_loader, pdf_quality


def _evidence(*, repeated: bool = True, margin: bool = True, large: bool = True,
              body_font_used: bool = False, body_x: float = 60) -> dict:
    signature = 'synthetic-signature'
    return {
        'width': 600, 'height': 800,
        'repeated_vertical_signatures': [signature] if repeated else [],
        'vertical_spans': [{
            'signature': signature, 'joined': 'abc', 'font': 'display-font', 'size': 24,
            'bbox': [20, 50, 50, 122], 'margin': margin, 'large': large,
            'body_font_used': body_font_used,
        }],
        'blocks': [
            {'id': 1, 'bbox': [20, 50, 50, 98], 'lines': [
                {'bbox': [25, 50, 40, 74], 'text': 'A'},
                {'bbox': [25, 74, 40, 98], 'text': 'b'},
            ]},
            {'id': 2, 'bbox': [20, 98, 50, 122], 'lines': [
                {'bbox': [25, 98, 40, 122], 'text': 'c'},
            ]},
            {'id': 3, 'bbox': [body_x, 60, 280, 150], 'lines': [
                {'bbox': [body_x, 60, 280, 77], 'text': 'The keeper has 1d6+2 damage.'},
                {'bbox': [body_x, 90, 180, 107], 'text': 'He acts now.'},
            ]},
        ],
        'words': [
            {'text': 'A', 'bbox': [25, 50, 40, 74], 'block': 1, 'line': 0,
             'font': 'display-font', 'font_size': 24},
            {'text': 'b', 'bbox': [25, 74, 40, 98], 'block': 1, 'line': 1,
             'font': 'display-font', 'font_size': 24},
            {'text': 'c', 'bbox': [25, 98, 40, 122], 'block': 2, 'line': 0,
             'font': 'display-font', 'font_size': 24},
        ],
    }


def _repair(layout: str, *, evidence: dict | None = None) -> tuple[str, dict]:
    return pdf_quality.repair_decorative_layout(
        layout, evidence or _evidence(), warnings=['ambiguous_columns'],
        method='layout', pairs=[],
    )


def test_exact_interleave_repairs_only_decorative_characters() -> None:
    old = '## Heading\n\nA The keeper has 1d6+2 b damage. c He acts now.\n\nOther text.'
    new, result = _repair(old)
    assert result['status'] == 'repaired'
    assert new == '## Heading\n\nThe keeper has 1d6+2 damage. He acts now.\n\nOther text.'
    assert pdf_quality._mechanic_tokens(new) == pdf_quality._mechanic_tokens(old)


def test_ambiguous_second_interleave_fails_closed() -> None:
    old = ('A The keeper has 1d6+2 b damage. c He acts now.\n\n'
           'A The keeper has 1d6+2 b damage. c He acts now.')
    new, result = _repair(old)
    assert new == old
    assert result['status'] != 'repaired'


def test_semantic_margin_text_is_not_discarded() -> None:
    old = 'A The keeper has 1d6+2 b damage. c He acts now.'
    for evidence in [_evidence(repeated=False), _evidence(margin=False),
                     _evidence(large=False), _evidence(body_font_used=True)]:
        assert _repair(old, evidence=evidence)[0] == old


def test_missing_body_word_fails_instead_of_using_a_reparse() -> None:
    old = 'A The keeper has 1d6+2 b damage. c He now.'
    new, result = _repair(old)
    assert new == old
    assert result['status'] != 'repaired'


@pytest.mark.parametrize('semantic_text', [
    'Read this sidebar', 'Figure caption', 'Chapter heading', 'Footnote rule',
])
def test_coherent_margin_text_is_not_deleted(semantic_text: str) -> None:
    evidence = copy.deepcopy(_evidence())
    evidence['blocks'][0]['lines'].append({'bbox': [20, 54, 50, 71], 'text': semantic_text})
    old = 'A The keeper has 1d6+2 b damage. c He acts now.'
    assert _repair(old, evidence=evidence)[0] == old


def test_genuine_vertical_text_remains_when_not_structurally_decorative() -> None:
    evidence = _evidence(repeated=False)
    old = 'A The keeper has 1d6+2 b damage. c He acts now.'
    assert _repair(old, evidence=evidence)[0] == old


def test_cross_column_alignment_is_not_repaired() -> None:
    old = 'A The keeper has 1d6+2 b damage. c He acts now.'
    assert _repair(old, evidence=_evidence(body_x=310))[0] == old


def test_unrelated_right_column_keeps_its_order_and_text() -> None:
    evidence = copy.deepcopy(_evidence())
    evidence['blocks'].append({'id': 4, 'bbox': [320, 60, 550, 150], 'lines': [
        {'bbox': [320, 60, 550, 77], 'text': 'The second column starts here.'},
        {'bbox': [320, 90, 550, 107], 'text': 'It continues here.'},
    ]})
    old = ('A The keeper has 1d6+2 b damage. c He acts now.\n\n'
           'The second column starts here. It continues here.')
    new, result = _repair(old, evidence=evidence)
    assert result['status'] == 'repaired'
    assert new == ('The keeper has 1d6+2 damage. He acts now.\n\n'
                   'The second column starts here. It continues here.')


def test_bound_mechanics_and_operators_survive_local_edit() -> None:
    evidence = copy.deepcopy(_evidence())
    evidence['blocks'][2]['lines'][0]['text'] = 'STR 60 deals 1d6+2 and SAN 1/1d6.'
    evidence['blocks'][2]['lines'][1]['text'] = 'A 25% chance remains.'
    old = 'A STR 60 deals 1d6+2 b and SAN 1/1d6. c A 25% chance remains.'
    new, result = _repair(old, evidence=evidence)
    assert result['status'] == 'repaired'
    assert all(value in new for value in ('STR 60', '1d6+2', 'SAN 1/1d6', '25%'))


@pytest.mark.parametrize('wrong', ['1d6-2', '1d6+1'])
def test_layout_mechanics_conflict_with_source_fails_closed(wrong: str) -> None:
    evidence = copy.deepcopy(_evidence())
    old = f'A The keeper has {wrong} b damage. c He acts now.'
    new, result = _repair(old, evidence=evidence)
    assert new == old
    assert result['status'] != 'repaired'


def test_loader_selects_local_repair_and_keeps_review_history() -> None:
    layout = 'A The keeper has 1d6+2 b damage. c He acts now.'
    native = 'The keeper has 1d6+2 damage. He acts now.'
    with pymupdf.open() as doc:
        for _ in range(3):
            doc.new_page()
        payload = doc.tobytes()
    report: dict = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks',
                      return_value={i: {'text': layout} for i in range(1, 4)}), \
         patch.object(pdf_loader.pdf_quality, 'native_text',
                      return_value=(native, ['ambiguous_columns'])), \
         patch.object(pdf_loader.pdf_quality, 'block_evidence', side_effect=lambda _p: copy.deepcopy(_evidence())), \
         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle',
                      return_value=pdf_loader.pdf_layout.LayoutResult(reason='model_unavailable')), \
         patch.object(pdf_loader, '_page_has_graphic_content', return_value=False), \
         patch.object(pdf_loader.pdf_ai_repair, 'repair_page',
                      side_effect=lambda _p, _r, text, _b: (text, {'regions': [], 'unresolved_labels': []})):
        text, reviews, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
    assert text.count('The keeper has 1d6+2 damage. He acts now.') == 3
    assert reviews == [1, 2, 3]
    assert all(row['decorative_reading_order']['status'] == 'repaired' for row in report['pages'])
    assert all('ambiguous_columns' in row['warnings'] for row in report['pages'])


@pytest.mark.parametrize('graphic', [False, True])
def test_loader_records_low_text_after_decorative_repair(graphic: bool) -> None:
    body = 'The keeper waits beside the lighthouse.'
    layout = body + ' decorative glyphs ' * 12
    assert len(layout) >= pdf_loader._LOW_TEXT_THRESHOLD
    assert len(body) < pdf_loader._LOW_TEXT_THRESHOLD
    with pymupdf.open() as doc:
        doc.new_page()
        payload = doc.tobytes()
    report: dict = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': layout}}), \
         patch.object(pdf_loader.pdf_quality, 'native_text',
                      return_value=(body, ['ambiguous_columns'])), \
         patch.object(pdf_loader.pdf_quality, 'repair_decorative_layout',
                      return_value=(body, {'status': 'repaired'})), \
         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle',
                      return_value=pdf_loader.pdf_layout.LayoutResult(reason='model_unavailable')), \
         patch.object(pdf_loader, '_page_has_graphic_content', return_value=graphic), \
         patch.object(pdf_loader, '_render_page_png', return_value=b'png'), \
         patch.object(pdf_loader, '_markitdown_page_texts', return_value={}) as markitdown, \
         patch.object(pdf_loader, '_analyze_graphic_page', return_value=('', None)) as vision:
        _text, reviews, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
    assert report['pages'][0]['decorative_reading_order']['status'] == 'repaired'
    assert report['pages'][0]['extracted_chars'] < pdf_loader._LOW_TEXT_THRESHOLD
    assert 'low_text' in report['pages'][0]['warnings']
    assert reviews == [1]
    assert markitdown.call_count == int(graphic)
    assert vision.call_count == int(graphic)
