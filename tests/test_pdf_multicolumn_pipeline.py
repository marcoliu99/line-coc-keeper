"""Publication and resume use the same source-preserving page contract."""
import hashlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from app import scenario_library

_SPEC = importlib.util.spec_from_file_location(
    'app._pdf_multicolumn_pipeline', Path(__file__).parents[1] / 'app' / 'pdf_loader.py')
assert _SPEC and _SPEC.loader
loader = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = loader
_SPEC.loader.exec_module(loader)


def pdf(*texts):
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page()
            page.insert_textbox(pymupdf.Rect(40, 40, 550, 790), text)
        return document.tobytes()


def decision(text, status='accepted'):
    return {'status': status, 'selected_text': text, 'diagnostics': [], 'blocks': [],
            'ordered_ids': [], 'layout_kind': 'two_column'}


def test_order_result_still_passes_source_numeric_gate():
    source = pdf('Failure causes 2d6 damage.')
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader.pdf_layout, 'analyze_page', return_value=decision('Failure causes damage.')), \
         pytest.raises(loader.LayoutReviewRequired) as raised:
        loader.extract_text(source, quality_report=report)
    assert raised.value.result[0].endswith('Failure causes 2d6 damage.')
    assert report['blocked_pages'] == [1]
    assert 'layout_source_gate_failed' in report['pages'][0]['warnings']


def test_pending_page_never_reaches_library_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    with pytest.raises(ValueError, match='unresolved pages'):
        scenario_library.save_scenario(pdf('source'), title='Test', filename='test.pdf',
            preview='source', text='source', indexes={}, pregens=[], page_maps={}, page_images={},
            parse_quality={'blocked_pages': [1]})
    assert list(tmp_path.iterdir()) == []


def test_resumed_page_reuses_text_images_and_maps_without_parser_work(certified_map_result):
    source = pdf('Accepted first page.', 'Pending second page.')
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader.pdf_layout, 'analyze_page', side_effect=[
             decision('Accepted first page.'), decision('Pending second page.', 'needs_review')]), \
         patch.object(loader.pdf_layout_adapters, 'resolve_page', side_effect=lambda page, result, budget: result), \
         pytest.raises(loader.LayoutReviewRequired):
        loader.extract_text(source, quality_report=report)
    first = report['pages'][0]
    graph = {'entry_room_id': 'door', 'rooms': [{'id': 'door', 'name': 'Entrance'}]}
    first['map_analysis'] = certified_map_result('', graph, b'image').analysis
    cached = {1: {'pdf_sha256': hashlib.sha256(source).hexdigest(),
        'pipeline_version': loader.PIPELINE_VERSION, 'renderer_version': loader.RENDERER_VERSION,
        'extraction_identity': report['extraction_identity'], 'selected_text': first['selected_text'],
        'selected_sha256': first['selected_sha256'], 'report': first, 'image': b'image',
        'map': graph, 'derived_description': 'labeled derived map'}}
    resumed = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None) as parse, \
         patch.object(loader.pdf_layout, 'analyze_page', return_value=decision('Pending second page.')) as analyze, \
         patch.object(loader, '_repair_local_regions', wraps=loader._repair_local_regions) as repair:
        text, _, _, images, maps = loader.extract_text(source, resume_pages=cached, quality_report=resumed)
    parse.assert_called_once_with(source, [2])
    assert analyze.call_count == repair.call_count == 1
    assert images[1] == b'image' and maps[1]['entry_room_id'] == 'door'
    assert resumed['derived_descriptions']['1'] == 'labeled derived map'
    assert resumed['pages'][0]['resumed']
    assert text.count('--- 第 1 頁 ---') == text.count('--- 第 2 頁 ---') == 1


def test_cache_from_other_pdf_does_not_skip_source_checks():
    source = pdf('Original text.')
    cache = {1: {'pdf_sha256': 'other', 'pipeline_version': loader.PIPELINE_VERSION,
                'selected_text': 'wrong', 'selected_sha256': 'wrong', 'report': {}}}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None) as parse, \
         patch.object(loader.pdf_layout, 'analyze_page', return_value=decision('Original text.')) as analyze:
        text, *_ = loader.extract_text(source, resume_pages=cache)
    parse.assert_called_once_with(source, [1])
    analyze.assert_called_once()
    assert 'Original text.' in text and 'wrong' not in text


