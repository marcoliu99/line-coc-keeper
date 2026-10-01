"""Bounded, offline local OCR candidates. Publication belongs to pdf_quality.

The standalone worker deliberately imports no bot configuration or providers:
its explicitly configured interpreter may use a different compatible Python.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from importlib import metadata
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

VERSION = 'local-ocr-v1'
DETECTOR = 'PP-OCRv5_server_det'
PACKAGES = ('paddleocr', 'paddlepaddle', 'paddlex')
_logger = logging.getLogger(__name__)


class OcrAttempt(TypedDict):
    engine: Literal['paddleocr', 'tesseract']
    model: str
    candidate: str
    status: Literal['candidate', 'accepted', 'rejected', 'unavailable', 'error', 'empty']
    reason: NotRequired[str]
    elapsed_ms: NotRequired[float]
    confidence: NotRequired[list[float]]
    pair_checks: NotRequired[list[dict]]


def _worker_call(payload: dict, timeout: float) -> dict:
    from app import config

    env = dict(os.environ, PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True', HF_HUB_OFFLINE='1',
               PADDLE_PDX_MODEL_SOURCE='BOS', CUDA_VISIBLE_DEVICES='')
    result = subprocess.run([config.PDF_OCR_PADDLE_PYTHON, str(Path(__file__).resolve())],
                            input=json.dumps(payload), text=True, capture_output=True,
                            timeout=timeout, check=True, env=env)
    # Paddle initialization writes progress to stdout; only our final prefixed line is transport.
    records = [line.removeprefix('OCR_RESULT:') for line in result.stdout.splitlines()
               if line.startswith('OCR_RESULT:')]
    if len(records) != 1:
        raise ValueError('invalid OCR worker response')
    response = json.loads(records[0])
    if not isinstance(response, dict):
        raise TypeError('invalid OCR worker response')
    return response


def _artifacts(root: Path, model: str) -> tuple[dict[str, str], str]:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', model):
        raise ValueError('invalid model name')
    raw = (root / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    paths = {}
    for name in (DETECTOR, model):
        entry = manifest['models'][name]
        directory = (root / entry['directory']).resolve()
        if not directory.is_relative_to(root.resolve()):
            raise ValueError('model outside configured cache')
        hashes = entry['files']
        if not isinstance(hashes, dict):
            raise TypeError('invalid model manifest')
        if not {'inference.json', 'inference.pdiparams', 'inference.yml'} <= hashes.keys():
            raise ValueError('incomplete model manifest')
        for relative, digest in hashes.items():
            file = (directory / relative).resolve()
            if (not file.is_relative_to(directory)
                    or hashlib.sha256(file.read_bytes()).hexdigest() != digest):
                raise ValueError('corrupt model artifact')
        paths[name] = str(directory)
    return paths, hashlib.sha256(raw).hexdigest()


def identity() -> dict:
    """Actual worker executable/package/model identity; never initializes Paddle."""
    from app import config

    result: dict = {'primary': 'paddleocr' if config.PDF_OCR_PADDLE_ENABLED else 'tesseract',
                    'enabled': config.PDF_OCR_PADDLE_ENABLED, 'model': config.PDF_OCR_PADDLE_MODEL,
                    'device': config.PDF_OCR_PADDLE_DEVICE, 'adapter_version': VERSION,
                    'detector': DETECTOR, 'detector_max_side': 960, 'cpu_threads': 4,
                    'model_state': 'disabled', 'model_digest': '',
                    'worker_python': config.PDF_OCR_PADDLE_PYTHON,
                    'timeout_seconds': config.PDF_OCR_PADDLE_TIMEOUT_SECONDS}
    versions = {package: 'disabled' for package in PACKAGES}
    if config.PDF_OCR_PADDLE_ENABLED:
        try:
            _, result['model_digest'] = _artifacts(Path(config.PDF_OCR_PADDLE_MODELS_PATH), config.PDF_OCR_PADDLE_MODEL)
            result['model_state'] = 'ready'
        except (OSError, ValueError, KeyError, TypeError):
            result['model_state'] = 'unavailable_or_corrupt'
        try:
            versions = _worker_call({'operation': 'versions'}, min(10, config.PDF_OCR_PADDLE_TIMEOUT_SECONDS))['versions']
            if not isinstance(versions, dict):
                raise TypeError('invalid worker versions')
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            versions = {package: 'unavailable' for package in PACKAGES}
    result.update({f'{package}_version': versions.get(package, 'unavailable') for package in PACKAGES})
    try:
        result['pytesseract_version'] = metadata.version('pytesseract')
    except metadata.PackageNotFoundError:
        result['pytesseract_version'] = 'unavailable'
    executable = shutil.which('tesseract')
    try:
        result['tesseract_version'] = subprocess.run([executable, '--version'], capture_output=True,
            text=True, timeout=5, check=True).stdout.splitlines()[0] if executable else 'unavailable'
    except (OSError, IndexError, subprocess.SubprocessError):
        result['tesseract_version'] = 'unavailable'
    return result


def paddle_candidate(png_bytes: bytes) -> OcrAttempt:
    from app import config

    attempt: OcrAttempt = {'engine': 'paddleocr', 'model': config.PDF_OCR_PADDLE_MODEL,
                           'candidate': '', 'status': 'unavailable'}
    started = time.perf_counter()
    try:
        if config.PDF_OCR_PADDLE_DEVICE != 'cpu':
            raise ValueError('only CPU OCR is supported')
        paths, _ = _artifacts(Path(config.PDF_OCR_PADDLE_MODELS_PATH), config.PDF_OCR_PADDLE_MODEL)
        result = _worker_call({'operation': 'predict', 'models': paths, 'model': config.PDF_OCR_PADDLE_MODEL,
                               'png': base64.b64encode(png_bytes).decode('ascii')},
                              config.PDF_OCR_PADDLE_TIMEOUT_SECONDS)
        if result.get('error'):
            attempt['status'], attempt['reason'] = 'error', result['error']
        elif isinstance(result.get('text'), str):
            attempt['candidate'] = result['text']
            attempt['confidence'] = result.get('confidence', [])
            attempt['status'] = 'candidate' if result['text'].strip() else 'empty'
        else:
            attempt['status'], attempt['reason'] = 'error', 'invalid_worker_output'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        attempt['reason'] = type(error).__name__
    attempt['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
    return attempt


def candidates(png_bytes: bytes, *, tesseract: Callable[[bytes], str] | None = None) -> Iterator[OcrAttempt]:
    """Lazy attempts: callers validate each candidate and stop only after acceptance."""
    from app import config

    if config.PDF_OCR_PADDLE_ENABLED:
        yield paddle_candidate(png_bytes)
    started = time.perf_counter()
    attempt: OcrAttempt = {'engine': 'tesseract', 'model': 'chi_tra+eng;eng', 'candidate': '', 'status': 'empty'}
    try:
        attempt['candidate'] = (tesseract or tesseract_text)(png_bytes)
        attempt['status'] = 'candidate' if attempt['candidate'].strip() else 'empty'
    except Exception as error:  # noqa: BLE001 - optional local OCR cannot discard the PDF.
        attempt['status'], attempt['reason'] = 'error', type(error).__name__
    attempt['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
    yield attempt


def tesseract_text(png_bytes: bytes) -> str:
    """Retain production language order, pytesseract and CLI fallback."""
    languages = ('chi_tra+eng', 'eng')
    try:
        import pytesseract
        from PIL import Image
        with Image.open(io.BytesIO(png_bytes)) as image:
            for lang in languages:
                text = pytesseract.image_to_string(image, lang=lang, timeout=30).strip()
                if text:
                    return text
    except Exception:
        _logger.debug('pytesseract image OCR failed', exc_info=True)
    executable = shutil.which('tesseract')
    if not executable:
        return ''
    with tempfile.NamedTemporaryFile(suffix='.png') as tmp:
        tmp.write(png_bytes)
        tmp.flush()
        for lang in languages:
            try:
                result = subprocess.run([executable, tmp.name, 'stdout', '-l', lang, '--psm', '6'],
                                        check=False, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            text = (result.stdout or '').strip()
            if text:
                return text
    return ''


def _deny_network(*_args, **_kwargs):
    raise OSError('Network is disabled in the local OCR worker')


def worker_network_policy() -> dict:
    """Probe the actual configured worker's socket policy without loading models."""
    from app import config

    return _worker_call({'operation': 'network_probe'}, min(10, config.PDF_OCR_PADDLE_TIMEOUT_SECONDS))


