"""Optional local OCR must improve candidates without replacing the fallback contract."""
import io
import sys
import types

import pytest
from PIL import Image


def test_paddle_valid_candidate_uses_explicit_cpu_models(monkeypatch, tmp_path):
    from app import pdf_ocr
    for model in ('PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec'):
        directory = tmp_path / model
        directory.mkdir()
        for name in ('inference.json', 'inference.pdiparams', 'inference.yml'):
            (directory / name).write_text('synthetic model fixture')
    monkeypatch.setenv('PDF_PADDLE_MODEL_DIR', str(tmp_path))
    calls = []
    def engine(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(predict=lambda _image: [{'rec_texts': ['Damage 1d6+2', 'SAN 1/1d6']}])
    monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(PaddleOCR=engine))
    image = io.BytesIO()
    Image.new('RGB', (100, 60), 'white').save(image, format='PNG')
    result = pdf_ocr.recognize_with_paddle(image.getvalue())
    assert result.status == 'accepted' and result.engine == 'paddleocr'
    assert result.text == 'Damage 1d6+2\nSAN 1/1d6'
    assert len(calls) == 1 and calls[0]['device'] == 'cpu'
    assert calls[0]['text_recognition_model_name'] == 'PP-OCRv5_mobile_rec'
    assert calls[0]['text_recognition_model_dir'] == str(tmp_path / 'PP-OCRv5_mobile_rec')
    assert calls[0]['text_detection_model_dir'] == str(tmp_path / 'PP-OCRv5_mobile_det')
    assert calls[0]['use_doc_orientation_classify'] is False
    assert calls[0]['use_doc_unwarping'] is False
    assert calls[0]['use_textline_orientation'] is False


@pytest.fixture
def paddle_backend(monkeypatch, tmp_path):
    from app import pdf_ocr
    for model in pdf_ocr.MODELS:
        directory = tmp_path / model
        directory.mkdir()
        for name in pdf_ocr.MODEL_FILES:
            (directory / name).write_text('synthetic model fixture')
    monkeypatch.setenv('PDF_PADDLE_MODEL_DIR', str(tmp_path))
    state = types.SimpleNamespace(output=[{'rec_texts': ['Damage 1d6+2']}], calls=0)
    def build(**kwargs):
        state.calls += 1
        return types.SimpleNamespace(predict=lambda _image: state.output)
    monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(PaddleOCR=build))
    image = io.BytesIO()
    Image.new('RGB', (100, 60), 'white').save(image, format='PNG')
    return pdf_ocr, state, image.getvalue()


@pytest.mark.parametrize(('source', 'candidate'), [
    ('Damage 1d6+2', 'Damage 1d4+2'),
    ('Damage 1d6+2', 'Damage'),
    ('Spot Hidden 50%', 'Spot Hidden 50'),
    ('Damage +2', 'Damage -2'),
    ('SAN 1/1d6', 'SAN 1'),
])
def test_paddle_does_not_replace_intact_mechanics(paddle_backend, source, candidate):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': [candidate]}]
    result = module.recognize_with_paddle(image, source_text=source)
    assert result.status == 'rejected' and result.text == ''


def test_paddle_preserves_complete_san_loss_expression(paddle_backend):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': ['SAN 1 and 1d6']}]
    result = module.recognize_with_paddle(image, source_text='SAN 1/1d6')
    assert result.status == 'rejected' and result.text == ''


@pytest.mark.parametrize(('source', 'candidate', 'expected'), [
    ('SAN 1/1d6', 'SAN 1/1d6', 'accepted'),
    ('SAN 1/1d6', 'SAN 1 / 1d6', 'accepted'),
    ('SAN 1/1d6', 'SAN 1/1D6', 'accepted'),
    ('SAN 1/1d6', 'SAN 1d6/1', 'rejected'),
    ('SAN 1/1d6', 'SAN 1 1d6', 'rejected'),
    ('SAN 0/1d4', 'SAN 0/1d4', 'accepted'),
    ('SAN 1d3/1d10', 'SAN 1d3/1d10', 'accepted'),
    ('SAN 1d6/1d20', 'SAN 1d6/1d20', 'accepted'),
    ('Damage 2d6', 'Damage 2d6', 'accepted'),
])
def test_paddle_preserves_san_pair_order_and_other_dice(paddle_backend, source, candidate, expected):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': [candidate]}]
    result = module.recognize_with_paddle(image, source_text=source)
    assert result.status == expected


