"""A repeated layout string is removable only when its PDF source is singular."""

from unittest.mock import patch

import pymupdf

from app import pdf_loader, pdf_quality


def _page(*, body: str = 'The lantern sheds 1d6+2 light.',
          continuation: str = 'The pier remains ahead.') -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((70, 100), f'South Dock\n{body}\n{continuation}', fontsize=9)
    return doc, page


def _repair(page: pymupdf.Page, layout: str, *, decorative_status: str = 'not_applicable') -> tuple[str, dict]:
    return pdf_quality.repair_duplicate_source_layout(
        page, layout, page.get_text('text'), method='layout', pairs=[],
        decorative_status=decorative_status,
    )


def test_one_source_run_emitted_twice_keeps_paragraph_flow_and_mechanics() -> None:
    doc, page = _page()
    with doc:
        old = ('South Dock The lantern sheds 1d6+2 light.\n\n'
               'The lantern sheds 1d6+2 light. The pier remains ahead.')
        native = page.get_text('text')
        new, decision = _repair(page, old)
    assert decision['status'] == 'duplicate_source_emission_repaired'
    assert decision['source_fragment_count'] == 1
    assert decision['kept_range'] == [43, 73]
    assert new.count('The lantern sheds 1d6+2 light.') == 1
    assert 'The lantern sheds 1d6+2 light. The pier remains ahead.' in new
    assert pdf_quality._mechanic_tokens(new) == pdf_quality._mechanic_tokens(native)


def test_survivor_can_be_first_when_second_emission_is_orphaned() -> None:
    doc, page = _page()
    with doc:
        old = ('South Dock\n\nThe lantern sheds 1d6+2 light. The pier remains ahead.\n\n'
               'The lantern sheds 1d6+2 light.')
        new, decision = _repair(page, old)
    assert decision['status'] == 'duplicate_source_emission_repaired'
    assert decision['kept_range'] == [12, 42]
    assert new.count('The lantern sheds 1d6+2 light.') == 1


def test_two_source_blocks_with_identical_text_remain_distinct() -> None:
    doc, page = _page()
    with doc:
        page.insert_text((350, 300), 'The lantern sheds 1d6+2 light.', fontsize=9)
        old = ('South Dock The lantern sheds 1d6+2 light.\n\n'
               'The lantern sheds 1d6+2 light. The pier remains ahead.')
        assert _repair(page, old)[0] == old


def test_interleaved_sidebar_does_not_hide_a_distinct_split_source() -> None:
    doc, page = _page(body='The lantern sheds light.')
    with doc:
        page.insert_text((350, 300), 'The lantern sh', fontsize=9)
        page.insert_text((70, 500), 'Unrelated sidebar text.', fontsize=9)
        page.insert_text((350, 312), 'eds light.', fontsize=9)
        native = page.get_text('text')
        assert 'The lantern sh\nUnrelated sidebar text.\neds light.' in native
        old = ('South Dock The lantern sheds light.\n\n'
               'The lantern sheds light. The pier remains ahead.\n\n'
               'Unrelated sidebar text.')
        paths = pdf_quality._source_fragment_sequences(
            page.get_text('rawdict')['blocks'], 'The lantern sheds light.')
        new, decision = _repair(page, old)
    assert paths is not None and len(paths) == 2
    assert sorted(map(len, paths)) == [1, 2]
    assert new == old
    assert new.count('The lantern sheds light.') == 2
    assert decision['status'] == 'duplicate_source_emission_ambiguous'


def test_hyphenated_split_source_has_distinct_fragment_identity() -> None:
    doc, page = _page(body='The lantern sheds light.')
    with doc:
        page.insert_text((350, 300), 'The lantern sh-', fontsize=9)
        page.insert_text((70, 500), 'Unrelated sidebar text.', fontsize=9)
        page.insert_text((350, 312), 'eds light.', fontsize=9)
        old = ('South Dock The lantern sheds light.\n\n'
               'The lantern sheds light. The pier remains ahead.\n\n'
               'Unrelated sidebar text.')
        new, decision = _repair(page, old)
    assert new == old
    assert decision['status'] == 'duplicate_source_emission_ambiguous'


def test_same_sentence_in_two_columns_is_not_deduplicated() -> None:
    doc, page = _page(body='A repeated rule is intentional.')
    with doc:
        page.insert_text((350, 100), 'A repeated rule is intentional.', fontsize=9)
        old = ('South Dock A repeated rule is intentional.\n\n'
               'A repeated rule is intentional. The pier remains ahead.')
        assert _repair(page, old)[0] == old


def test_running_header_and_body_heading_with_same_text_are_both_kept() -> None:
    doc, page = _page(body='South Dock')
    with doc:
        page.insert_text((70, 30), 'South Dock', fontsize=9)
        old = 'South Dock\n\nSouth Dock\n\nSouth Dock The pier remains ahead.'
        assert _repair(page, old)[0] == old


