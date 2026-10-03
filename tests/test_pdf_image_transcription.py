"""Image-only publication is based on independent source agreement."""
import pytest

from app import pdf_loader, pdf_quality

_MARKITDOWN_CONVERT = pdf_loader._markitdown_page_texts


def test_independent_transcription_accepts_formatting_but_retains_all_mechanics():
    paddle = '調查員 STR 60 DEX 55\nSpot Hidden 50%\nDamage 1d10+DB\nThe door is not locked.'
    vision = '**調查員** STR: 60 DEX: 55\nSpot Hidden: 50%\nDamage 1d10+DB\nThe door is not locked.'
    assert pdf_quality.accept_independent_transcription(paddle, vision)


@pytest.mark.parametrize('vision', [
    'STR 55 DEX 60\nSpot Hidden 50%\nDamage 1d10+DB\nThe door is not locked.',
    'STR 60 DEX 55\nSpot Hidden 50\nDamage 1d10+DB\nThe door is not locked.',
    'STR 60 DEX 55\nSpot Hidden 50%\nDamage 1d10\nThe door is not locked.',
    'STR 60 DEX 55 HP 12\nSpot Hidden 50%\nDamage 1d10+DB\nThe door is not locked.',
    'STR 60 DEX 55\nSpot Hidden 50%\nDamage 1d10+DB\nThe door is locked.',
    'STR 60 DEX 55\nSpot Hidden 50%\nDamage 1d10+DB',
    '',
])
def test_independent_transcription_rejects_swaps_additions_loss_and_contradiction(vision):
    paddle = 'STR 60 DEX 55\nSpot Hidden 50%\nDamage 1d10+DB\nThe door is not locked.'
    assert not pdf_quality.accept_independent_transcription(paddle, vision)


@pytest.fixture
def scanned_page(monkeypatch):
    import io

    import pymupdf
    from PIL import Image

    from app import pdf_loader, pdf_ocr

    png = io.BytesIO()
    Image.new('RGB', (300, 400), 'white').save(png, format='PNG')
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_image(page.rect, stream=png.getvalue())
        raw = doc.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pdf_loader, '_analyze_graphic_page', lambda *_: ('', None))
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec',
        'candidate': 'STR 60 DEX 55 Damage 1d10+DB', 'status': 'candidate'})
    monkeypatch.setattr(pdf_loader, '_ocr_image', lambda *_: 'STR 50 DEX 55 Damage 1d10')
    return raw


def test_single_local_candidate_is_retained_privately_and_never_becomes_source(scanned_page):
    from app import pdf_loader

    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
        pdf_loader.extract_text(scanned_page, quality_report=report)
    row = report['pages'][0]
    assert row['image_transcription']['status'] == 'unverified'
    assert row['image_transcription']['engine'] == 'paddleocr'
    assert row['image_transcription']['candidate'] == 'STR 60 DEX 55 Damage 1d10+DB'
    assert row['image_transcription']['reason'] == 'no_independent_evidence'
    assert 'STR 60' not in pending.value.result[0]
    assert report['image_only_unverified'] == 1


def test_provider_agreement_can_publish_paddle_without_tesseract_agreement(scanned_page, monkeypatch):
    from types import SimpleNamespace

    from app import config, pdf_loader
    from app.providers import registry

    provider = SimpleNamespace(analyze_image=lambda *_args, **_kwargs: {
        'page_type': 'character_sheet', 'text': 'STR: 60 DEX: 55 Damage 1d10+DB'})
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    report = {}
    text, *_ = pdf_loader.extract_text(scanned_page, quality_report=report)
    assert 'STR 60 DEX 55 Damage 1d10+DB' in text
    assert report['pages'][0]['image_transcription']['status'] == 'authoritative'
    assert report['paddle_page_transcriptions_authoritative'] == 1
    assert report['ai_transcription_agreements'] == 1
    assert report['blocked_pages'] == []


