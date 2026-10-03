"""Import-only OCR preserves source mechanics independently of engine confidence."""
import pytest

from app import pdf_quality


@pytest.mark.parametrize(('source', 'candidate'), [
    ('Dam\ufffdage 1d10+DB', 'Damage 1d10+2'),
    ('Dam\ufffdage 1d6+2', 'Damage ld6+2'),
    ('Dam\ufffdage 2d6', 'Damage 2d6+1'),
    ('Dam\ufffdage 1d4', 'Damage 1d4 plus 12'),
    ('Dam\ufffdage Spot Hidden 50%', 'Damage Spot Hidden 50'),
])
def test_local_candidate_cannot_change_complete_mechanics(source, candidate):
    assert not pdf_quality.accept_region(source, candidate, [])


def test_local_repair_cannot_rewrite_intact_prose():
    source = 'The investigator suffers Dam\ufffdage 2d6 after failure.'
    assert pdf_quality.accept_region(source, 'The investigator suffers Damage 2d6 after failure.', [])
    assert not pdf_quality.accept_region(source, 'The investigator takes Damage 2d6.', [])


def test_missing_paddle_artifacts_still_offer_tesseract(monkeypatch, tmp_path):
    from app import config, pdf_ocr
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_ENABLED', True)
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_MODELS_PATH', str(tmp_path / 'absent'))
    results = list(pdf_ocr.candidates(b'png', tesseract=lambda _: 'Damage 2d6'))
    assert [(r['engine'], r['status']) for r in results] == [('paddleocr', 'unavailable'), ('tesseract', 'candidate')]
    assert results[-1]['candidate'] == 'Damage 2d6'


def test_rejected_paddle_candidate_does_not_prevent_validated_tesseract_recovery():
    from unittest.mock import patch

    from app import pdf_loader, pdf_ocr

    pairs = [{'label': 'STR', 'value': '50', 'status': 'same_row_candidate', 'block': 0},
             {'label': 'DEX', 'value': '70', 'status': 'same_row_candidate', 'block': 0}]
    with patch.object(pdf_ocr, 'paddle_candidate', return_value={
        'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec',
        'candidate': 'Damage STR 70 DEX 50 2d6', 'confidence': [.999], 'status': 'candidate'}), \
         patch.object(pdf_loader, '_ocr_image', return_value='Damage STR 50 DEX 70 2d6'):
        text, attempts = pdf_loader.recover_local_ocr(b'png', 'Dam\ufffdage STR 50 DEX 70 2d6', pairs)
    assert text == 'Damage STR 50 DEX 70 2d6'
    assert [(a['engine'], a['status']) for a in attempts] == [('paddleocr', 'rejected'), ('tesseract', 'accepted')]


def test_mixed_language_fixture_preserves_values_skills_and_dice():
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/pdf_ocr/mixed_language.json').read_text())
    pairs = [{'block': 0, 'label': label, 'value': value, 'status': 'same_row_candidate'}
             for label, value in [('STR', '60'), ('DEX', '55'), ('SAN', '40'), ('幸運', '45')]]
    pairs.extend({'block': 0, 'label': label, 'value': value, 'status': 'skill_candidate'}
                 for label, value in [('SPOT HIDDEN', '50%'), ('偵查', '55%')])
    assert pdf_quality.accept_region(fixture['source'], fixture['candidate'], pairs)
    assert not pdf_quality.accept_region(fixture['source'], fixture['candidate'].replace('偵查 55%', '偵查 50%'), pairs)
    assert not pdf_quality.accept_region(fixture['source'], fixture['candidate'].replace('調查員', ''), pairs)


def test_unsupported_hp_completion_is_not_source_authority():
    assert not pdf_quality.accept_transcription('HP', 'HP 12', [])
    assert not pdf_quality.accept_region('H\ufffdP', 'HP 12', [])
    assert not pdf_quality.accept_transcription('', 'HP 12', [])


