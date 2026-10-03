"""Source-preserving PDF extraction with per-page quality diagnostics.

Prefer checked layout text over native text, and invoke OCR only for pages lacking
usable text. Preserve the full library source and original page markers. Uncertain
layout, missing numeric evidence and possible continuations remain reviewable rather
than being silently guessed. Map interpretation remains explicitly identified as
model-derived evidence, distinct from verbatim source transcription.
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import io
import logging
import re
import threading
from collections.abc import Callable, Iterable
from functools import partial
from importlib import metadata
from typing import Any, Literal, TypedDict, cast

import pymupdf

from app import (
    config,
    pdf_admission,
    pdf_ai_repair,
    pdf_image_transcription,
    pdf_layout,
    pdf_layout_adapters,
    pdf_map_analysis,
    pdf_ocr,
    pdf_page_criticality,
    pdf_quality,
    pdf_raster_source,
    pdf_source_topology,
    pdf_source_topology_discovery,
)
from app.markitdown_shim import build_markitdown

_logger = logging.getLogger(__name__)

PIPELINE_VERSION = 'multicolumn-v9'
RENDERER_VERSION = 1
PdfExtraction = tuple[str, list[int], bool, dict[int, bytes], dict[int, dict]]
PageDisposition = Literal['accepted', 'needs_review', 'legacy_route', 'soft_review']
PublicationSeverity = Literal['NONE', 'HARD_BLOCK', 'SOFT_REVIEW']
ScenarioReadiness = Literal['READY', 'READY_WITH_WARNINGS', 'BLOCKED']
SourceBlockingReason = Literal['source_ordering_unverified', 'source_image_transcription_unverified',
                               'source_mechanics_unresolved', 'source_transcription_unverified',
                               'canonical_playable_source_missing']


class PagePublication(TypedDict):
    disposition: PageDisposition
    source_blocking_reasons: list[SourceBlockingReason]


def _publication_disposition(status: pdf_layout.LayoutStatus, source_blocking: bool) -> PageDisposition:
    if status == 'needs_review' or source_blocking:
        return 'needs_review'
    return 'accepted' if status == 'accepted' else 'legacy_route'



class LayoutReviewRequired(ValueError):
    """Keep an incomplete extraction available for a durable import draft."""

    def __init__(self, report: dict, result: PdfExtraction) -> None:
        self.report = report
        self.result = result
        pages = ', '.join(str(number) for number in report['blocked_pages'])
        super().__init__(f'PDF 頁面解析尚未完成：第 {pages} 頁；可繼續匯入未完成頁。')


def render_source_pages(texts: list[str]) -> str:
    """Keep physical page markers and downstream Unicode spans deterministic."""
    return '\n\n'.join(f'--- 第 {i + 1} 頁 ---\n{text}' for i, text in enumerate(texts)).strip()


def extraction_identity() -> dict:
    """Actual executable versions, including renderer and enabled optional parser."""
    def version(package: str) -> str:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return 'unavailable'
    return {'pipeline_version': PIPELINE_VERSION, 'renderer_version': RENDERER_VERSION,
            'admission_version': pdf_admission.VERSION, 'ordering_validation_version': 2, 'page_criticality_version': pdf_page_criticality.VERSION, 'appendix_triage_version': 1,
            'quality_version': pdf_quality.VERSION, 'layout_version': pdf_layout.PIPELINE_VERSION,
            'pymupdf': version('PyMuPDF'), 'pymupdf4llm': version('pymupdf4llm'),
            'markitdown': version('markitdown'), 'ocr': pdf_ocr.identity(),
            'source_discovery': pdf_source_topology_discovery.identity(),
            'map_version': pdf_map_analysis.VERSION, 'source_topology_version': pdf_source_topology.VERSION,
            'docling': version('docling') if config.PDF_LAYOUT_DOCLING_ENABLED else 'disabled'}


def _cached_page(cached: dict | None, pdf_hash: str, number: int, identity: dict) -> dict | None:
    if not isinstance(cached, dict):
        return None
    text = cached.get('selected_text')
    row = cached.get('report')
    if (cached.get('pdf_sha256') != pdf_hash or cached.get('pipeline_version') != PIPELINE_VERSION
            or cached.get('extraction_identity') != identity
            or cached.get('renderer_version') != RENDERER_VERSION
            or not isinstance(text, str) or not isinstance(row, dict)
            or row.get('page') != number or row.get('disposition') not in {'accepted', 'legacy_route', 'soft_review'}
            or row.get('publication_severity') == 'HARD_BLOCK' or row.get('source_blocking_reasons')
            or cached.get('selected_sha256') != hashlib.sha256(text.encode()).hexdigest()):
        return None
    if cached.get('map') is not None:
        if not isinstance(cached.get('image'), bytes):
            return None
        reusable = pdf_map_analysis.reusable_visual_graph(cached['map'], row.get('map_analysis'), cached['image'])
        if reusable is None:
            return None
        cached = copy.deepcopy(cached)
        cached['map'] = reusable.graph
        cached['report']['map_analysis'] = reusable.analysis
    if (row.get('map_analysis', {}).get('status') == 'MAP_GRAPH_VERIFIED'
            and not isinstance(cached.get('map'), dict)):
        return None
    return cached

# Below this many extracted characters, a page that also contains an image is
# treated as "probably graphic content" (handout/map/cover) and gets a
# vision-description or OCR pass. 200 rather than a tighter cutoff: checked
# against a real scenario PDF, its graphic pages (pregen sheets, floor plans,
# illustrations) topped out at 137 chars while the shortest genuine narrative
# page had 369 — plenty of margin either side of that gap. The two floor-plan
# pages that actually caused a real in-game room-layout mistake had 53 and 70
# chars each, comfortably past the old 40-char cutoff undetected.
_LOW_TEXT_THRESHOLD = 200

# A page with at least this many vector drawing primitives (lines, rectangles,
# curves — pymupdf's page.get_drawings()) is treated as "probably graphic
# content" the same way a page with an embedded raster image is (see
# _page_has_graphic_content below). Catches a real gap: a floor plan drawn
# entirely out of vector shapes (walls as lines/rectangles, no embedded raster
# image anywhere on the page) used to fall through _LOW_TEXT_THRESHOLD's
# has_images-only check completely untouched — not misclassified, just never
# rendered or analyzed at all, contradicting this module's own stated reason
# for keeping the whole-page-render fallback in the first place (see the
# module docstring). 8 is a conservative floor: confirmed against two
# synthetic pages — a plain page with just decorative top/bottom rule
# lines came out to 2 drawing primitives, while a 3-room floor plan drawn
# as individual wall-line segments (the way a real vector floor-plan
# export typically looks, not one rectangle per room) came out to 13; 8
# sits cleanly between the two.
_MIN_VECTOR_DRAWINGS = 8

# How many low-text pages to describe concurrently. A scenario can easily have
# 15-20+ such pages (character sheets, maps, illustrations); doing them one at
# a time risked the whole upload taking minutes — long enough to delay the
# "scenario loaded" confirmation.
# 6 workers against a real 24-page batch still took ~60s wall time; 12 is the
# next thing to try if that's still too slow in practice.
_MAX_CONCURRENT_PAGE_CALLS = 12

# Plain-text prompt for markitdown-ocr's embedded-image OCR (see
# _markitdown_page_texts/app/markitdown_shim.py) — that path calls a simple
# text-completion-style API (OCRResult.text), not a forced tool call, so it
# can't return the structured fields app/scene_map.py's analyze_page_image
# tool schema does. Kept as plain prose matching the same three-branch
# classification (map / character sheet / other) for consistency, even
# though only the text half is usable here.
_VISION_PROMPT = (
    "請忠實轉錄圖片中的所有文字，保留原文語言、標題、段落、表格欄列、數字與骰式。"
    "這是劇本來源擷取，不是翻譯、摘要或故事解讀。正文、手稿、規則與角色背景不可省略。"
    "角色卡保留年齡、所有屬性技能、背景欄位及空白欄位的標籤；空值標為未填，不能猜值。"
    "雙欄按左欄到右欄閱讀；表格維持標籤與值對應。看不清楚處標示 [無法辨識]。"
    "地圖只抄錄標籤，不推測通道或空間關係；無文字圖片回傳空字串。"
)



def _page_has_graphic_content(page: pymupdf.Page) -> bool:
    """True if this page has an embedded raster image OR at least
    _MIN_VECTOR_DRAWINGS vector drawing primitives (lines/rectangles/curves).
    The raster check alone (page.get_images()) misses a floor plan drawn
    entirely out of vector shapes — see _MIN_VECTOR_DRAWINGS' own comment for
    why that used to mean such a page was never even rendered for the
    vision/OCR fallback below, not just misclassified once it got there."""
    if page.get_images():
        return True
    return len(page.get_drawings()) >= _MIN_VECTOR_DRAWINGS


def _render_page_png(page: pymupdf.Page, dpi: int = 200) -> bytes:
    return page.get_pixmap(dpi=dpi).tobytes("png")


def _ocr_image(png_bytes: bytes) -> str:
    """Compatibility seam for unchanged Tesseract fallback; not a publication gate."""
    return pdf_ocr.tesseract_text(png_bytes)


def recover_local_ocr(png_bytes: bytes, original: str, pairs: list[dict], *,
                      region: bool = True, source_unique: bool = True) -> tuple[str, list[pdf_ocr.OcrAttempt]]:
    """Offer local candidates lazily, preserving rejected attempts before fallback."""
    attempts = []
    for attempt in pdf_ocr.candidates(png_bytes, tesseract=_ocr_image):
        candidate = pdf_quality.normalize(attempt['candidate'])
        attempt['candidate'] = candidate
        attempt['pair_checks'] = pdf_quality.check_pairs(pairs, candidate)
        if attempt['status'] == 'candidate':
            accepted = source_unique and (pdf_quality.accept_region(original, candidate, pairs) if region
                        else pdf_quality.accept_transcription(original, candidate, pairs))
            attempt['status'] = 'accepted' if accepted else 'rejected'
            if not accepted:
                attempt['reason'] = 'source_evidence_gate_failed' if source_unique else 'source_region_not_unique'
        attempts.append(attempt)
        if attempt['status'] == 'accepted':
            return candidate, attempts
    return '', attempts


def _analyze_graphic_page(png_bytes: bytes, *, reserve: Callable[[], bool], candidate: bool) -> pdf_map_analysis.MapResult:
    """Image-only graph certification remains independent of source transcription."""
    return pdf_map_analysis.analyze(png_bytes, reserve=reserve, candidate=candidate)


class MarkitdownPages(dict[int, str]):
    """Page-aligned converted text with separately observed image OCR provenance."""

    def __init__(self) -> None:
        super().__init__()
        self.image_evidence: dict[int, pdf_image_transcription.ImageEvidence] = {}


_MARKITDOWN_PAGE_RE = re.compile(r"^##\s*Page\s+(\d+)\s*$", re.MULTILINE)


def _markitdown_page_texts(pdf_bytes: bytes, page_numbers: list[int] | None = None, *,
                          reserve_image_request: Callable[[], bool] | None = None) -> dict[int, str] | None:
    """Best-effort: convert the whole PDF via MarkItDown (+ markitdown-ocr,
    see app/markitdown_shim.py — its PDF converter emits a "## Page N" header
    before every page's content, which is what this splits back apart into a
    1-indexed page-number -> text dict). Any embedded raster image markitdown-
    ocr detects gets OCR'd inline using our own _VISION_PROMPT, not its
    generic default, so a character-sheet photo embedded this way gets the
    same exhaustive-transcription treatment as the whole-page fallback below.

    Returns None on any failure (no ANTHROPIC_API_KEY, markitdown/markitdown-
    ocr not installed, the call raised, or the output had no recognizable
    page markers to align against actual page numbers) — callers must fall
    back to PyMuPDF's own text layer per page in that case, exactly as this
    module worked before MarkItDown was wired in."""
    if page_numbers is not None:
        if not page_numbers:
            return None
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as original, pymupdf.open() as subset:
            for number in page_numbers:
                subset.insert_pdf(original, from_page=number - 1, to_page=number - 1)
            pdf_bytes = subset.tobytes()
    image_ocr_evidence: list[dict] = []
    md = build_markitdown(_VISION_PROMPT, image_ocr_evidence=image_ocr_evidence,
                         reserve_image_request=reserve_image_request)
    if md is None:
        return None
    try:
        from markitdown import StreamInfo

        result = md.convert(io.BytesIO(pdf_bytes), stream_info=StreamInfo(extension=".pdf"))
        text = result.text_content
    except Exception:  # optional MarkItDown plugins expose heterogeneous conversion errors.
        _logger.debug("MarkItDown page conversion failed", exc_info=True)
        return None
    if not text or not text.strip():
        return None

    matches = list(_MARKITDOWN_PAGE_RE.finditer(text))
    if not matches:
        return None  # can't align without page markers — don't guess

    pages = MarkitdownPages()
    for i, m in enumerate(matches):
        page_num = int(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        page_text = text[m.end() : end].strip()
        page_text = re.sub(r"[ \t]+", " ", page_text)
        page_text = re.sub(r"\n{3,}", "\n\n", page_text)
        if page_numbers is not None:
            if not 1 <= page_num <= len(page_numbers):
                continue
            page_num = page_numbers[page_num - 1]
        pages[page_num] = page_text
        # Only a full-page transcription observed on the image completion path
        # can certify the local candidate; unrelated embedded captions cannot.
        for observed in image_ocr_evidence:
            if pdf_quality.accept_independent_transcription(page_text, observed['candidate']):
                pages.image_evidence[page_num] = {
                    'origin': 'markitdown_ocr', 'source_id': observed['source_id'],
                    'candidate': observed['candidate'], 'page_type': 'text'}
                break
    return pages


def _pymupdf4llm_page_chunks(pdf_bytes: bytes, page_numbers: list[int] | None = None) -> dict[int, dict] | None:
    """Return PyMuPDF4LLM's page-level layout evidence when available.

    PyMuPDF4LLM is intentionally optional at import time. This keeps preview
    and existing deployments usable while requirements installation is being
    rolled out, and lets the parser fall back to PyMuPDF + MarkItDown if a
    particular PDF or package version cannot be processed.

    The package has used both ``page`` and ``page_number`` in its metadata
    examples across releases, so accept either key and normalize to a 1-based
    page number here.
    """
    try:
        import pymupdf4llm
    except Exception:  # optional parser import can fail during model/plugin initialization.
        # pymupdf4llm imports pymupdf.layout, which can eagerly load an
        # optional ONNX model. A broken/missing model is a parser capability
        # problem, not a reason to reject an otherwise readable PDF.
        _logger.debug("pymupdf4llm is unavailable", exc_info=True)
        return None

    doc = None
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if page_numbers is not None:
            if not page_numbers:
                return None
            subset = pymupdf.open()
            for number in page_numbers:
                subset.insert_pdf(doc, from_page=number - 1, to_page=number - 1)
            doc.close()
            doc = subset
        chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            write_images=False,
            embed_images=False,
            use_ocr=False,
            force_ocr=False,
        )
    except Exception:  # a parser failure must fall back to PyMuPDF text extraction.
        _logger.debug("pymupdf4llm page parsing failed", exc_info=True)
        return None
    finally:
        if doc is not None:
            doc.close()

    if not isinstance(chunks, list):
        return None

    pages: dict[int, dict] = {}
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if "page_number" in metadata:
            raw_page = metadata["page_number"]
            zero_based = False
        elif "page" in metadata:
            raw_page = metadata["page"]
            zero_based = True
        else:
            raw_page = None
            zero_based = False
        try:
            page_number = int(raw_page) if raw_page is not None else index + 1
        except (TypeError, ValueError):
            page_number = index + 1
        if zero_based:
            page_number += 1
        if page_numbers is not None:
            if not 1 <= page_number <= len(page_numbers):
                continue
            page_number = page_numbers[page_number - 1]
        if page_number >= 1:
            pages[page_number] = chunk
    return pages or None


def _pymupdf4llm_has_graphic_evidence(chunk: dict | None) -> bool:
    """Whether a PyMuPDF4LLM page chunk contains non-text layout evidence."""
    if not chunk:
        return False
    for key in ("images", "graphics", "tables"):
        value = chunk.get(key)
        if isinstance(value, (list, tuple, dict)) and value:
            return True
    page_boxes = chunk.get("page_boxes")
    if isinstance(page_boxes, list):
        return any(
            isinstance(box, dict)
            and str(box.get("class", "")).lower() in {"picture", "image", "figure", "graphic", "table"}
            for box in page_boxes
        )
    return False


def _pymupdf4llm_page_text(chunk: dict | None) -> str:
    if not chunk:
        return ""
    text = chunk.get("text")
    if not isinstance(text, str):
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _repair_local_regions(page: pymupdf.Page, evidence: dict, pairs: list[dict],
                          text: str, budget: list[int]) -> tuple[str, list[dict]]:
    """Crop only suspect native blocks; keep every attempt in the audit artifact."""
    suspect = {p["block"] for p in pairs if p["status"] == "unresolved"}
    attempts = []
    for block in evidence["blocks"]:
        original = pdf_quality.normalize("\n".join(line["text"] for line in block["lines"]))
        if block["id"] not in suspect and "\ufffd" not in original:
            continue
        local_pairs = [p for p in pairs if p['block'] == block['id']]
        if pdf_quality.recovered_region(original, text, local_pairs) is not None:
            continue  # The final selected source already passes this exact repair gate.
        attempt = {"block": block["id"], "bbox": block["bbox"], "original": original,
                   "ocr_text": "", "ocr_attempts": [], "status": "review_required"}
        attempts.append(attempt)
        if page.rotation:
            attempt["status"] = "rotation_requires_review"
            continue
        if budget[0] <= 0:
            attempt["status"] = "budget_exhausted"
            continue
        budget[0] -= 1
        rect = (pymupdf.Rect(block["bbox"]) + (-2, -2, 2, 2)) & page.rect
        attempt["crop_bbox"] = list(rect)
        try:
            png = page.get_pixmap(clip=rect, dpi=300).tobytes("png")
            local_pairs = [p for p in pairs if p['block'] == block['id']]
            candidate, engine_attempts = recover_local_ocr(
                png, original, local_pairs, source_unique=text.count(original) == 1)
            attempt['ocr_attempts'] = engine_attempts
        except Exception:  # noqa: BLE001 - local optional OCR never discards source.
            attempt["status"] = "ocr_failed"
            continue
        attempt["ocr_text"] = candidate or (engine_attempts[-1]["candidate"] if engine_attempts else "")
        local_pairs = [p for p in pairs if p["block"] == block["id"]]
        attempt["pair_checks"] = pdf_quality.check_pairs(local_pairs, attempt["ocr_text"])
        if not candidate:
            attempt["status"] = "review_required" if any(a["candidate"] for a in engine_attempts) else "ocr_empty"
        elif text.count(original) == 1 and pdf_quality.accept_region(original, candidate, local_pairs):
            text = text.replace(original, candidate, 1)
            attempt["status"] = "accepted"
    return text, attempts


def extract_text(pdf_bytes: bytes, *, quality_report: dict | None = None, local_ocr_limit: int = 8,
                 ai_repair_limit: int = 8, resume_pages: dict[int, dict] | None = None,
                 layout_budget: dict | None = None,
                 layout_budget_checkpoint: Callable[[dict], None] | None = None,
                 ai_repair_ledger: dict | None = None,
                 ai_budget_checkpoint: Callable[[dict], None] | None = None) -> PdfExtraction:
    """Return complete source, review pages, legacy truncation flag, images, maps.

    The source is never cut to a prompt budget. The optional report distinguishes
    extraction methods, uncertain pages and derived visual descriptions.
    """
    report = quality_report if quality_report is not None else {}
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    identity = extraction_identity()
    report.update(extraction_identity=identity, version=pdf_quality.VERSION, pdf_sha256=pdf_hash,
                  pipeline_version=PIPELINE_VERSION, renderer_version=RENDERER_VERSION,
                  pages=[], continuations=[], derived_descriptions={})
    local_budget = [max(0, local_ocr_limit)]
    local_page_budget = max(0, local_ocr_limit)
    ai_repair_ledger = ai_repair_ledger if ai_repair_ledger is not None else {}
    ai_used_before = ai_repair_ledger.get("consumed_requests", 0)
    ai_budget = [max(0, ai_repair_limit - ai_used_before)]
    report["ai_repair_budget"] = ai_repair_ledger
    layout_budget = pdf_layout_adapters.reconcile_budget(layout_budget)
    report["layout_budget"] = layout_budget
    cached_pages = {}
    if resume_pages:
        with pymupdf.open(stream=pdf_bytes, filetype='pdf') as source:
            cached_pages = {number: cached for number in range(1, len(source) + 1)
                            if (cached := _cached_page(resume_pages.get(number), pdf_hash, number, identity)) is not None}
            unresolved_pages = [number for number in range(1, len(source) + 1) if number not in cached_pages]
        layout_pages = _pymupdf4llm_page_chunks(pdf_bytes, unresolved_pages)
    else:
        layout_pages = _pymupdf4llm_page_chunks(pdf_bytes)
    texts: list[str] = []
    images: dict[int, bytes] = {}
    pending: dict[int, bytes] = {}
    maps: dict[int, dict] = {}
    map_candidates: set[int] = set()
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        report["page_count"] = doc.page_count
        for i, page in enumerate(doc):
            number = i + 1
            if number in cached_pages:
                cached = cached_pages[number]
                texts.append(cached['selected_text'])
                row = copy.deepcopy(cached['report'])
                row['resumed'] = True
                report['pages'].append(row)
                if isinstance(cached.get('image'), bytes):
                    images[number] = cached['image']
                if isinstance(cached.get('map'), dict):
                    maps[number] = copy.deepcopy(cached['map'])
                if isinstance(cached.get('derived_description'), str):
                    report['derived_descriptions'][str(number)] = cached['derived_description']
                continue
            native, warnings = pdf_quality.native_text(page)
            chunk = layout_pages.get(number) if layout_pages else None
            layout_text = _pymupdf4llm_page_text(chunk)
            evidence = pdf_quality.block_evidence(page)
            pairs = pdf_quality.numeric_pairs(evidence)
            if any(p["status"] == "unresolved" for p in pairs):
                warnings.append("source_pair_unresolved")
            text, method, selected_warnings = pdf_quality.select_text(native, layout_text)
            pair_checks = pdf_quality.check_pairs(pairs, layout_text) if layout_text else []
            if any(p["status"] == "pair_mismatch" for p in pair_checks):
                text, method = native, "native"
                selected_warnings.append("layout_pair_mismatch")
            if any(p["status"] in {"source_pair_unresolved", "candidate_pair_unverified"} for p in pair_checks):
                text, method = native, "native"
                selected_warnings.append("numeric_pair_review")
            warnings.extend(selected_warnings)
            decision = pdf_layout.analyze_page(page, {'native': native, 'layout': layout_text})
            if decision['status'] == 'needs_review':
                if layout_budget_checkpoint is None:
                    decision = pdf_layout_adapters.resolve_page(page, decision, layout_budget)
                else:
                    decision = pdf_layout_adapters.resolve_page(page, decision, layout_budget,
                        budget_checkpoint=layout_budget_checkpoint)
            if decision['status'] == 'accepted':
                candidate = decision['selected_text']
                _, checked_method, loss_warnings = pdf_quality.select_text(native, candidate)
                known_pairs = [pair for pair in pairs if pair['status'] != 'unresolved']
                order_pair_checks = pdf_quality.check_pairs(known_pairs, candidate)
                if (checked_method != 'layout'
                        or any(check['status'] != 'matched' for check in order_pair_checks)):
                    decision['status'] = 'needs_review'
                    decision['diagnostics'].append('layout_source_gate_failed')
                    warnings.extend(loss_warnings)
                else:
                    text, method = candidate, 'multicolumn'
            warnings.extend(decision['diagnostics'])
            graphic = _page_has_graphic_content(page) or _pymupdf4llm_has_graphic_evidence(chunk)
            if graphic:
                images[number] = _render_page_png(page)
            # Readable labels do not establish a floor plan's spatial graph.
            map_text = re.sub(r"[·_]+", " ", native + "\n" + layout_text)
            map_heading = re.search(r"(?i)\bfloor\s*plan\b|\binvestigator\s+map\b|平面圖|樓層圖", map_text)
            short_map_title = len(native) < _LOW_TEXT_THRESHOLD and re.search(r"(?i)\bmap\b|地圖", native)
            if graphic and (map_heading or short_map_title):
                map_candidates.add(number)
                pending[number] = images[number]
            raster_source_gap = pdf_raster_source.raster_source_gap(
                page, native, minimum_text=_LOW_TEXT_THRESHOLD)
            safe_short_native = bool(native.strip()
                                     and not raster_source_gap
                                     and '\ufffd' not in text
                                     and all(p['status'] == 'matched' for p in pdf_quality.check_pairs(pairs, text)))
            if raster_source_gap:
                pending[number] = images[number]
            if len(text) < _LOW_TEXT_THRESHOLD:
                warnings.append("low_text")
                if graphic and not safe_short_native:
                    pending[number] = images[number]
            texts.append(text)
            report["pages"].append({"page": number, "method": method, "native_chars": len(native),
                                    "warnings": warnings, "evidence": evidence,
                                    "numeric_pairs": pairs, "layout_pair_checks": pair_checks, "local_repairs": [],
                                    "candidates": {"native": native, "layout": layout_text},
                                    "layout_decision": decision, "graphic_evidence": graphic,
                                    "safe_short_native": safe_short_native,
                                    "raster_source_gap": raster_source_gap,
                                    "requires_image_transcription": bool(graphic and (raster_source_gap or len(text) < _LOW_TEXT_THRESHOLD)
                                                                           and not safe_short_native),
                                    "source_kind": ('native_text_absent' if not native.strip() else
                                                    'native_text_present_but_short' if len(native) < _LOW_TEXT_THRESHOLD
                                                    else 'native_text_present')})
        safe_pages = pdf_page_criticality.source_pages(report['pages'], texts)
        asset_sections = pdf_page_criticality.asset_sections(doc)
        for number in list(pending):
            row = report['pages'][number - 1]
            if not row['requires_image_transcription'] or row.get('resumed'):
                continue
            criticality = pdf_page_criticality.classify(pending[number], page=number,
                pdf_sha256=pdf_hash, native=row['candidates']['native'], safe=safe_pages, asset=asset_sections.get(number))
            row['page_criticality'] = criticality
            if criticality['source_critical'] is False:
                if criticality['page_role'] in {'OPTIONAL_PREGEN', 'OPTIONAL_HANDOUT', 'DUPLICATE_SOURCE'}:
                    texts[number - 1] = f"[PDF_OPTIONAL_ASSET: {criticality['page_role']}; page {number}; original image retained]"
                if criticality['page_role'] == 'PURE_ILLUSTRATION':
                    row['verified_illustration'] = True
                if criticality['map_asset']:
                    map_candidates.add(number)
                if number not in map_candidates:
                    pending.pop(number)
        appendix = pdf_page_criticality.triage(pending, pdf_sha256=pdf_hash, safe=safe_pages,
            assets=asset_sections, current={row['page']: row['page_criticality']
                for row in report['pages'] if 'page_criticality' in row})
        for number, criticality in appendix.items():
            report['pages'][number - 1]['page_criticality'] = criticality
            texts[number - 1] = f"[PDF_OPTIONAL_ASSET: {criticality['page_role']}; page {number}; original image retained]"
            pending.pop(number, None)
        for i, page in enumerate(doc):
            row = report['pages'][i]
            if row.get('resumed') or row.get('page_criticality', {}).get('source_critical') is False:
                continue
            text, repairs = _repair_local_regions(page, row['evidence'], row['numeric_pairs'], texts[i], local_budget)
            texts[i] = text
            row['local_repairs'] = repairs
            if repairs:
                row['warnings'].append('local_ocr_review' if any(r['status'] != 'accepted' for r in repairs) else 'local_ocr_repaired')
                if any(r['status'] == 'accepted' for r in repairs):
                    row['method'] += '+local_ocr'
        # Only pages lacking usable text go through the potentially paid OCR
        # adapter. Already readable layout pages never trigger whole-book OCR.
        for number in list(pending):
            row = report['pages'][number - 1]
            if row.get('page_criticality', {}).get('source_critical') is False:
                continue
            if (len(texts[number - 1]) >= _LOW_TEXT_THRESHOLD and not row['requires_image_transcription']) or local_page_budget <= 0:
                continue  # Readable map labels still require scene_map, not whole-page OCR.
            local_page_budget -= 1
            chosen, attempts = recover_local_ocr(pending[number], texts[number - 1],
                                                  row['numeric_pairs'], region=False)
            row['page_ocr_attempts'] = attempts
            if any(re.search(r'(?i)\bfloor\s*plan\b|\binvestigator\s+map\b|平面圖|樓層圖', a['candidate'])
                   for a in attempts):
                map_candidates.add(number)
            if row['requires_image_transcription']:
                transcription = pdf_image_transcription.retain_local(attempts)
                if transcription:
                    row['image_transcription'] = transcription
            if chosen:
                texts[number - 1] = chosen
                row['method'] += '+local_page_ocr'
                if (len(chosen) >= _LOW_TEXT_THRESHOLD and number not in map_candidates
                        and not row['requires_image_transcription']):
                    pending.pop(number)
        alternate_pages = [number for number in pending if report['pages'][number - 1].get('page_criticality', {}).get('source_critical') is not False and (len(texts[number - 1]) < _LOW_TEXT_THRESHOLD
                           or report['pages'][number - 1]['requires_image_transcription'])
                           and (report['pages'][number - 1]['requires_image_transcription']
                                or not any(a['status'] == 'accepted' for a in
                                           report['pages'][number - 1].get('page_ocr_attempts', [])))]
        alternate: dict[int, str] = MarkitdownPages()
        # Convert separately so each image request is charged to its physical page,
        # even when a converter makes several requests for embedded images.
        for number in sorted(alternate_pages):
            def reserve_image_request(number: int = number) -> bool:
                return pdf_image_transcription.reserve_verification(layout_budget, number, layout_budget_checkpoint)
            converted = _markitdown_page_texts(pdf_bytes, [number], reserve_image_request=reserve_image_request)
            if converted:
                alternate.update(converted)
                if isinstance(converted, MarkitdownPages) and isinstance(alternate, MarkitdownPages):
                    alternate.image_evidence.update(converted.image_evidence)
        for number in list(pending):
            extra = (alternate or {}).get(number, "").strip()
            if extra:
                row = report["pages"][number - 1]
                row["candidates"]["markitdown"] = extra
                if row['requires_image_transcription']:
                    independent = alternate.image_evidence.get(number) if isinstance(alternate, MarkitdownPages) else None
                    if independent:
                        chosen = pdf_image_transcription.verify(row, independent)
                        if chosen:
                            texts[number - 1] = chosen
                            row['method'] = 'image_transcription'
                            if number not in map_candidates:
                                pending.pop(number)
                            continue
                checks = pdf_quality.check_pairs(row["numeric_pairs"], extra)
                row["ocr_pair_checks"] = checks
                if any(p["status"] != "matched" for p in checks):
                    row["warnings"].append("ocr_pair_review")
                chosen, method, warnings = pdf_quality.select_text(texts[number - 1], extra)
                if (any(p['status'] != 'matched' for p in checks)
                        or not pdf_quality.accept_transcription(texts[number - 1], extra, row['numeric_pairs'])):
                    method = "native"
                    row["warnings"].append("ocr_pair_mismatch")
                if method == "layout":
                    texts[number - 1] = chosen
                    report["pages"][number - 1]["method"] = "markitdown"
                else:
                    report["pages"][number - 1]["warnings"].append("ocr_evidence_loss")
                if (len(texts[number - 1]) >= _LOW_TEXT_THRESHOLD and number not in map_candidates
                        and not row['requires_image_transcription']):
                    pending.pop(number)

        # Image-only candidates need independent image evidence, never native-empty certification.
        for number in list(pending):
            row = report['pages'][number - 1]
            if row.get('page_criticality', {}).get('source_critical') is False or not row['requires_image_transcription'] or number in map_candidates:
                continue
            if not pdf_image_transcription.reserve_verification(layout_budget, number, layout_budget_checkpoint):
                row['warnings'].append('image_verification_budget_exhausted')
                pending.pop(number)
                continue
            try:
                image_evidence = pdf_image_transcription.analyze(pending[number])
            except Exception:  # noqa: BLE001 - preserve private candidates on provider failure.
                row['warnings'].append('image_verification_failed')
                pending.pop(number)
                continue
            if image_evidence is None:
                row['warnings'].append('vision_empty')
                pending.pop(number)
                continue
            row['image_page_type'] = image_evidence['page_type']
            if (image_evidence['page_type'] == 'illustration' and not image_evidence['candidate']
                    and not row['candidates']['native'].strip()
                    and (not any(a['candidate'].strip() for a in row.get('page_ocr_attempts', []))
                         or any(a['engine'] == 'paddleocr' and a['status'] == 'empty'
                                for a in row.get('page_ocr_attempts', [])))):
                row['verified_illustration'] = True
                pending.pop(number)
                continue
            chosen = pdf_image_transcription.verify(row, image_evidence)
            if chosen:
                texts[number - 1] = chosen
                row['method'] = 'image_transcription'
                pending.pop(number)
            elif image_evidence['page_type'] == 'map':
                map_candidates.add(number)
            else:
                pending.pop(number)  # The transcription request already classified this non-map page.

        # Repair only remaining numeric/corrupted blocks, before extraction consumers.
        for i, row in enumerate(report["pages"]):
            if row.get('resumed'):
                continue
            if row.get('page_criticality', {}).get('source_critical') is False:
                row['ai_repair'] = {'regions': [], 'unresolved_labels': []}
                continue
            texts[i], ai_result = pdf_ai_repair.repair_page(doc[i], row, texts[i], ai_budget,
                ledger=ai_repair_ledger, checkpoint=ai_budget_checkpoint)
            row["ai_repair"] = ai_result
            if any(r["status"] == "accepted" for r in ai_result["regions"]):
                row["method"] += "+ai_repair"
                if len(texts[i]) >= _LOW_TEXT_THRESHOLD and i + 1 not in map_candidates:
                    pending.pop(i + 1, None)
            if ai_result["unresolved_labels"]:
                row["warnings"].append("ai_fields_unresolved")

        if pending:
            map_dispatch_lock = threading.Lock()
            def reserve_map_request(number: int) -> bool:
                with map_dispatch_lock:
                    return pdf_image_transcription.reserve_verification(layout_budget, number, layout_budget_checkpoint)
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENT_PAGE_CALLS, len(pending))) as executor:
                futures = {executor.submit(_analyze_graphic_page, png,
                    reserve=partial(reserve_map_request, number),
                    candidate=number in map_candidates): number for number, png in pending.items()}
                for future in concurrent.futures.as_completed(futures):
                    number = futures[future]
                    row = report["pages"][number - 1]
                    try:
                        map_result = future.result()
                        extra, scene_map = map_result.description, map_result.graph
                        row['map_analysis'] = map_result.analysis
                        if map_result.analysis['candidate']:
                            map_candidates.add(number)
                    except Exception:  # noqa: BLE001 - retain other pages and record this failed fallback.
                        row["warnings"].append("vision_failed")
                        analysis = pdf_map_analysis.not_analyzed(candidate=number in map_candidates)
                        analysis.update({'status': 'MAP_ANALYSIS_FAILED', 'initial_status': 'MAP_ANALYSIS_FAILED',
                                         'analysis_attempted': True})
                        row['map_analysis'] = analysis
                        continue
                    if scene_map and pdf_map_analysis.verified_graph(scene_map, row['map_analysis'], pending[number]):
                        maps[number] = scene_map
                    else:
                        scene_map = None
                    if not extra:
                        row["warnings"].append("vision_empty")
                    if extra:
                        row["candidates"]["vision"] = extra
                        checks = pdf_quality.check_pairs(row["numeric_pairs"], extra)
                        row["vision_pair_checks"] = checks
                        if any(p["status"] != "matched" for p in checks):
                            row["warnings"].append("vision_pair_review")
                        if any(p["status"] == "pair_mismatch" for p in checks):
                            row["warnings"].append("vision_pair_mismatch")
                            continue
                        if scene_map:
                            report["derived_descriptions"][str(number)] = scene_map.get('description', extra)
                            # Graph interpretation is a separate artifact, never PDF source text.
                        elif number in map_candidates:
                            # Uncertified descriptions belong only to private evidence.
                            row['map_candidate_description'] = extra
                        elif pdf_quality.accept_transcription(texts[number - 1], extra, row['numeric_pairs']):
                            texts[number - 1] = extra
                        else:
                            row['warnings'].append('transcription_unverified' if not pdf_quality.preserves_mechanics(
                                texts[number - 1], extra) else 'transcription_review')
                            report['derived_descriptions'][str(number)] = extra
                        row["warnings"].append("vision_review_required")

    review = []
    for i, text in enumerate(texts):
        row = report["pages"][i]
        unresolved = row["ai_repair"]["unresolved_labels"]
        if unresolved and not row.get('resumed'):
            text += "\n[PDF_UNRESOLVED_FIELDS: " + ",".join(unresolved) + "]"
            texts[i] = text
        if i + 1 in map_candidates and i + 1 not in maps:
            analysis = row.get('map_analysis')
            if analysis is None:
                analysis = pdf_map_analysis.not_analyzed()
                row['map_analysis'] = analysis
            warning = analysis['status'].lower()
            if warning not in row['warnings']:
                row['warnings'].append(warning)
        row["extracted_chars"] = len(text)
        row["selected_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        row['selected_text'] = text
        if not row.get('resumed'):
            status = row['layout_decision']['status']
            verified_image = row.get('image_transcription', {}).get('status') == 'authoritative'
            # A derived map failure cannot certify or invalidate canonical source.
            source_blocking_reasons: list[SourceBlockingReason] = []
            noncritical_asset = row.get('page_criticality', {}).get('source_critical') is False
            if status == 'needs_review' and not noncritical_asset:
                source_blocking_reasons.append('source_ordering_unverified')
            if (row.get('requires_image_transcription') and not verified_image
                    and not row.get('verified_illustration')
                    and row.get('page_criticality', {}).get('source_critical') is not False):
                source_blocking_reasons.append('source_image_transcription_unverified')
            if unresolved:
                source_blocking_reasons.append('source_mechanics_unresolved')
            if (not noncritical_asset and ('transcription_unverified' in row['warnings']
                    or ('transcription_review' in row['warnings'] and not text.strip()))):
                source_blocking_reasons.append('source_transcription_unverified')
            publication: PagePublication = {'disposition': _publication_disposition(
                'not_applicable' if noncritical_asset else status, bool(source_blocking_reasons)), 'source_blocking_reasons': source_blocking_reasons}
            row.update(publication)
        # Recheck the final canonical source even on reusable cached pages.
        if pdf_quality.has_corrupted_mechanics(text):
            reasons = row.setdefault('source_blocking_reasons', [])
            if 'source_mechanics_unresolved' not in reasons:
                reasons.append('source_mechanics_unresolved')
            row['disposition'] = 'needs_review'
        if not text.strip() and not row.get('verified_illustration') and i + 1 not in maps:
            row["warnings"].append("empty_page")
        # Candidate diagnostics remain available, but do not imply that the
        # source-preserving winning candidate still has the rejected defect.
        informational = {"native_two_columns", "layout_unavailable", "no_native_text_geometry"}
        candidate_only = {"layout_numeric_loss", "layout_text_loss", "layout_pair_mismatch",
                          "numeric_pair_review", "ocr_evidence_loss", "ocr_pair_review", "ocr_pair_mismatch"}
        accepted_order = row['layout_decision']['status'] == 'accepted'
        review_reasons = []
        for warning in row['warnings']:
            if row.get('page_criticality', {}).get('source_critical') is False and warning in {
                    'low_text', 'empty_page', 'vision_empty', 'vision_failed', 'transcription_review',
                    'transcription_unverified', 'vision_review_required', 'image_verification_budget_exhausted'}:
                continue
            if warning == 'table_or_character_grid' and row.get('graphic_evidence') and row.get('safe_short_native'):
                continue
            if warning in {'low_text', 'vision_empty', 'vision_failed', 'transcription_review',
                           'transcription_unverified', 'vision_review_required'} and (
                    row.get('safe_short_native') or row.get('verified_illustration')
                    or row.get('image_transcription', {}).get('status') == 'authoritative'):
                continue
            if warning in informational:
                continue
            if warning == 'low_text' and accepted_order and not row.get('graphic_evidence'):
                continue
            if (accepted_order and warning.startswith(
                    ('native:', 'layout:', 'layout ordering accepted:', 'layout docling failed:'))):
                continue
            if accepted_order and warning in {"ambiguous_columns", "unsupported_spanning_region"}:
                continue
            if warning in candidate_only and row['method'].split('+')[0] in {'native', 'multicolumn'}:
                continue
            if warning == 'local_ocr_repaired':
                continue
            if warning == 'local_ocr_review':
                resolved_blocks = {r['block_id'] for r in row['ai_repair']['regions'] if r['status'] == 'accepted'}
                if all(r['status'] == 'accepted' or r['block'] in resolved_blocks for r in row['local_repairs']):
                    continue
            if warning in {'source_pair_unresolved', 'ai_fields_unresolved'} and not unresolved:
                continue
            review_reasons.append(warning)
        map_soft_failure = bool(row.get('map_analysis', {}).get('candidate') and i + 1 not in maps)
        row['derived_feature_warnings'] = ([row['map_analysis']['status']] if map_soft_failure else [])
        criticality = row.get('page_criticality', {})
        if criticality.get('page_role') in {'OPTIONAL_PREGEN', 'OPTIONAL_HANDOUT'}:
            row['derived_feature_warnings'].append(criticality['page_role'] + '_QUARANTINED')
        severity: PublicationSeverity = ('HARD_BLOCK' if row['disposition'] == 'needs_review'
                                          else 'SOFT_REVIEW' if row['derived_feature_warnings'] or review_reasons else 'NONE')
        row['publication_severity'] = severity
        row['review_reasons'] = review_reasons
        if review_reasons:
            review.append(i + 1)
        if i and pdf_quality.continuation(texts[i - 1], text):
            report["continuations"].append({"from_page": i, "to_page": i + 1, "status": "candidate"})
    if (not any(t.strip() for t in texts)
            and not any(row.get('verified_illustration') for row in report['pages'])
            and not any(row['disposition'] == 'needs_review' for row in report['pages'])):
        for row in report['pages']:
            publication = {'disposition': 'needs_review',
                           'source_blocking_reasons': ['canonical_playable_source_missing']}
            row.update(publication, publication_severity='HARD_BLOCK')
            row['review_reasons'].append('canonical_playable_source_missing')
            if row['page'] not in review:
                review.append(row['page'])
    texts = pdf_admission.compose(texts, report['pages'])
    report['quarantined_pages'] = [row['page'] for row in report['pages']
                                   if row.get('source_authority') == 'QUARANTINED']
    full_text = render_source_pages(texts)
    discovery_ledger = layout_budget.get('source_discovery', {})
    def checkpoint_discovery(ledger: dict) -> None:
        layout_budget['source_discovery'] = ledger
        if layout_budget_checkpoint is not None:
            layout_budget_checkpoint(layout_budget)
    report['source_topology_discovery'] = pdf_source_topology_discovery.discover(full_text,
        ledger=discovery_ledger, checkpoint=checkpoint_discovery)
    report['source_topology_discovery_requests'] = report['source_topology_discovery']['requests']
    source_context = pdf_source_topology_discovery.source_context(full_text, report, pdf_sha256=report['pdf_sha256'])
    report['source_topology_discovery']['certification_status'] = (
        'DISCOVERY_NONE' if not report['source_topology_discovery']['candidates'] else
        'AWAITING_CANONICAL_SOURCE' if source_context.receipt() is None else 'ENDPOINT_UNRESOLVED')
    # Source topology can use only the final canonical book, after every source gate.
    # Draft overlays are rebuilt here, never reused against an earlier source identity.
    if not any(row['publication_severity'] == 'HARD_BLOCK' for row in report['pages']):
        for number, graph in list(maps.items()):
            row = report['pages'][number - 1]
            merged = pdf_map_analysis.certify_source_topology(graph, row['map_analysis'], full_text,
                source_context=source_context, candidates=report['source_topology_discovery']['candidates'])
            row['map_analysis'] = merged.analysis
            bindings = merged.analysis.get('source_topology_binding_statuses', [])
            row['source_topology_binding_statuses'] = bindings
            if merged.graph is not None:
                maps[number] = merged.graph
            else:
                maps.pop(number)
                report['derived_descriptions'].pop(str(number), None)
                row['publication_severity'] = 'SOFT_REVIEW'
                row['derived_feature_warnings'].append('MAP_GRAPH_INCOMPLETE')
                row['review_reasons'].append('source_topology_unverified')
                if number not in review:
                    review.append(number)
    candidate_outcomes = []
    for index, _ in enumerate(report['source_topology_discovery']['candidates']):
        statuses = [row['source_topology_binding_statuses'][index] for row in report['pages']
                    if index < len(row.get('source_topology_binding_statuses', []))]
        candidate_outcomes.append('CERTIFIED' if 'CERTIFIED' in statuses else
            statuses[0] if statuses else report['source_topology_discovery']['certification_status'])
    report['source_topology_discovery']['binding_statuses'] = candidate_outcomes
    report['source_topology_discovery']['certified_count'] = candidate_outcomes.count('CERTIFIED')
    report['source_topology_discovery']['pending_count'] = len(candidate_outcomes) - candidate_outcomes.count('CERTIFIED')
    if candidate_outcomes:
        report['source_topology_discovery']['certification_status'] = (
            'CERTIFIED' if all(status == 'CERTIFIED' for status in candidate_outcomes) else next(status for status in candidate_outcomes if status != 'CERTIFIED'))
    report["review_pages"] = review
    report["source_chars"] = len(full_text)
    report["ai_repair_requests"] = ai_repair_ledger.get("consumed_requests", 0) - ai_used_before
    report["local_ocr_attempts"] = max(0, local_ocr_limit) - local_budget[0]
    report["local_page_ocr_attempts"] = max(0, local_ocr_limit) - local_page_budget
    engine_attempts = [attempt for row in report['pages']
                       for attempt in (row.get('page_ocr_attempts', [])
                           + [a for repair in row['local_repairs'] for a in repair.get('ocr_attempts', [])])]
    for engine, name in [('paddleocr', 'paddle'), ('tesseract', 'tesseract')]:
        selected = [a for a in engine_attempts if a['engine'] == engine]
        report[f'local_{name}_attempts'] = len(selected)
        report[f'local_{name}_accepted'] = sum(a['status'] == 'accepted' for a in selected)
        report[f'local_{name}_rejected'] = sum(a['status'] == 'rejected' for a in selected)
        report[f'local_{name}_failed'] = sum(a['status'] in {'unavailable', 'error', 'empty'} for a in selected)
    report['tesseract_fallbacks'] = sum(
        any(a['engine'] == 'paddleocr' for a in group) and any(a['engine'] == 'tesseract' for a in group)
        for row in report['pages'] for group in ([row.get('page_ocr_attempts', [])]
            + [repair.get('ocr_attempts', []) for repair in row['local_repairs']]))
    report.update(pdf_image_transcription.metrics(report['pages']))
    report.update(pdf_map_analysis.metrics(report['pages']))
    report['layout_budget'] = layout_budget
    report['hard_block_pages'] = [row['page'] for row in report['pages'] if row['publication_severity'] == 'HARD_BLOCK']
    report['blocked_pages'] = list(report['hard_block_pages'])
    report['soft_review_pages'] = [row['page'] for row in report['pages'] if row['publication_severity'] == 'SOFT_REVIEW']
    readiness: ScenarioReadiness = ('BLOCKED' if report['hard_block_pages']
                                    else 'READY_WITH_WARNINGS' if report['soft_review_pages'] or review else 'READY')
    if readiness == 'READY' and report['source_topology_discovery']['pending_count']:
        readiness = 'READY_WITH_WARNINGS'
    feature_warnings: list[pdf_page_criticality.FeatureWarning] = []
    if report['quarantined_pages']:
        feature_warnings.append('source_review_quarantined')
    if any(row.get('page_criticality', {}).get('page_role') == 'OPTIONAL_PREGEN' for row in report['pages']):
        feature_warnings.append('optional_pregen_unavailable')
    if any(row.get('page_criticality', {}).get('page_role') == 'OPTIONAL_HANDOUT' for row in report['pages']):
        feature_warnings.append('optional_handout_unavailable')
    if report['source_topology_discovery']['pending_count'] or any('provider' in reason or 'budget' in reason
            for reason in report['source_topology_discovery']['diagnostics']):
        feature_warnings.append('topology_assistance_unavailable')
    report['feature_warnings'] = feature_warnings
    if readiness == 'READY' and feature_warnings:
        readiness = 'READY_WITH_WARNINGS'
    report['scenario_readiness'] = readiness
    report['map_status'] = {str(row['page']): row['map_analysis']['status']
                            for row in report['pages'] if row.get('map_analysis', {}).get('candidate')}
    result = (full_text, review, False, images, maps)
    if report['blocked_pages']:
        raise LayoutReviewRequired(report, result)
    return result


_PAGE_MARKER_RE = re.compile(r"^-*\s*第\s*\d+\s*頁\s*-*$")

# Filenames like "scan.pdf", "IMG_1234.pdf", "untitled.pdf" carry no real title info,
# so those fall through to the text-scanning guess below instead.
_GENERIC_FILENAME_RE = re.compile(
    r"^(img|image|scan|doc|document|file|untitled|new|download|pdf|劇本|文件|未命名)[\s_.-]*\d*$",
    re.IGNORECASE,
)


def _title_from_filename(file_name: str) -> str | None:
    stem = re.sub(r"\.pdf$", "", file_name.strip(), flags=re.IGNORECASE)
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem or _GENERIC_FILENAME_RE.match(stem):
        return None
    return stem


def guess_title(text: str, file_name: str = "", fallback: str = "未命名劇本") -> str:
    """Prefer the uploaded filename as the title — downloaded scenario PDFs are
    almost always named after the scenario itself, and that's far more reliable
    than scanning extracted text: professionally designed cover pages routinely
    scramble a stylized title across disconnected text fragments (a decorative
    drop-cap layout can extract as "ightless / The / B / L / eacon" instead of
    "The Lightless Beacon"), while a plain author byline nearby extracts cleanly
    and gets mistaken for the title. Only fall back to scanning the text itself
    when the filename is missing or looks generic."""
    from_filename = _title_from_filename(file_name) if file_name else None
    if from_filename:
        return from_filename

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _PAGE_MARKER_RE.match(line):
            continue
        if 2 <= len(line) <= 40:
            return line
    return fallback


def extract_preview(pdf_bytes: bytes, page_limit: int = 3) -> str:
    """Fast, no-LLM preview used before a potentially expensive full parse."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    parts: list[str] = []
    for i, page in enumerate(cast(Iterable[Any], doc)):
        if i >= page_limit:
            break
        text = re.sub(r"[ \t]+", " ", page.get_text("text") or "").strip()
        if text:
            parts.append(f"--- 第 {i + 1} 頁 ---\n{text}")
    if not parts:
        raise ValueError("這份 PDF 的前幾頁無法抽取文字，無法快速比對")
    return "\n\n".join(parts)


def combine_pdfs(pdf_parts: list[bytes]) -> bytes:
    """Combine uploaded parts before sending them through the normal pipeline."""
    if not pdf_parts:
        raise ValueError("沒有可合併的 PDF")
    if len(pdf_parts) == 1:
        return pdf_parts[0]
    output = pymupdf.open()
    merged_toc: list[list] = []
    page_offset = 0
    try:
        for payload in pdf_parts:
            source = pymupdf.open(stream=payload, filetype="pdf")
            try:
                output.insert_pdf(source)
                for level, title, page_number in source.get_toc(simple=True):
                    merged_toc.append([level, title, page_number + page_offset])
                page_offset += source.page_count
            finally:
                source.close()
        if merged_toc:
            output.set_toc(merged_toc)
        return output.tobytes()
    finally:
        output.close()