def test_provider_verified_text_free_illustration_does_not_require_transcription(scanned_page, monkeypatch):
    from types import SimpleNamespace

    from app import config, pdf_loader, pdf_ocr
    from app.providers import registry

    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec', 'candidate': '', 'status': 'empty'})
    monkeypatch.setattr(pdf_loader, '_ocr_image', lambda *_: '')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_kwargs: {'page_type': 'illustration', 'text': ''}))
    report = {}
    import pymupdf
    with pymupdf.open(stream=scanned_page, filetype='pdf') as doc:
        doc.new_page().insert_text((40, 100), 'The Keeper describes the scene.')
        raw = doc.tobytes()
    _, review, *_ = pdf_loader.extract_text(raw, quality_report=report)
    assert report['blocked_pages'] == []
    assert review == []
    assert report['pages'][0]['image_page_type'] == 'illustration'


def test_short_safe_native_mechanics_survive_graphic_challenger_failure(scanned_page, monkeypatch):
    import pymupdf

    from app import pdf_loader

    # Safe native source with a decorative graphic, not an untranscribed
    # full-page scan whose additional mechanics are unknown.
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), 'STR 60 DEX 55 Damage 1d10+DB')
        for index in range(8):
            page.draw_rect((40 + index * 10, 710, 45 + index * 10, 720))
        raw = doc.tobytes()
    report = {}
    text, review, *_ = pdf_loader.extract_text(raw, quality_report=report)
    assert 'STR 60 DEX 55 Damage 1d10+DB' in text
    assert report['blocked_pages'] == []
    assert review == []
    assert report['pages'][0]['source_kind'] == 'native_text_present_but_short'


def test_reused_local_output_is_not_independent_markitdown_evidence(scanned_page, monkeypatch):
    from app import pdf_loader

    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts',
                        lambda *_args, **_kwargs: {1: 'STR 60 DEX 55 Damage 1d10+DB'})
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(scanned_page, quality_report=report)
    assert report['pages'][0]['image_transcription']['status'] == 'unverified'
    assert report['markitdown_transcription_agreements'] == 0


def test_markitdown_image_request_agreement_certifies_local_candidate(scanned_page, monkeypatch):
    import sys
    from types import SimpleNamespace

    from app import config, markitdown_shim, pdf_loader

    class MarkItDown:
        def __init__(self, **options):
            self.client = options['llm_client']

        def convert(self, *_args, **_kwargs):
            response = self.client.chat.completions.create(messages=[{'content': [
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}}]}])
            return SimpleNamespace(text_content='## Page 1\n' + response.choices[0].message.content)

    completion = SimpleNamespace(create=lambda **_: SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content='STR: 60 DEX: 55 Damage 1d10+DB'), finish_reason='stop')]))
    monkeypatch.setattr(markitdown_shim, 'ANALYSIS_PROVIDER', 'openai')
    monkeypatch.setattr(markitdown_shim, 'OPENAI_API_KEY', 'fake-test-key')
    monkeypatch.setattr(config, 'ANALYSIS_PROVIDER', 'openai')
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=completion))))
    monkeypatch.setitem(sys.modules, 'markitdown', SimpleNamespace(MarkItDown=MarkItDown, StreamInfo=lambda **_: None))
    # Restore the real conversion adapter; fake only the external SDK/converter.
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', _MARKITDOWN_CONVERT)
    report = {}
    text, *_ = pdf_loader.extract_text(scanned_page, quality_report=report)
    assert 'STR 60 DEX 55 Damage 1d10+DB' in text
    assert report['markitdown_transcription_agreements'] == 1
    assert report['blocked_pages'] == []


def test_unverified_candidates_survive_draft_checkpoint_with_operator_instructions(scanned_page, monkeypatch, tmp_path):
    from app import pdf_ingestion_drafts as drafts

    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
        pdf_loader.extract_text(scanned_page, quality_report=report)
    lease = drafts.reserve('group', scanned_page, 'scanned.pdf', owner_id='kp')
    drafts.checkpoint(lease, report, pending.value.result)
    draft = drafts.load('group')
    assert draft['pages']['1']['report']['image_transcription']['candidate'] == 'STR 60 DEX 55 Damage 1d10+DB'
    assert 'STR 60' not in draft['pages']['1']['selected_text']
    assert drafts.resume_pages(draft, report['extraction_identity']) == {}
    assert '影像分析或人工核對原稿' in drafts.progress(draft)
    assert '確認後，才能發布' in drafts.progress(draft)


