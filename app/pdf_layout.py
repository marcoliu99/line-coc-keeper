"""Optional, conservative two/three-column ordering of native PDF text; never OCR."""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any, Literal

import pymupdf

BBox = tuple[float, float, float, float]
LayoutReason = Literal['two_columns', 'three_columns', 'disabled', 'model_unavailable', 'backend_unavailable',
                       'unsupported_rotation', 'initialization_error', 'inference_error',
                       'malformed_result', 'not_two_columns', 'overlapping_regions',
                       'incomplete_mapping', 'ambiguous_mapping', 'invalid_order', 'content_mismatch', 'repair_required']


@dataclass(frozen=True)
class NativeLine:
    id: int
    bbox: BBox
    text: str


@dataclass(frozen=True)
class LayoutRegion:
    label: str
    bbox: BBox
    order: int | None


@dataclass(frozen=True)
class LayoutResult:
    text: str = ''
    status: Literal['accepted', 'fallback'] = 'fallback'
    reason: LayoutReason = 'model_unavailable'
    initialization_seconds: float = 0.0
    inference_seconds: float = 0.0


_IGNORED = {'image', 'chart', 'seal', 'header_image', 'footer_image'}
_TEXT = {'text', 'paragraph_title', 'doc_title', 'header', 'footer', 'number', 'footnote'}
_FOOTERS = {'footer', 'number', 'footnote'}


def _area(box: BBox) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _intersection(a: BBox, b: BBox) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _valid_box(box: BBox, width: float, height: float) -> bool:
    return (len(box) == 4 and all(math.isfinite(value) for value in box)
            and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)