def worker(payload: dict) -> dict:
    """Standalone transport used by explicit compatible CPU interpreter only."""
    if payload.get('operation') == 'versions':
        versions = {}
        for package in PACKAGES:
            try:
                versions[package] = metadata.version(package)
            except metadata.PackageNotFoundError:
                versions[package] = 'unavailable'
        return {'versions': versions}
    # Prevent Python HTTP/model-source paths even if a library attempts retrieval.
    socket.socket.connect = _deny_network  # type: ignore[assignment]
    socket.socket.connect_ex = _deny_network  # type: ignore[assignment]
    socket.create_connection = _deny_network
    if payload.get('operation') == 'network_probe':
        result = {}
        with socket.socket() as probe:
            calls = [('connect_denied', lambda: probe.connect(('127.0.0.1', 9))),
                     ('connect_ex_denied', lambda: probe.connect_ex(('127.0.0.1', 9))),
                     ('create_connection_denied', lambda: socket.create_connection(('127.0.0.1', 9), timeout=.2))]
            for name, call in calls:
                try:
                    call()
                    result[name] = False
                except OSError as error:
                    result[name] = str(error) == 'Network is disabled in the local OCR worker'
        return result
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    try:
        import numpy as np
        from paddleocr import PaddleOCR
        from PIL import Image

        paths = payload['models']
        model = PaddleOCR(text_detection_model_name=DETECTOR, text_detection_model_dir=paths[DETECTOR],
                          text_recognition_model_name=payload['model'], text_recognition_model_dir=paths[payload['model']],
                          device='cpu', enable_mkldnn=False, cpu_threads=4,
                          text_det_limit_side_len=960, text_det_limit_type='max',
                          use_doc_orientation_classify=False, use_doc_unwarping=False,
                          use_textline_orientation=False)
        with Image.open(io.BytesIO(base64.b64decode(payload['png'], validate=True))) as image:
            results = list(model.predict(np.asarray(image.convert('RGB'))))
        texts: list[str] = []
        scores: list[float] = []
        for result in results:
            texts.extend(result['rec_texts'])
            scores.extend(float(score) for score in result['rec_scores'])
        return {'text': '\n'.join(texts), 'confidence': scores}
    except Exception as error:  # noqa: BLE001 - version-specific heavy dependency errors are contained.
        return {'error': type(error).__name__}


if __name__ == '__main__':
    payload = json.loads(sys.stdin.read())
    # Keep third-party progress separate from the one transport record.
    with contextlib.redirect_stdout(sys.stderr):
        response = worker(payload)
    print('OCR_RESULT:' + json.dumps(response))