@pytest.mark.parametrize('independent', ['Modifier -10', 'Cost 10'])
def test_independent_transcription_retains_signed_and_currency_mechanics(independent):
    candidate = 'Modifier +10' if independent.startswith('Modifier') else 'Cost $10'
    assert not pdf_quality.accept_independent_transcription(candidate, independent)


def test_safe_native_source_does_not_request_unnecessary_challengers(monkeypatch):
    import pymupdf

    from app import pdf_ocr

    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 60), 'Alice STR 60 DEX 55')
        raw = doc.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec', 'candidate': 'Alice STR 55 DEX 60', 'status': 'candidate'})
    monkeypatch.setattr(pdf_loader, '_ocr_image', lambda *_: '')
    report = {}
    text, *_ = pdf_loader.extract_text(raw, quality_report=report)
    assert 'Alice STR 60 DEX 55' in text
    assert report['blocked_pages'] == []
    assert report['local_paddle_attempts'] == 0


def test_verified_image_source_can_resume_only_with_the_same_extraction_identity(scanned_page, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app import config
    from app import pdf_ingestion_drafts as drafts
    from app.providers import registry

    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_kwargs: {'page_type': 'character_sheet', 'text': 'STR 60 DEX 55 Damage 1d10+DB'}))
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    report = {}
    result = pdf_loader.extract_text(scanned_page, quality_report=report)
    lease = drafts.reserve('group', scanned_page, 'scanned.pdf', owner_id='kp')
    drafts.checkpoint(lease, report, result)
    draft = drafts.load('group')
    cached = drafts.resume_pages(draft, report['extraction_identity'])
    assert 1 in cached
    assert cached[1]['report']['image_transcription']['status'] == 'authoritative'
    changed = dict(report['extraction_identity'], pipeline_version='multicolumn-v4')
    assert drafts.resume_pages(draft, changed) == {}
    old_quality = dict(report['extraction_identity'], quality_version='ai-import-repair-v7')
    assert drafts.resume_pages(draft, old_quality) == {}


def test_image_candidate_cannot_be_published_to_the_scenario_library(scanned_page, monkeypatch, tmp_path):
    from app import scenario_library

    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
        pdf_loader.extract_text(scanned_page, quality_report=report)
    with pytest.raises(ValueError, match='unresolved pages'):
        scenario_library.save_scenario(scanned_page, title='Scan', filename='scan.pdf', preview='',
            text=pending.value.result[0], indexes={}, pregens=[], page_maps={}, page_images={}, parse_quality=report)
    assert not list(tmp_path.iterdir())


def test_image_only_map_candidate_runs_scene_map_independently_of_ocr(scanned_page, monkeypatch, certified_map_result):
    from app import pdf_ocr

    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec',
        'candidate': 'Floor plan Room 1 Room 2', 'status': 'candidate'})
    graph = {'entry_room_id': 'room_1', 'rooms': [
        {'id': 'room_1', 'name': 'Room 1', 'exits': [{'to': 'room_2', 'compass': 'E'}]},
        {'id': 'room_2', 'name': 'Room 2', 'exits': [{'to': 'room_1', 'compass': 'W'}]}]}
    monkeypatch.setattr(pdf_loader, '_analyze_graphic_page', lambda png, **_options: certified_map_result('', graph, png))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
        pdf_loader.extract_text(scanned_page, quality_report=report)
    text, _, _, _, maps = pending.value.result
    assert report['hard_block_pages'] == [1]  # Graph certification does not certify image-only source text.
    assert maps[1] == graph
    assert 'Floor plan Room 1 Room 2' not in text
    assert 'floor_plan_graph_missing' not in report['pages'][0]['review_reasons']


def test_independent_verification_respects_durable_provider_budget(scanned_page, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app import config
    from app.providers import registry

    call = Mock(return_value={'page_type': 'character_sheet', 'text': 'STR 60 DEX 55 Damage 1d10+DB'})
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=call))
    monkeypatch.setattr(config, 'PDF_LAYOUT_MAX_REQUESTS', 0)
    monkeypatch.setattr(config, 'PDF_PAGE_CRITICALITY_MAX_REQUESTS', 0)
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(scanned_page, quality_report=report)
    assert not call.called
    assert report['pages'][0]['image_transcription']['status'] == 'unverified'
    assert report['layout_budget']['consumed_requests'] == 0