@pytest.mark.parametrize('failure', ['ImportError', 'InitializationError', 'InferenceError', 'TimeoutExpired'])
def test_paddle_runtime_errors_do_not_prevent_tesseract(monkeypatch, tmp_path, failure):
    import hashlib
    import json
    import subprocess

    from app import config, pdf_ocr

    models = {}
    for name in ('PP-OCRv5_server_det', 'PP-OCRv5_mobile_rec'):
        directory = tmp_path / name
        directory.mkdir()
        files = {}
        for filename in ('inference.json', 'inference.yml', 'inference.pdiparams'):
            (directory / filename).write_bytes(b'fixture')
            files[filename] = hashlib.sha256(b'fixture').hexdigest()
        models[name] = {'directory': name, 'files': files}
    (tmp_path / 'manifest.json').write_text(json.dumps({'models': models}))
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_MODELS_PATH', str(tmp_path))
    def external_process(*args, **kwargs):
        if failure == 'TimeoutExpired':
            raise subprocess.TimeoutExpired(args[0], .1)
        return subprocess.CompletedProcess(args[0], 0, 'OCR_RESULT:' + json.dumps({'error': failure}), '')
    monkeypatch.setattr(subprocess, 'run', external_process)
    attempts = list(pdf_ocr.candidates(b'png', tesseract=lambda _: 'STR 50'))
    assert attempts[0]['candidate'] == ''
    assert attempts[0]['status'] in {'error', 'unavailable'}
    assert attempts[1]['candidate'] == 'STR 50'


def test_corrupt_model_falls_back_without_starting_worker(monkeypatch, tmp_path):
    import json

    from app import config, pdf_ocr
    (tmp_path / 'manifest.json').write_text(json.dumps({'models': {'PP-OCRv5_server_det': {
        'directory': '.', 'files': {'inference.json': 'wrong', 'inference.yml': 'wrong', 'inference.pdiparams': 'wrong'}}}}))
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_MODELS_PATH', str(tmp_path))
    attempts = list(pdf_ocr.candidates(b'png', tesseract=lambda _: 'STR 50'))
    assert attempts[0]['status'] == 'unavailable'
    assert attempts[-1]['candidate'] == 'STR 50'


def test_local_confidence_does_not_accept_hallucinated_or_dice_corruption():
    from unittest.mock import patch

    from app import pdf_loader, pdf_ocr
    for candidate in ('Damage ld6+2', 'Damage 1d6+2 HP 12'):
        with patch.object(pdf_ocr, 'paddle_candidate', return_value={
            'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec', 'candidate': candidate,
            'confidence': [1.0], 'status': 'candidate'}), patch.object(pdf_loader, '_ocr_image', return_value=''):
            text, attempts = pdf_loader.recover_local_ocr(b'png', 'Dam\ufffdage 1d6+2', [])
        assert text == ''
        assert attempts[0]['status'] == 'rejected'
        assert attempts[-1]['status'] == 'empty'


def test_local_repair_preserves_intact_word_order_and_does_not_add_sentences():
    source = 'The investigator suffers Dam\ufffdage 2d6 after failure.'
    assert not pdf_quality.accept_region(source, 'After failure the investigator suffers Damage 2d6.', [])
    assert not pdf_quality.accept_region(source, 'The investigator suffers Damage 2d6 after failure. It is fatal.', [])


def test_changed_ocr_identity_invalidates_accepted_cache_but_same_identity_resumes():
    import hashlib
    from unittest.mock import patch

    import pymupdf

    from app import pdf_loader
    document = pymupdf.open()
    document.new_page().insert_text((40, 60), 'Verified native source.')
    raw = document.tobytes()
    document.close()
    identity = pdf_loader.extraction_identity()
    report = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None):
        pdf_loader.extract_text(raw, quality_report=report)
    row = report['pages'][0]
    entry = {'pdf_sha256': hashlib.sha256(raw).hexdigest(), 'pipeline_version': pdf_loader.PIPELINE_VERSION,
             'renderer_version': pdf_loader.RENDERER_VERSION, 'extraction_identity': identity,
             'selected_text': row['selected_text'], 'selected_sha256': row['selected_sha256'], 'report': row}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None):
        same = {}
        pdf_loader.extract_text(raw, resume_pages={1: entry}, quality_report=same)
    assert same['pages'][0]['resumed']
    old_identity = dict(identity)
    old_identity.pop('ocr')
    entry['extraction_identity'] = old_identity
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None):
        changed = {}
        pdf_loader.extract_text(raw, resume_pages={1: entry}, quality_report=changed)
    assert not changed['pages'][0].get('resumed')


def test_model_configuration_participates_in_extraction_identity(monkeypatch):
    from app import config, pdf_loader
    original = pdf_loader.extraction_identity()
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_MODEL', 'PP-OCRv5_server_rec')
    changed = pdf_loader.extraction_identity()
    assert changed['ocr']['model'] == 'PP-OCRv5_server_rec'
    assert changed != original
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_ENABLED', False)
    assert pdf_loader.extraction_identity()['ocr']['enabled'] is False