def order_native_lines(lines: list[NativeLine], regions: list[LayoutRegion], *,
                       width: float, height: float) -> LayoutResult:
    """Return native text only for completely mapped, safely separated two or three columns."""
    def fallback(reason: LayoutReason) -> LayoutResult:
        return LayoutResult(reason=reason)

    if (not lines or not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0
            or len({line.id for line in lines}) != len(lines)
            or any(not line.text.strip() or not _valid_box(line.bbox, width, height) for line in lines)):
        return fallback('content_mismatch')
    if not regions or any(not _valid_box(region.bbox, width, height)
                          or region.label not in _TEXT | _IGNORED for region in regions):
        return fallback('malformed_result')
    eligible = [region for region in regions if region.label in _TEXT]
    for i, region in enumerate(eligible):
        for other in eligible[i + 1:]:
            if _intersection(region.bbox, other.bbox) / min(_area(region.bbox), _area(other.bbox)) > .5:
                return fallback('overlapping_regions')
    assignments: dict[int, list[NativeLine]] = {i: [] for i in range(len(eligible))}
    for line in lines:
        matches = sorted(((_intersection(line.bbox, region.bbox) / _area(line.bbox), i)
                          for i, region in enumerate(eligible)), reverse=True)
        if not matches or matches[0][0] < .15:
            return fallback('incomplete_mapping')
        primary = eligible[matches[0][1]].bbox
        if any((coverage >= .15 and matches[0][0] - coverage < .1) or (coverage > 0 and (eligible[i].bbox[0] >= primary[2]
                                                   or eligible[i].bbox[2] <= primary[0]))
               for coverage, i in matches[1:]):
            return fallback('ambiguous_mapping')
        assignments[matches[0][1]].append(line)
    body = [i for i, region in enumerate(eligible) if region.label == 'text' and assignments[i]]
    groups: list[list[int]] = []
    for i in sorted(body, key=lambda index: eligible[index].bbox[0]):
        if not groups or eligible[i].bbox[0] > max(eligible[j].bbox[2] for j in groups[-1]):
            groups.append([i])
        else:
            groups[-1].append(i)
    column_count = len(groups)
    if column_count not in {2, 3}:
        return fallback('not_two_columns')
    if column_count == 2:
        orders = [region.order for region in eligible if region.order is not None]
        if (any(type(order) is not int or order < 1 for order in orders)
                or len(set(orders)) != len(orders)
                or any(region.order is None and region.label not in {'header'} | _FOOTERS for region in eligible)):
            return fallback('invalid_order')

    intervals = [(min(eligible[i].bbox[0] for i in group), max(eligible[i].bbox[2] for i in group))
                 for group in groups]
    if (any(intervals[i + 1][0] - intervals[i][1] < .02 * width for i in range(column_count - 1))
            or any(right - left < .15 * width for left, right in intervals)
            or any(sum(len(assignments[i]) for i in group) < (3 if column_count == 3 else 2) for group in groups)):
        return fallback('not_two_columns')
    if column_count == 3:
        body_lines = [[line for i in group for line in assignments[i]] for group in groups]
        line_height = median(line.bbox[3] - line.bbox[1] for column in body_lines for line in column)
        bands = [(min(line.bbox[1] for line in column), max(line.bbox[3] for line in column))
                 for column in body_lines]
        heights = [bottom - top for top, bottom in bands]
        tolerance_y = max(2 * line_height, .1 * max(heights))
        if (min(heights) < 2 * line_height
                or max(top for top, _ in bands) - min(top for top, _ in bands) > tolerance_y
                or max(bottom for _, bottom in bands) - min(bottom for _, bottom in bands) > tolerance_y):
            return fallback('not_two_columns')
    top = min(eligible[i].bbox[1] for i in body)
    bottom = max(eligible[i].bbox[3] for i in body)
    tolerance = .01 * width
    vertical_tolerance = tolerance if column_count == 2 else 0.0
    columns: dict[int, int] = {}
    prefixes: list[int] = []
    suffixes: list[int] = []
    for i, region in enumerate(eligible):
        if not assignments[i]:
            continue
        if (column_count == 3 and region.label in {'doc_title', 'paragraph_title', 'header'}
                and region.bbox[3] <= top):
            prefixes.append(i)
            continue
        if region.label == 'header':
            if region.bbox[3] > top + vertical_tolerance:
                return fallback('invalid_order')
            prefixes.append(i)
            continue
        if region.label in _FOOTERS:
            if region.bbox[1] < bottom - vertical_tolerance:
                return fallback('invalid_order')
            suffixes.append(i)
            continue
        if region.label == 'doc_title':
            if region.bbox[3] > top + vertical_tolerance:
                return fallback('not_two_columns')
            prefixes.append(i)
            continue
        column = next((column for column, (left, right) in enumerate(intervals)
                       if region.bbox[0] >= left - tolerance and region.bbox[2] <= right + tolerance), None)
        if column is None:
            if region.label == 'paragraph_title' and region.bbox[3] <= top + vertical_tolerance:
                prefixes.append(i)
                continue
            return fallback('not_two_columns')
        columns[i] = column
    ordered_regions = (sorted(columns, key=lambda i: eligible[i].order or 0) if column_count == 2
                       else sorted(columns, key=lambda i: (columns[i], eligible[i].bbox[1], eligible[i].bbox[0], i)))
    if column_count == 2:
        sequence = [columns[i] for i in ordered_regions]
        if sequence != sorted(sequence) or set(sequence) != {0, 1}:
            return fallback('invalid_order')
        for column in (0, 1):
            column_lines = [line for i in ordered_regions if columns[i] == column
                            for line in sorted(assignments[i], key=lambda line: (line.bbox[1], line.bbox[0], line.id))]
            if column_lines != sorted(column_lines, key=lambda line: (line.bbox[1], line.bbox[0], line.id)):
                return fallback('invalid_order')
    # Reject disconnected native horizontal bands inside a predicted column,
    # including staggered short labels. This veto never constructs extra columns.
    for column in range(column_count):
        native = sorted((line for i in ordered_regions if columns[i] == column
                         for line in assignments[i]), key=lambda line: line.bbox[0])
        if column_count == 3 and any(line.bbox[0] < intervals[column][0]
                                     or line.bbox[2] > intervals[column][1] for line in native):
            return fallback('ambiguous_mapping')
        rightmost = native[0].bbox[2]
        for line in native[1:]:
            if line.bbox[0] - rightmost >= .02 * width:
                return fallback('not_two_columns')
            rightmost = max(rightmost, line.bbox[2])
    if column_count == 2:
        first = min(eligible[i].order or 0 for i in ordered_regions)
        last = max(eligible[i].order or 0 for i in ordered_regions)
        if (any(eligible[i].order is not None and (eligible[i].order or 0) >= first for i in prefixes)
                or any(eligible[i].order is not None and (eligible[i].order or 0) <= last for i in suffixes)):
            return fallback('invalid_order')
        region_order = (sorted(prefixes, key=lambda i: eligible[i].bbox[1]) + ordered_regions
                        + sorted(suffixes, key=lambda i: eligible[i].bbox[1]))
        ordered = [line for i in region_order for line in sorted(assignments[i],
                   key=lambda line: (line.bbox[1], line.bbox[0], line.id))]
    else:
        line_key = lambda line: (line.bbox[1], line.bbox[0], line.id)
        ordered = sorted((line for i in prefixes for line in assignments[i]), key=line_key)
        for column in range(3):
            ordered.extend(sorted((line for i in columns if columns[i] == column
                                   for line in assignments[i]), key=line_key))
        ordered.extend(sorted((line for i in suffixes for line in assignments[i]), key=line_key))
    if Counter(line.id for line in ordered) != Counter(line.id for line in lines):
        return fallback('content_mismatch')
    text = '\n'.join(line.text for line in ordered)
    if Counter(text.split()) != Counter(token for line in lines for token in line.text.split()):
        return fallback('content_mismatch')
    return LayoutResult(text=text, status='accepted', reason='three_columns' if column_count == 3 else 'two_columns')