def test_overlay_pdf_text_objects_are_not_treated_as_one_source() -> None:
    doc, page = _page()
    with doc:
        page.insert_text((70, 109), 'The lantern sheds 1d6+2 light.', fontsize=9)
        old = ('South Dock The lantern sheds 1d6+2 light.\n\n'
               'The lantern sheds 1d6+2 light. The pier remains ahead.')
        assert _repair(page, old)[0] == old


def test_ambiguous_neighbor_or_survivor_alignment_fails_closed() -> None:
    doc, page = _page()
    with doc:
        old = ('South Dock The lantern sheds 1d6+2 light. The pier remains ahead.\n\n'
               'The lantern sheds 1d6+2 light. The pier remains ahead.')
        new, decision = _repair(page, old)
    assert new == old
    assert decision['status'] == 'duplicate_source_emission_ambiguous'


def test_punctuation_difference_does_not_trigger_fuzzy_dedup() -> None:
    doc, page = _page()
    with doc:
        old = ('South Dock The lantern sheds 1d6-2 light.\n\n'
               'The lantern sheds 1d6+2 light. The pier remains ahead.')
        assert _repair(page, old)[0] == old


def test_unresolved_decorative_alignment_blocks_duplicate_repair() -> None:
    doc, page = _page()
    with doc:
        old = ('South Dock The lantern sheds 1d6+2 light.\n\n'
               'The lantern sheds 1d6+2 light. The pier remains ahead.')
        new, decision = _repair(page, old, decorative_status='ambiguous_alignment')
    assert new == old
    assert decision['status'] == 'duplicate_source_emission_ambiguous'


def test_distinct_repeated_san_sources_remain_both() -> None:
    doc, page = _page(body='SAN 1/1D6')
    with doc:
        page.insert_text((350, 300), 'SAN 1/1D6', fontsize=9)
        old = 'South Dock SAN 1/1D6\n\nSAN 1/1D6 The pier remains ahead.'
        assert _repair(page, old)[0] == old


def test_one_san_source_emitted_twice_preserves_exact_expression() -> None:
    doc, page = _page(body='SAN 1/1D6')
    with doc:
        old = 'South Dock SAN 1/1D6\n\nSAN 1/1D6 The pier remains ahead.'
        new, decision = _repair(page, old)
    assert decision['status'] == 'duplicate_source_emission_repaired'
    assert new.count('SAN 1/1D6') == 1
    assert 'SAN 1/1D6 The pier remains ahead.' in new


def test_missing_source_mechanic_rejects_the_proposed_repair() -> None:
    doc, page = _page()
    with doc:
        page.insert_text((70, 200), 'STR 60', fontsize=9)
        old = ('South Dock The lantern sheds 1d6+2 light.\n\n'
               'The lantern sheds 1d6+2 light. The pier remains ahead.')
        new, decision = _repair(page, old)
    assert new == old
    assert decision['status'] == 'duplicate_source_emission_preservation_failed'


def test_loader_uses_source_dedup_after_decorative_repair_and_keeps_review() -> None:
    doc, page = _page()
    with doc:
        payload = doc.tobytes()
        native = page.get_text('text')
    layout = ('South Dock The lantern sheds 1d6+2 light.\n\n'
              'The lantern sheds 1d6+2 light. The pier remains ahead.')
    report: dict = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': layout}}), \
         patch.object(pdf_loader.pdf_quality, 'native_text',
                      return_value=(native, ['ambiguous_columns'])), \
         patch.object(pdf_loader.pdf_quality, 'repair_decorative_layout',
                      side_effect=lambda text, *_args, **_kwargs: (text, {'status': 'repaired'})), \
         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle',
                      return_value=pdf_loader.pdf_layout.LayoutResult(reason='model_unavailable')), \
         patch.object(pdf_loader, '_page_has_graphic_content', return_value=False):
        text, reviews, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
    assert text.count('The lantern sheds 1d6+2 light.') == 1
    assert report['pages'][0]['source_duplicate']['status'] == 'duplicate_source_emission_repaired'
    assert reviews == [1]
    assert 'ambiguous_columns' in report['pages'][0]['warnings']


def test_loader_keeps_review_when_duplicate_alignment_is_ambiguous() -> None:
    doc, _page_obj = _page()
    with doc:
        payload = doc.tobytes()
    layout = ('South Dock The lantern sheds 1d6+2 light. The pier remains ahead.\n\n'
              'The lantern sheds 1d6+2 light. The pier remains ahead.')
    report: dict = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': layout}}), \
         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle',
                      return_value=pdf_loader.pdf_layout.LayoutResult(reason='model_unavailable')), \
         patch.object(pdf_loader, '_page_has_graphic_content', return_value=False):
        text, reviews, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
    assert text.count('The lantern sheds 1d6+2 light.') == 2
    assert report['pages'][0]['source_duplicate']['status'] == 'duplicate_source_emission_ambiguous'
    assert 'duplicate_source_emission_ambiguous' in report['pages'][0]['warnings']
    assert reviews == [1]