def test_map_ocr_success_retains_native_text_and_independent_certified_graph(certified_map_result):
    from unittest.mock import patch

    import pymupdf

    from app import pdf_loader, pdf_ocr
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), 'Floor plan Room 1 Room 2')
    for number in range(10):
        page.draw_line((40, 100 + number * 10), (250, 100 + number * 10))
    raw = doc.tobytes()
    doc.close()
    graph = {'rooms': [{'id': '1', 'name': 'Room 1'}, {'id': '2', 'name': 'Room 2'}], 'entry_room_id': '1'}
    report = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(pdf_loader, '_markitdown_page_texts', return_value=None) as transcription, \
         patch.object(pdf_ocr, 'paddle_candidate', return_value={'engine': 'paddleocr',
             'model': 'PP-OCRv5_mobile_rec', 'candidate': 'Floor plan Room 1 Room 2', 'status': 'candidate'}), \
         patch.object(pdf_loader, '_analyze_graphic_page', side_effect=lambda png, **_options: certified_map_result('', graph, png)):
        text, _, _, _, maps = pdf_loader.extract_text(raw, quality_report=report)
    assert maps[1] == graph
    transcription.assert_not_called()
    assert 'Room 1 Room 2' in text
    assert report['pages'][0]['page_ocr_attempts'][0]['status'] == 'accepted'
    assert report['local_paddle_accepted'] == 1
    assert report['layout_budget']['remaining_requests'] == report['layout_budget']['configured_max_requests']
    assert report['ai_repair_requests'] == 0


def test_both_local_engines_fail_but_markitdown_route_is_preserved():
    from unittest.mock import patch

    import pymupdf

    from app import pdf_loader, pdf_ocr
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), 'Alice STR')
    for number in range(10):
        page.draw_line((40, 100 + number * 10), (250, 100 + number * 10))
    raw = doc.tobytes()
    doc.close()
    report = {}
    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
         patch.object(pdf_ocr, 'paddle_candidate', return_value={'engine': 'paddleocr',
             'model': 'PP-OCRv5_mobile_rec', 'candidate': '', 'status': 'error'}), \
         patch.object(pdf_loader, '_ocr_image', return_value=''), \
         patch.object(pdf_loader, '_markitdown_page_texts', return_value={1: 'Alice STR'}) as fallback, \
         patch.object(pdf_loader, '_analyze_graphic_page', return_value=('', None)):
        with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
            pdf_loader.extract_text(raw, quality_report=report)
        text = pending.value.result[0]
    assert 'Alice STR' in text
    assert report['pages'][0]['candidates']['markitdown'] == 'Alice STR'
    assert report['pages'][0]['method'] == 'native'
    assert fallback.call_args.args[1] == [1]


def test_selected_source_already_recovered_is_not_an_unresolved_ocr_failure():
    source = 'Dam\ufffdage 2d6 after failure.'
    selected = 'An intact header.\nDamage 2d6 after failure.\nAn intact footer.'
    assert pdf_quality.recovered_region(source, selected, []) == 'Damage 2d6 after failure.'
    assert pdf_quality.recovered_region(source, selected.replace('2d6', '2d6+2'), []) is None


@pytest.mark.parametrize('malformed', [{'models': {'PP-OCRv5_server_det': {'directory': '.', 'files': []}}},
                                     {'models': []}, [], {'models': {'PP-OCRv5_server_det': []}}])
def test_malformed_model_manifest_cannot_crash_import(monkeypatch, tmp_path, malformed):
    import json

    from app import config, pdf_ocr
    (tmp_path / 'manifest.json').write_text(json.dumps(malformed))
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_MODELS_PATH', str(tmp_path))
    attempts = list(pdf_ocr.candidates(b'png', tesseract=lambda _: 'STR 50'))
    assert attempts[0]['status'] == 'unavailable'
    assert attempts[-1]['candidate'] == 'STR 50'
    assert pdf_ocr.identity()['model_state'] == 'unavailable_or_corrupt'


def test_layout_extraction_does_not_bypass_primary_ocr_with_hidden_ocr():
    import sys
    import types
    from unittest.mock import patch

    import pymupdf

    from app import pdf_loader
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), 'Native text is already intact.')
    raw = doc.tobytes()
    doc.close()
    options = {}
    def to_markdown(_document, **kwargs):
        options.update(kwargs)
        return []
    with patch.dict(sys.modules, {'pymupdf4llm': types.SimpleNamespace(to_markdown=to_markdown)}):
        text, *_ = pdf_loader.extract_text(raw)
    assert 'Native text is already intact.' in text
    assert options.get('use_ocr') is False
    assert options.get('force_ocr') is False