def test_empty_paddle_and_provider_no_text_classification_can_resolve_tesseract_noise(scanned_page, monkeypatch):
    from types import SimpleNamespace

    from app import config, pdf_ocr
    from app.providers import registry

    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec', 'candidate': '', 'status': 'empty'})
    monkeypatch.setattr(pdf_loader, '_ocr_image', lambda *_: '5 es 5 4 = ff J 1 fi 3')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_kwargs: {'page_type': 'illustration', 'text': ''}))
    report = {}
    import pymupdf
    with pymupdf.open(stream=scanned_page, filetype='pdf') as doc:
        doc.new_page().insert_text((40, 100), 'The Keeper describes the scene.')
        raw = doc.tobytes()
    text, review, *_ = pdf_loader.extract_text(raw, quality_report=report)
    assert report['blocked_pages'] == []
    assert review == []
    assert '5 es' not in text
    assert report['pages'][0]['verified_illustration'] is True
    assert report['pages'][0]['image_transcription']['status'] == 'unverified'


def test_native_header_does_not_hide_the_scanned_page_body(scanned_page, monkeypatch):
    from types import SimpleNamespace

    import pymupdf

    from app import config, pdf_ocr
    from app.providers import registry

    with pymupdf.open(stream=scanned_page, filetype='pdf') as doc:
        doc[0].insert_text((40, 60), 'Investigator')
        raw = doc.tobytes()
    candidate = 'Investigator STR 60 DEX 55 Damage 1d10+DB'
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec', 'candidate': candidate, 'status': 'candidate'})
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_kwargs: {'page_type': 'character_sheet', 'text': candidate}))
    report = {}
    text, *_ = pdf_loader.extract_text(raw, quality_report=report)
    assert candidate in text
    assert report['pages'][0]['image_transcription']['status'] == 'authoritative'


def test_markitdown_image_completions_obey_exhausted_durable_budget(scanned_page, monkeypatch):
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app import config, markitdown_shim

    completion = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content='STR 60 DEX 55 Damage 1d10+DB'), finish_reason='stop')]))
    class MarkItDown:
        def __init__(self, **options):
            self.client = options['llm_client']

        def convert(self, *_args, **_kwargs):
            response = self.client.chat.completions.create(messages=[{'content': [
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}}]}])
            return SimpleNamespace(text_content='## Page 1\n' + response.choices[0].message.content)

    monkeypatch.setattr(config, 'PDF_LAYOUT_MAX_REQUESTS', 0)
    monkeypatch.setattr(markitdown_shim, 'ANALYSIS_PROVIDER', 'openai')
    monkeypatch.setattr(markitdown_shim, 'OPENAI_API_KEY', 'fake-test-key')
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completion)))))
    monkeypatch.setitem(sys.modules, 'markitdown', SimpleNamespace(MarkItDown=MarkItDown, StreamInfo=lambda **_: None))
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', _MARKITDOWN_CONVERT)
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(scanned_page, quality_report=report)
    assert not completion.called
    assert report['layout_budget']['consumed_requests'] == 0
    assert report['pages'][0]['image_transcription']['status'] == 'unverified'


@pytest.mark.parametrize(('native', 'candidate'), [
    ('The damaged wall hides a silent room. Damage 1d10+DB',
     'The damaged wall hides a silent room. Damage 1d10 STR 60 DEX 55'),
    ('The door is locked.', 'The door is not locked. STR 60'),
    ('Damage 1d10', 'Damage -1d10 STR 60'),
    ('1d10', '-1d10 STR 60'),
    ('60', '-60 STR 60'),
])
def test_independent_agreement_cannot_change_an_intact_native_anchor(scanned_page, monkeypatch, native, candidate):
    from types import SimpleNamespace

    import pymupdf

    from app import config, pdf_ocr
    from app.providers import registry

    with pymupdf.open(stream=scanned_page, filetype='pdf') as doc:
        doc[0].insert_text((40, 60), native)
        raw = doc.tobytes()
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec', 'candidate': candidate, 'status': 'candidate'})
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_kwargs: {'page_type': 'text', 'text': candidate}))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
        pdf_loader.extract_text(raw, quality_report=report)
    assert native in report['pages'][0]['quarantine_evidence']['selected_text']
    assert candidate not in pending.value.result[0]
    assert report['pages'][0]['image_transcription']['status'] == 'unverified'