def test_published_manifest_distinguishes_pdf_and_rendered_source_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    source = pdf('Rule 2d6.')
    rendered = loader.render_source_pages(['Rule 2d6.'])
    sid = scenario_library.save_scenario(source, title='Test', filename='test.pdf', preview='Rule',
        text=rendered, indexes={}, pregens=[], page_maps={}, page_images={},
        parse_quality={'pipeline_version': loader.PIPELINE_VERSION, 'renderer_version': 1, 'blocked_pages': []})
    manifest = scenario_library.load_context(sid)['manifest']
    assert manifest['pdf_sha256'] == hashlib.sha256(source).hexdigest()
    assert manifest['content_hash'] == hashlib.sha256(rendered.encode()).hexdigest()
    assert manifest['parser_version'] == loader.PIPELINE_VERSION


def test_resume_keeps_book_layout_budget():
    source = pdf('Unresolved page.')
    budget = loader.pdf_layout_adapters.new_budget()
    budget.update(remaining_requests=0, remaining_pages=0, consumed_requests=budget['configured_max_requests'],
                  visited_pages=list(range(1, budget['configured_max_pages'] + 1)), legacy_consumed_pages=0)
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader.pdf_layout, 'analyze_page', return_value=decision('Unresolved page.', 'needs_review')), \
         patch.object(loader.pdf_layout_adapters, 'resolve_page', side_effect=lambda page, result, current: result) as resolve, \
         pytest.raises(loader.LayoutReviewRequired):
        loader.extract_text(source, quality_report=report, layout_budget=budget)
    assert resolve.call_args.args[2] == budget
    assert report['layout_budget'] == budget
    assert report['layout_budget'] is not budget


@pytest.mark.parametrize('changed', ['renderer_version', 'pymupdf', 'pymupdf4llm', 'docling', 'quality_version', 'layout_version'])
def test_changed_executable_identity_reprocesses_cached_page(changed):
    source = pdf('Verified source.')
    identity = loader.extraction_identity()
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None):
        loader.extract_text(source, quality_report=report)
    first = report['pages'][0]
    cached = {1: {'pdf_sha256': hashlib.sha256(source).hexdigest(),
        'pipeline_version': loader.PIPELINE_VERSION, 'renderer_version': loader.RENDERER_VERSION,
        'extraction_identity': identity, 'selected_text': first['selected_text'],
        'selected_sha256': first['selected_sha256'], 'report': first}}
    updated = dict(identity)
    updated[changed] = 'changed-version'
    with patch.object(loader, 'extraction_identity', return_value=updated), \
         patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None) as parse, \
         patch.object(loader.pdf_layout, 'analyze_page', wraps=loader.pdf_layout.analyze_page) as analyze:
        text, *_ = loader.extract_text(source, resume_pages=cached)
    assert 'Verified source.' in text
    parse.assert_called_once_with(source, [1])
    analyze.assert_called_once()


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('readable_page', [False, True])
def test_unreadable_graphic_page_blocks_publication(failed, readable_page):
    source = pdf('Readable first page.', '') if readable_page else pdf('')
    report = {}
    last_page = 1 if readable_page else 0
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader, '_page_has_graphic_content', side_effect=lambda page: page.number == last_page), \
         patch.object(loader, '_render_page_png', return_value=b'png'), \
         patch.object(loader, '_markitdown_page_texts', return_value=None), \
         patch.object(loader.pdf_image_transcription, 'analyze', side_effect=RuntimeError('offline') if failed else None,
                      return_value=None), \
         pytest.raises(loader.LayoutReviewRequired) as raised:
        loader.extract_text(source, quality_report=report)
    assert report['blocked_pages'] == [last_page + 1]
    row = report['pages'][last_page]
    assert row['disposition'] == 'needs_review'
    assert ('image_verification_failed' if failed else 'vision_empty') in row['warnings']
    assert raised.value.result[3][last_page + 1] == b'png'


def test_blank_page_without_graphics_keeps_legacy_disposition():
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader, '_analyze_graphic_page', side_effect=AssertionError('blank page has no visual evidence')):
        loader.extract_text(pdf('Readable source.', ''), quality_report=report)
    assert report['blocked_pages'] == []
    assert report['pages'][1]['disposition'] == 'legacy_route'