MODEL = 'PP-DocLayoutV3'
MODEL_FILES = ('inference.json', 'inference.pdiparams', 'inference.yml')


def model_directory() -> Path:
    return Path(os.environ.get('PDF_PADDLE_LAYOUT_MODEL_DIR',
                str(Path.home() / '.cache' / 'line-coc-keeper' / 'paddle-layout'))).expanduser()


def model_ready(root: Path) -> bool:
    try:
        return all((root / MODEL / name).is_file() and (root / MODEL / name).stat().st_size > 0
                   for name in MODEL_FILES)
    except (OSError, ValueError):
        return False


_logger = logging.getLogger(__name__)
_lock = threading.Lock()
_engine: Any = None
_engine_root: Path | None = None


def _log_result(result: LayoutResult) -> LayoutResult:
    _logger.info('layout=paddle status=%s reason=%s initialization_seconds=%.6f inference_seconds=%.6f',
                 result.status, result.reason, result.initialization_seconds, result.inference_seconds)
    return result


def reorder_with_paddle(page: pymupdf.Page, *, repair_required: bool = False) -> LayoutResult:
    """Try a locally prepared layout model; failures never supply replacement text."""
    global _engine, _engine_root
    if os.environ.get('PDF_PADDLE_LAYOUT_ENABLED', 'true').casefold() in {'false', '0', 'no'}:
        return _log_result(LayoutResult(reason='disabled'))
    if repair_required:
        return _log_result(LayoutResult(reason='repair_required'))
    try:
        root = model_directory()
        if not model_ready(root):
            return _log_result(LayoutResult())
    except (OSError, RuntimeError, ValueError):
        return _log_result(LayoutResult())
    initialization = inference = 0.0
    stage: LayoutReason = 'malformed_result'
    try:
        if page.rotation:
            return _log_result(LayoutResult(reason='unsupported_rotation'))
        lines: list[NativeLine] = []
        for block in page.get_text('dict')['blocks']:
            if block['type'] != 0:
                continue
            for line in block['lines']:
                text = ''.join(span['text'] for span in line['spans'])
                if text.strip():
                    lines.append(NativeLine(len(lines), tuple(line['bbox']), text))
        if not lines:
            return _log_result(LayoutResult(reason='incomplete_mapping'))
        with _lock:
            if _engine is None or _engine_root != root:
                stage = 'initialization_error'
                started = time.perf_counter()
                try:
                    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
                    from paddleocr import LayoutDetection
                    engine = LayoutDetection(model_name=MODEL, model_dir=str(root / MODEL),
                                             device='cpu', enable_mkldnn=False, cpu_threads=4)
                    _engine, _engine_root = engine, root
                finally:
                    initialization = time.perf_counter() - started
            import numpy as np
            pixmap = page.get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
            stage = 'inference_error'
            started = time.perf_counter()
            try:
                output = _engine.predict(image[:, :, ::-1].copy())
            finally:
                inference = time.perf_counter() - started
        stage = 'malformed_result'
        if not isinstance(output, (list, tuple)) or len(output) != 1:
            return _log_result(LayoutResult(reason=stage, initialization_seconds=initialization,
                                           inference_seconds=inference))
        boxes = output[0]['boxes']
        if not isinstance(boxes, list):
            raise TypeError('Invalid regions')
        sx, sy = pixmap.width / page.rect.width, pixmap.height / page.rect.height
        regions: list[LayoutRegion] = []
        for box in boxes:
            coordinates = box['coordinate']
            if len(coordinates) != 4:
                raise ValueError('Invalid coordinates')
            bbox = (float(coordinates[0]) / sx, float(coordinates[1]) / sy,
                    float(coordinates[2]) / sx, float(coordinates[3]) / sy)
            regions.append(LayoutRegion(box['label'], bbox, box.get('order')))
        result = order_native_lines(lines, regions, width=page.rect.width, height=page.rect.height)
    except ImportError:
        result = LayoutResult(reason='backend_unavailable')
    except Exception:  # noqa: BLE001 - optional layout must never prevent legacy extraction.
        result = LayoutResult(reason=stage)
    return _log_result(replace(result, initialization_seconds=initialization, inference_seconds=inference))
