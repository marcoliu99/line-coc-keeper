"""Optional CPU PaddleOCR; model acquisition belongs exclusively to explicit setup."""
from __future__ import annotations

import io
import logging
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app import pdf_quality

_logger = logging.getLogger(__name__)
MODELS = ('PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec')
MODEL_FILES = ('inference.json', 'inference.pdiparams', 'inference.yml')
_lock = threading.Lock()
_engine: Any = None
_engine_root: Path | None = None
_MECHANIC = re.compile(r'(?<!\w)[+-]?\d+(?:[dD]\d+(?:\s*[+-]\s*(?:\d+|[A-Za-z]+))?|\.\d+)?%?(?!\w)')


@dataclass(frozen=True)
class OcrResult:
    text: str = ''
    status: Literal['accepted', 'rejected', 'unavailable', 'error', 'empty'] = 'unavailable'
    engine: Literal['paddleocr'] = 'paddleocr'


def model_directory() -> Path:
    return Path(os.environ.get('PDF_PADDLE_MODEL_DIR', str(Path.home() / '.cache' / 'line-coc-keeper' / 'paddleocr'))).expanduser()


def models_ready(root: Path) -> bool:
    return all((root / model / name).is_file() and (root / model / name).stat().st_size > 0
               for model in MODELS for name in MODEL_FILES)


def _candidate_safe(text: str, source: str, pairs: list[dict]) -> bool:
    if (not any(char.isalnum() for char in text) or len(text) > 100_000 or '\ufffd' in text
            or re.search(r'(?<!\w)[lI|][dD]\d', text)):
        return False
    if source:
        required = Counter(re.sub(r'\s', '', token).casefold() for token in _MECHANIC.findall(source))
        available = Counter(re.sub(r'\s', '', token).casefold() for token in _MECHANIC.findall(text))
        if required - available:
            return False
        # A rejected region candidate must not preempt a working legacy repair.
        if '\ufffd' in source and not pdf_quality.accept_region(source, text, pairs):
            return False
        intact = re.sub(r'\S*\ufffd\S*', '', source)
        if pdf_quality.select_text(intact, text)[1] != 'layout':
            return False
    resolved = [pair for pair in pairs if pair['status'] != 'unresolved']
    return all(check['status'] == 'matched' for check in pdf_quality.check_pairs(resolved, text))


def recognize_with_paddle(image_bytes: bytes, *, source_text: str = '', pairs: list[dict] | None = None) -> OcrResult:
    """Try local OCR once; unavailable/rejected output authorizes the existing fallback."""
    global _engine, _engine_root
    if os.environ.get('PDF_PADDLE_OCR_ENABLED', 'true').casefold() in {'false', '0', 'no'}:
        return OcrResult()
    try:
        root = model_directory()
        if not models_ready(root):
            return OcrResult()
        with _lock:
            if _engine is None or _engine_root != root:
                # Explicit paths bypass the SDK model downloader. No auxiliary models.
                os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
                from paddleocr import PaddleOCR
                _engine = PaddleOCR(device='cpu', enable_mkldnn=False,
                    text_detection_model_name=MODELS[0], text_detection_model_dir=str(root / MODELS[0]),
                    text_recognition_model_name=MODELS[1], text_recognition_model_dir=str(root / MODELS[1]),
                    use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
                _engine_root = root
            import numpy as np
            from PIL import Image
            with Image.open(io.BytesIO(image_bytes)) as image:
                output = _engine.predict(np.array(image.convert('RGB'))[:, :, ::-1].copy())
            lines: list[str] = []
            for page in output:
                entries = page['rec_texts']
                if not isinstance(entries, (list, tuple)) or not all(isinstance(line, str) for line in entries):
                    return OcrResult(status='rejected')
                lines.extend(entries)
            text = pdf_quality.normalize('\n'.join(lines))
        if not text:
            result = OcrResult(status='empty')
        elif not _candidate_safe(text, source_text, pairs or []):
            result = OcrResult(status='rejected')
        else:
            result = OcrResult(text=text, status='accepted')
    except ImportError:
        result = OcrResult()
    except Exception as error:  # noqa: BLE001 - optional OCR must never break the legacy path.
        _logger.debug('engine=paddleocr status=error error_type=%s', type(error).__name__)
        result = OcrResult(status='error')
    _logger.debug('engine=paddleocr status=%s', result.status)
    return result