def test_rendered_unicode_page_markers_preserve_source_unit_spans():
    from app.scenario_authoring import unit_ranges

    pages = [('A room. 🎲 無損傷。\n\n' * 350).strip(), 'Never roll 2d6.\n\nSanity loss 0/1d4.']
    source = loader.render_source_pages(pages)
    spans = unit_ranges(source)
    assert ''.join(source[start:end] for start, end in spans) == source
    assert spans[0][0] == 0 and spans[-1][1] == len(source)
    for marker in ('--- 第 1 頁 ---', '--- 第 2 頁 ---'):
        assert source.count(marker) == 1
    quote = '🎲 無損傷。'
    start = source.index(quote)
    assert source[start:start + len(quote)] == quote
    assert len(source[:start].encode()) > start


def test_readable_floor_plan_still_builds_map(certified_map_result):
    source = pdf('BEACON ISLAND LIGHTHOUSE FLOOR PLAN ' + 'Room entrance corridor stairs. ' * 12)
    report = {}
    expected = {'entry_room_id': 'door', 'rooms': [{'id': 'door', 'name': 'Entrance'}]}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader, '_page_has_graphic_content', return_value=True), \
         patch.object(loader, '_render_page_png', return_value=b'png'), \
         patch.object(loader, '_analyze_graphic_page', return_value=certified_map_result('Ground floor entrance.', expected)) as analyze:
        text, _, _, _, maps = loader.extract_text(source, quality_report=report)
    analyze.assert_called_once()
    assert maps[1] == expected
    assert 'FLOOR PLAN' in text


def test_rejected_candidate_warning_does_not_mark_verified_order_for_review():
    report = {}
    accepted = decision('Rule causes 2d6 damage.')
    accepted['diagnostics'] = ['layout:numeric_dice_or_negation_change', 'native:reading_order_mismatch']
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader.pdf_layout, 'analyze_page', return_value=accepted):
        _, review, *_ = loader.extract_text(pdf('Rule causes 2d6 damage.'), quality_report=report)
    assert review == []
    assert 'layout:numeric_dice_or_negation_change' in report['pages'][0]['warnings']
    assert report['pages'][0]['review_reasons'] == []


@pytest.mark.parametrize('failed', [False, True])
def test_readable_floor_plan_without_graph_stays_draft(failed):
    source = pdf('FLOOR PLAN ' + 'Room entrance corridor stairs. ' * 12)
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader, '_page_has_graphic_content', return_value=True), \
         patch.object(loader, '_render_page_png', return_value=b'png'), \
         patch.object(loader, '_analyze_graphic_page', side_effect=RuntimeError('offline') if failed else None,
                      return_value=('Floor plan visible.', None)), \
         pytest.raises(loader.LayoutReviewRequired):
        loader.extract_text(source, quality_report=report)
    assert report['blocked_pages'] == [1]


def test_map_graph_survives_description_that_omits_numeric_source_labels(certified_map_result):
    source = pdf('FLOOR PLAN 12 ' + 'Room entrance corridor stairs. ' * 12)
    expected = {'entry_room_id': 'door', 'rooms': [{'id': 'door', 'name': 'Entrance'}]}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader, '_page_has_graphic_content', return_value=True), \
         patch.object(loader, '_render_page_png', return_value=b'png'), \
         patch.object(loader, '_analyze_graphic_page', return_value=certified_map_result('Entrance connects to stairs.', expected)):
        text, _, _, _, maps = loader.extract_text(source)
    assert maps == {1: expected}
    assert '12' in text


def test_keeper_map_with_readable_ocr_still_requires_graph(certified_map_result):
    source = pdf('Corbitt House Map (Keeper Version)')
    expected = {'entry_room_id': 'door', 'rooms': [{'id': 'door', 'name': 'Entrance'}]}
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': 'Room corridor stairs. ' * 20}}), \
         patch.object(loader, '_page_has_graphic_content', return_value=True), \
         patch.object(loader, '_render_page_png', return_value=b'png'), \
         patch.object(loader, '_analyze_graphic_page', return_value=certified_map_result('Map description.', expected)) as analyze:
        _, _, _, _, maps = loader.extract_text(source, quality_report=report)
    analyze.assert_called_once()
    assert maps == {1: expected}
