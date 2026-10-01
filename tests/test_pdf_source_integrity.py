"""Final canonical source, rather than challenger diagnostics, controls publication."""
from types import SimpleNamespace

import pymupdf
import pytest

from app import config, pdf_ai_repair, pdf_loader, pdf_ocr, pdf_quality
from app.providers import registry


@pytest.fixture
def extraction_without_providers(monkeypatch):
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_a, **_kw: None)
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {'engine': 'paddleocr',
        'model': 'PP-OCRv5_mobile_rec', 'candidate': '', 'status': 'empty'})
    monkeypatch.setattr(pdf_loader, '_ocr_image', lambda *_: '')
    monkeypatch.setattr(pdf_ai_repair, 'analysis_provider', lambda: None)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_a, **_kw: None))


def source_pdf():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((30, 80), 'Damage xd6+2. This weapon hurts the investigator.')
        return doc.tobytes()


@pytest.mark.parametrize('mechanic', ['�d6+2', '1d�+2', '1d6+�', '�%', '+�2'])
def test_final_corrupted_mechanics_hard_block(extraction_without_providers, monkeypatch, mechanic):
    # PDF font decoding can return replacement glyphs; inject that parser result.
    monkeypatch.setattr(pdf_quality, 'native_text', lambda _: ('Damage ' + mechanic, []))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(source_pdf(), quality_report=report, local_ocr_limit=0, ai_repair_limit=0)
    assert report['hard_block_pages'] == report['blocked_pages'] == [1]
    assert 'source_mechanics_unresolved' in report['pages'][0]['source_blocking_reasons']
    assert report['scenario_readiness'] == 'BLOCKED'


def test_clean_final_source_with_failed_challenger_not_blocked(extraction_without_providers, monkeypatch):
    monkeypatch.setattr(pdf_quality, 'native_text', lambda _: ('Damage 1d6+2', []))
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks',
                        lambda *_: {1: {'text': 'Damage 1d10+99'}})
    report = {}
    text, *_ = pdf_loader.extract_text(source_pdf(), quality_report=report, local_ocr_limit=0, ai_repair_limit=0)
    assert 'Damage 1d6+2' in text
    assert report['pages'][0]['candidates']['layout'] == 'Damage 1d10+99'
    assert 'layout_numeric_loss' in report['pages'][0]['warnings']
    assert report['blocked_pages'] == []


def test_prose_replacement_glyph_is_not_dice_corruption(extraction_without_providers, monkeypatch):
    monkeypatch.setattr(pdf_quality, 'native_text', lambda _: ('The old � portrait hangs in the hallway.', []))
    report = {}
    pdf_loader.extract_text(source_pdf(), quality_report=report, local_ocr_limit=0, ai_repair_limit=0)
    assert 'source_mechanics_unresolved' not in report['pages'][0]['source_blocking_reasons']


def raster_pdf(rectangles, text='Investigator'):
    with pymupdf.open() as tile:
        tile.new_page(width=600, height=350).insert_text((30, 60), 'STR 60 DEX 55 Damage 1d6+2')
        png = tile[0].get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_textbox((30, 20, 570, 60), text, fontsize=8)
        for rectangle in rectangles:
            page.insert_image(rectangle, stream=png, keep_proportion=False)
        return doc.tobytes()


@pytest.mark.parametrize('rectangles', [
    [(0, 70, 600, 420), (0, 430, 600, 780)], [(0, 70, 600, 780)]])
def test_raster_source_with_header_requires_verified_transcription(extraction_without_providers, rectangles):
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(raster_pdf(rectangles), quality_report=report)
    row = report['pages'][0]
    assert row['raster_source_gap'] is True
    assert row['requires_image_transcription'] is True
    assert row['page_ocr_attempts']
    assert 'source_image_transcription_unverified' in row['source_blocking_reasons']


def test_overlapping_images_use_union_not_sum(extraction_without_providers):
    report = {}
    pdf_loader.extract_text(raster_pdf([(0, 70, 600, 420), (0, 70, 600, 420)]), quality_report=report)
    assert report['pages'][0]['raster_source_gap'] is False
    assert report['blocked_pages'] == []