@pytest.mark.parametrize(('source', 'candidate', 'corrected'), [
    ('SAN 1/1d6', 'SAN l/ld6', 'SAN 1/1d6'),
    ('SAN 1/1d6', 'SAN I/Id6', 'SAN 1/1d6'),
    ('Damage 1d6', 'Damage ld6', 'Damage 1d6'),
    ('Damage 1d10', 'Damage Id10', 'Damage 1d10'),
    ('Damage 1d20', 'Damage Id20', 'Damage 1d20'),
])
def test_paddle_repairs_source_bound_one_as_letter_in_dice(paddle_backend, source, candidate, corrected):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': [candidate]}]
    result = module.recognize_with_paddle(image, source_text=source)
    assert result.status == 'accepted' and result.text == corrected


@pytest.mark.parametrize(('source', 'candidate'), [
    ('SAN 1/1d6', 'SAN l/ld8'),
    ('Damage 1d6', 'Damage ld8'),
    ('', 'Damage ld6'),
    ('SAN 1/1d6', 'SAN |/|d6'),
])
def test_paddle_does_not_guess_unknown_or_different_dice(paddle_backend, source, candidate):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': [candidate]}]
    assert module.recognize_with_paddle(image, source_text=source).status == 'rejected'


@pytest.mark.parametrize('formula', [
    '1d4', '1d6', '1d8', '1d10', '1d20', '1d100', '2d6', '3d10', '1d6+2', '1d4-1',
])
def test_paddle_keeps_other_valid_dice_unchanged(paddle_backend, formula):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': [f'Damage {formula}']}]
    result = module.recognize_with_paddle(image, source_text=f'Damage {formula}')
    assert result.status == 'accepted' and result.text == f'Damage {formula}'


def test_paddle_does_not_change_prose_letters(paddle_backend):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': ['Idea library Index. Damage ld6']}]
    result = module.recognize_with_paddle(image, source_text='Idea library Index. Damage 1d6')
    assert result.status == 'accepted' and result.text == 'Idea library Index. Damage 1d6'


def test_paddle_swapped_stat_pairs_are_rejected(paddle_backend):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': ['STR 70 DEX 50']}]
    pairs = [{'label': 'STR', 'value': '50', 'block': 0, 'status': 'same_row_candidate'},
             {'label': 'DEX', 'value': '70', 'block': 0, 'status': 'same_row_candidate'}]
    result = module.recognize_with_paddle(image, source_text='STR 50 DEX 70', pairs=pairs)
    assert result.status == 'rejected' and result.text == ''


@pytest.mark.parametrize('candidate', ['Damage �d6+2', 'Damage ld6+2', 'Damage 1d�+2'])
def test_paddle_observable_corruption_rejected(paddle_backend, candidate):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': [candidate]}]
    assert module.recognize_with_paddle(image).status == 'rejected'


def test_image_only_pdf_uses_paddle_in_existing_local_ocr_path(paddle_backend, monkeypatch):
    import pymupdf

    from app import pdf_loader
    _module, backend, image = paddle_backend
    backend.output = [{'rec_texts': ['A scanned source page. Damage 1d6+2.']}]
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, stream=image)
        payload = document.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda _: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *args: None)
    monkeypatch.setattr(pdf_loader, 'analyze_page_image', lambda _: ('', None))
    text, _, _, _, _ = pdf_loader.extract_text(payload, ai_repair_limit=0)
    assert 'A scanned source page. Damage 1d6+2.' in text
    assert backend.calls == 1


