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


def test_resumed_page_reuses_text_images_and_maps_without_parser_work():
    source = pdf('Accepted first page.', 'Pending second page.')
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader.pdf_layout, 'analyze_page', side_effect=[
             decision('Accepted first page.'), decision('Pending second page.', 'needs_review')]), \
         patch.object(loader.pdf_layout_adapters, 'resolve_page', side_effect=lambda page, result, budget: result), \
         pytest.raises(loader.LayoutReviewRequired):
        loader.extract_text(source, quality_report=report)
    first = report['pages'][0]
    cached = {1: {'pdf_sha256': hashlib.sha256(source).hexdigest(),
        'pipeline_version': loader.PIPELINE_VERSION, 'selected_text': first['selected_text'],
        'selected_sha256': first['selected_sha256'], 'report': first, 'image': b'image',
        'map': {'entry_room_id': 'door'}, 'derived_description': 'labeled derived map'}}
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
    budget = {'remaining_requests': 0, 'remaining_pages': 0, 'retries': 1, 'metrics': {'image_calls': 8}}
    report = {}
    with patch.object(loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(loader.pdf_layout, 'analyze_page', return_value=decision('Unresolved page.', 'needs_review')), \
         patch.object(loader.pdf_layout_adapters, 'resolve_page', side_effect=lambda page, result, current: result) as resolve, \
         pytest.raises(loader.LayoutReviewRequired):
        loader.extract_text(source, quality_report=report, layout_budget=budget)
    assert resolve.call_args.args[2] == budget
    assert report['layout_budget'] == budget
    assert report['layout_budget'] is not budget