def test_decorative_images_with_native_narrative_are_not_image_only(extraction_without_providers):
    with pymupdf.open(stream=raster_pdf([(0, 70, 80, 110)]), filetype='pdf') as doc:
        doc[0].insert_textbox((30, 180, 550, 650), 'The investigator follows the original safe narrative. ' * 16)
        raw = doc.tobytes()
    report = {}
    pdf_loader.extract_text(raw, quality_report=report)
    assert report['pages'][0]['requires_image_transcription'] is False
    assert report['blocked_pages'] == []


def test_known_native_pairs_do_not_certify_unseen_tiled_mechanics(extraction_without_providers):
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(raster_pdf([(0, 70, 600, 420), (0, 430, 600, 780)],
                                           'Investigator STR 60'), quality_report=report)
    assert report['pages'][0]['numeric_pairs']
    assert report['pages'][0]['requires_image_transcription'] is True


def test_successful_local_dice_repair_checks_final_selected_source(extraction_without_providers, monkeypatch):
    damaged = 'Damage �d6+2. This weapon hurts the investigator.'
    repaired = 'Damage d6+2. This weapon hurts the investigator.'
    monkeypatch.setattr(pdf_quality, 'native_text', lambda _: (damaged, []))
    original = pdf_quality.block_evidence
    def evidence(page):
        data = original(page)
        for block in data['blocks']:
            for line in block['lines']:
                line['text'] = line['text'].replace('xd6', '�d6')
        return data
    monkeypatch.setattr(pdf_quality, 'block_evidence', evidence)
    # Exercise the production deterministic region gate through the OCR seam.
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {'engine': 'paddleocr',
        'model': 'PP-OCRv5_mobile_rec', 'candidate': repaired, 'status': 'candidate'})
    report = {}
    text, *_ = pdf_loader.extract_text(source_pdf(), quality_report=report)
    assert repaired in text
    assert report['pages'][0]['local_repairs'][0]['status'] == 'accepted'
    assert report['blocked_pages'] == []


def test_raster_union_measures_exact_area_and_clips_page():
    from app import pdf_raster_source

    raw = raster_pdf([(0, 70, 600, 420), (0, 70, 600, 420)])
    with pymupdf.open(stream=raw, filetype='pdf') as doc:
        assert pdf_raster_source.raster_union_coverage(doc[0]) == pytest.approx(.4375)
    raw = raster_pdf([(-50, 70, 650, 420), (0, 300, 600, 650)])
    with pymupdf.open(stream=raw, filetype='pdf') as doc:
        assert pdf_raster_source.raster_union_coverage(doc[0]) == pytest.approx(.725)


def test_cached_final_source_corruption_is_rechecked(extraction_without_providers):
    import copy
    import hashlib

    raw = source_pdf()
    initial = {}
    pdf_loader.extract_text(raw, quality_report=initial)
    row = copy.deepcopy(initial['pages'][0])
    damaged = 'Damage �d6+2'
    cache = {1: {'report': row, 'pdf_sha256': initial['pdf_sha256'],
        'pipeline_version': initial['pipeline_version'], 'renderer_version': initial['renderer_version'],
        'extraction_identity': initial['extraction_identity'], 'selected_text': damaged,
        'selected_sha256': hashlib.sha256(damaged.encode()).hexdigest(), 'map': None}}
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(raw, quality_report=report, resume_pages=cache)
    assert report['pages'][0]['resumed'] is True
    assert report['hard_block_pages'] == [1]
    assert 'source_mechanics_unresolved' in report['pages'][0]['source_blocking_reasons']


def test_large_background_with_complete_native_body_is_not_image_only(extraction_without_providers):
    with pymupdf.open(stream=raster_pdf([(0, 70, 600, 780)]), filetype='pdf') as doc:
        doc[0].insert_textbox((30, 180, 550, 650), 'The original native narrative is complete. ' * 20)
        raw = doc.tobytes()
    report = {}
    pdf_loader.extract_text(raw, quality_report=report)
    assert report['pages'][0]['requires_image_transcription'] is False
    assert report['blocked_pages'] == []