@pytest.mark.parametrize('failure', ['disabled', 'missing_model', 'missing_package', 'init', 'inference', 'empty', 'malformed'])
def test_paddle_failure_preserves_existing_tesseract_import(paddle_backend, monkeypatch, failure):
    import pymupdf

    from app import pdf_loader
    module, backend, image = paddle_backend
    if failure == 'disabled':
        monkeypatch.setenv('PDF_PADDLE_OCR_ENABLED', 'false')
    elif failure == 'missing_model':
        (module.model_directory() / module.MODELS[0] / module.MODEL_FILES[0]).unlink()
    elif failure == 'missing_package':
        monkeypatch.setitem(sys.modules, 'paddleocr', None)
    elif failure in {'init', 'inference'}:
        def throw(*args, **kwargs):
            raise RuntimeError('private OCR data must not enter logs')
        if failure == 'init':
            monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(PaddleOCR=throw))
        else:
            monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(
                PaddleOCR=lambda **kwargs: types.SimpleNamespace(predict=throw)))
    elif failure == 'empty':
        backend.output = [{'rec_texts': []}]
    else:
        backend.output = [{'rec_texts': 'invalid scalar output'}]
    monkeypatch.setitem(sys.modules, 'pytesseract', types.SimpleNamespace(
        image_to_string=lambda *args, **kwargs: 'Existing Tesseract source: Damage 1d6+2.'))
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, stream=image)
        payload = document.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda _: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *args: None)
    monkeypatch.setattr(pdf_loader, 'analyze_page_image', lambda _: ('', None))
    text, _, _, _, _ = pdf_loader.extract_text(payload, ai_repair_limit=0)
    assert 'Existing Tesseract source: Damage 1d6+2.' in text


def test_native_pdf_unchanged_and_does_not_initialize_paddle(paddle_backend, monkeypatch):
    import pymupdf

    from app import pdf_loader
    _module, backend, _image = paddle_backend
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(page.rect + (30, 30, -30, -30), 'A normal narrative with 1d6 damage. ' * 15)
        payload = document.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda _: None)
    before = pdf_loader.extract_text(payload, ai_repair_limit=0)
    monkeypatch.setenv('PDF_PADDLE_OCR_ENABLED', 'false')
    after = pdf_loader.extract_text(payload, ai_repair_limit=0)
    assert before == after and backend.calls == 0


def test_startup_does_not_import_paddle(paddle_backend):
    import os
    import subprocess
    from pathlib import Path

    import app
    script = 'import app.pdf_loader, sys; assert "paddleocr" not in sys.modules; assert "paddle" not in sys.modules'
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(app.__file__).parent.parent,
                            env={**os.environ, 'PYTHON_DOTENV_DISABLED': '1'}, capture_output=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr.decode()


def test_paddle_failure_log_does_not_expose_image_source_or_raw_error(paddle_backend, monkeypatch, caplog):
    module, _backend, image = paddle_backend
    def failure(**kwargs):
        raise RuntimeError('sensitive-source-and-key')
    monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(PaddleOCR=failure))
    caplog.set_level('DEBUG', logger='app.pdf_ocr')
    assert module.recognize_with_paddle(image).status == 'error'
    assert 'RuntimeError' in caplog.text and 'sensitive-source-and-key' not in caplog.text


def test_explicit_setup_replays_prepared_models_without_network(paddle_backend, monkeypatch, capsys):
    import runpy
    import urllib.request
    from pathlib import Path

    module, _backend, _image = paddle_backend
    def denied(*args, **kwargs):
        raise AssertionError('Prepared setup must not download again')
    monkeypatch.setattr(urllib.request, 'urlopen', denied)
    monkeypatch.setattr(sys, 'argv', ['setup_paddle_ocr', '--model-dir', str(module.model_directory())])
    runpy.run_path(str(Path(__file__).parents[1] / 'scripts/setup_paddle_ocr.py'), run_name='__main__')
    assert capsys.readouterr().out.count('already prepared') == 2


def test_lazy_engine_reused_and_inference_serialized(paddle_backend, monkeypatch):
    import concurrent.futures
    import threading
    import time

    module, _backend, image = paddle_backend
    guard = threading.Lock()
    active = 0
    maximum = 0
    builds = []
    def predict(_image):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(.01)
        with guard:
            active -= 1
        return [{'rec_texts': ['A scanned source.']}]
    def build(**kwargs):
        builds.append(kwargs)
        return types.SimpleNamespace(predict=predict)
    monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(PaddleOCR=build))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(module.recognize_with_paddle, [image] * 4))
    assert all(result.status == 'accepted' for result in results)
    assert len(builds) == 1 and maximum == 1


def test_region_paddle_rejection_still_allows_legacy_safe_repair(paddle_backend, monkeypatch):
    import pymupdf

    from app import pdf_loader, pdf_quality
    _module, backend, _image = paddle_backend
    backend.output = [{'rec_texts': ['Damage 1d8']}]
    monkeypatch.setitem(sys.modules, 'pytesseract', types.SimpleNamespace(
        image_to_string=lambda *args, **kwargs: 'Damage 1d6'))
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), 'Damage 1d6')
        evidence = pdf_quality.block_evidence(page)
        evidence['blocks'][0]['lines'][0]['text'] = 'Dam�age 1d6'
        text, attempts = pdf_loader._repair_local_regions(page, evidence, [], 'Dam�age 1d6', [1])
    assert text == 'Damage 1d6' and attempts[0]['status'] == 'accepted'


def test_local_paddle_does_not_call_external_analysis_provider(paddle_backend, monkeypatch):
    from app.providers import registry
    module, _backend, image = paddle_backend
    calls = []
    def denied(*args, **kwargs):
        calls.append(1)
        raise AssertionError('Local OCR cannot dispatch to a provider')
    for provider in registry.ANALYSIS_PROVIDERS.values():
        monkeypatch.setattr(provider, 'analyze_image', denied)
    assert module.recognize_with_paddle(image).status == 'accepted'
    assert calls == []


def test_whole_page_paddle_preserves_existing_native_dice(paddle_backend, monkeypatch):
    import pymupdf

    from app import pdf_loader
    _module, backend, image = paddle_backend
    backend.output = [{'rec_texts': ['Damage 2d6+2']}]
    monkeypatch.setitem(sys.modules, 'pytesseract', types.SimpleNamespace(
        image_to_string=lambda *args, **kwargs: 'Damage 1d6+2'))
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, stream=image)
        page.insert_text((40, 60), 'Damage 1d6+2')
        payload = document.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda _: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *args: None)
    monkeypatch.setattr(pdf_loader, 'analyze_page_image', lambda _: ('', None))
    text, _, _, _, _ = pdf_loader.extract_text(payload, ai_repair_limit=0)
    assert 'Damage 2d6+2' not in text and 'Damage 1d6+2' in text


def test_rejected_san_candidate_uses_existing_tesseract_fallback(paddle_backend, monkeypatch):
    import pymupdf

    from app import pdf_loader
    _module, backend, image = paddle_backend
    backend.output = [{'rec_texts': ['SAN 1 and 1d6']}]
    tesseract_calls = []
    def tesseract(*args, **kwargs):
        tesseract_calls.append(1)
        return 'SAN 1/1d6'
    monkeypatch.setitem(sys.modules, 'pytesseract', types.SimpleNamespace(image_to_string=tesseract))
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, stream=image)
        page.insert_text((40, 60), 'SAN 1/1d6')
        payload = document.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda _: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *args: None)
    monkeypatch.setattr(pdf_loader, 'analyze_page_image', lambda _: ('', None))
    text, _, _, _, _ = pdf_loader.extract_text(payload, ai_repair_limit=0)
    assert tesseract_calls == [1] and 'SAN 1/1d6' in text


@pytest.mark.parametrize(('source', 'candidate', 'fallback'), [
    ('Dam�age 1d6', 'Damage 1d6. Bonus 1d8', 'Damage 1d6'),
    ('Dam�age 1d6+DB', 'Damage 1d6-DB', 'Damage 1d6+DB'),
])
def test_unsafe_paddle_cannot_preempt_working_region_fallback(paddle_backend, monkeypatch, source, candidate, fallback):
    import pymupdf

    from app import pdf_loader, pdf_quality
    _module, backend, _image = paddle_backend
    backend.output = [{'rec_texts': [candidate]}]
    monkeypatch.setitem(sys.modules, 'pytesseract', types.SimpleNamespace(
        image_to_string=lambda *args, **kwargs: fallback))
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), fallback)
        evidence = pdf_quality.block_evidence(page)
        evidence['blocks'][0]['lines'][0]['text'] = source
        text, attempts = pdf_loader._repair_local_regions(page, evidence, [], source, [1])
    assert text == fallback and attempts[0]['status'] == 'accepted'


def test_nontext_paddle_noise_rejected(paddle_backend):
    module, backend, image = paddle_backend
    backend.output = [{'rec_texts': ['... ??? ---']}]
    assert module.recognize_with_paddle(image).status == 'rejected'
