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
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from importlib import metadata
from typing import Any, Literal, TypedDict, cast

import pymupdf

from app import config, pdf_ai_repair, pdf_layout, pdf_layout_adapters, pdf_quality
from app.markitdown_shim import build_markitdown
from app.scene_map import analyze_page_image

_logger = logging.getLogger(__name__)

PIPELINE_VERSION = 'multicolumn-v2'
RENDERER_VERSION = 1
PdfExtraction = tuple[str, list[int], bool, dict[int, bytes], dict[int, dict]]
PageDisposition = Literal['accepted', 'needs_review', 'legacy_route']


class PagePublication(TypedDict):
    disposition: PageDisposition


def _publication_disposition(status: pdf_layout.LayoutStatus, failed_graphic: bool) -> PageDisposition:
    if status == 'needs_review' or failed_graphic:
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
            'quality_version': pdf_quality.VERSION, 'layout_version': pdf_layout.PIPELINE_VERSION,
            'pymupdf': version('PyMuPDF'), 'pymupdf4llm': version('pymupdf4llm'),
            'markitdown': version('markitdown'),
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
            or row.get('page') != number or row.get('disposition') not in {'accepted', 'legacy_route'}
            or cached.get('selected_sha256') != hashlib.sha256(text.encode()).hexdigest()):
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
    """Best-effort OCR of a page image. Returns "" if OCR isn't available/fails
    (missing pytesseract, missing the tesseract binary, missing language pack, ...).
    Recovers text-in-image content, but — unlike _analyze_graphic_page — has no
    way to reconstruct the spatial relationships between what it reads.
    """
    languages = ("chi_tra+eng", "eng")
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        pytesseract = None
        Image = None  # type: ignore[assignment]

    if pytesseract is not None and Image is not None:
        try:
            image = Image.open(io.BytesIO(png_bytes))
            for lang in languages:
                text = pytesseract.image_to_string(image, lang=lang).strip()
                if text:
                    return text
        except Exception:  # OCR libraries have version-specific failures; fallback below is intentional.
            _logger.debug("pytesseract image OCR failed", exc_info=True)

    tesseract = shutil.which("tesseract")
    if not tesseract:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp.write(png_bytes)
        tmp.flush()
        for lang in languages:
            try:
                result = subprocess.run(
                    [tesseract, tmp.name, "stdout", "-l", lang, "--psm", "6"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                _logger.debug("tesseract subprocess failed for language %s", lang, exc_info=True)
                continue
            text = (result.stdout or "").strip()
            if text:
                return text
    return ""


def _analyze_graphic_page(png_bytes: bytes) -> tuple[str, dict | None]:
    """One combined vision call (scene_map.analyze_page_image — see its own
    docstring for why this used to be two separate API calls per page)
    handles both the prose description (spatial layout for maps, exhaustive
    number transcription for character sheets, brief description otherwise)
    and, when the page turns out to be a map, the structured room graph.
    Falls back to local OCR for the text half only if the vision call
    produced nothing (no ANTHROPIC_API_KEY, or the call failed) — there's no
    fallback for the map half, a page just won't get one."""
    description, scene_map = analyze_page_image(png_bytes)
    return description or _ocr_image(png_bytes), scene_map


_MARKITDOWN_PAGE_RE = re.compile(r"^##\s*Page\s+(\d+)\s*$", re.MULTILINE)


def _markitdown_page_texts(pdf_bytes: bytes, page_numbers: list[int] | None = None) -> dict[int, str] | None:
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
    md = build_markitdown(_VISION_PROMPT)
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

    pages: dict[int, str] = {}
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
        attempt = {"block": block["id"], "bbox": block["bbox"], "original": original,
                   "ocr_text": "", "status": "review_required"}
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
            candidate = pdf_quality.normalize(_ocr_image(png))
        except Exception:  # noqa: BLE001 - local optional OCR never discards source.
            attempt["status"] = "ocr_failed"
            continue
        attempt["ocr_text"] = candidate
        local_pairs = [p for p in pairs if p["block"] == block["id"]]
        attempt["pair_checks"] = pdf_quality.check_pairs(local_pairs, candidate)
        if not candidate:
            attempt["status"] = "ocr_empty"
        elif text.count(original) == 1 and pdf_quality.accept_region(original, candidate, local_pairs):
            text = text.replace(original, candidate, 1)
            attempt["status"] = "accepted"
    return text, attempts


def extract_text(pdf_bytes: bytes, *, quality_report: dict | None = None, local_ocr_limit: int = 8,
                 ai_repair_limit: int = 8, resume_pages: dict[int, dict] | None = None,
                 layout_budget: dict | None = None,
                 layout_budget_checkpoint: Callable[[dict], None] | None = None) -> PdfExtraction:
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
    ai_budget = [max(0, ai_repair_limit)]
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
            text, repairs = _repair_local_regions(page, evidence, pairs, text, local_budget)
            if repairs:
                warnings.append("local_ocr_review" if any(r["status"] != "accepted" for r in repairs) else "local_ocr_repaired")
                if any(r["status"] == "accepted" for r in repairs):
                    method += "+local_ocr"
            graphic = _page_has_graphic_content(page) or _pymupdf4llm_has_graphic_evidence(chunk)
            if graphic:
                images[number] = _render_page_png(page)
            # Readable labels do not establish a floor plan's spatial graph.
            map_text = re.sub(r"[·_]+", " ", native + "\n" + layout_text)
            if graphic and re.search(r"(?i)\bfloor\s*plan\b|\binvestigator\s+map\b|平面圖|樓層圖", map_text):
                map_candidates.add(number)
                pending[number] = images[number]
            if len(text) < _LOW_TEXT_THRESHOLD:
                warnings.append("low_text")
                if graphic:
                    pending[number] = images[number]
            texts.append(text)
            report["pages"].append({"page": number, "method": method, "native_chars": len(native),
                                    "warnings": warnings, "evidence": evidence,
                                    "numeric_pairs": pairs, "layout_pair_checks": pair_checks, "local_repairs": repairs,
                                    "candidates": {"native": native, "layout": layout_text},
                                    "layout_decision": decision, "graphic_evidence": graphic})
        # Only pages lacking usable text go through the potentially paid OCR
        # adapter. Already readable layout pages never trigger whole-book OCR.
        alternate = _markitdown_page_texts(pdf_bytes, sorted(pending)) if pending else None
        for number in list(pending):
            extra = (alternate or {}).get(number, "").strip()
            if extra:
                row = report["pages"][number - 1]
                row["candidates"]["markitdown"] = extra
                checks = pdf_quality.check_pairs(row["numeric_pairs"], extra)
                row["ocr_pair_checks"] = checks
                if any(p["status"] != "matched" for p in checks):
                    row["warnings"].append("ocr_pair_review")
                chosen, method, warnings = pdf_quality.select_text(texts[number - 1], extra)
                if any(p["status"] == "pair_mismatch" for p in checks):
                    method = "native"
                    row["warnings"].append("ocr_pair_mismatch")
                if method == "layout":
                    texts[number - 1] = chosen
                    report["pages"][number - 1]["method"] = "markitdown"
                else:
                    report["pages"][number - 1]["warnings"].append("ocr_evidence_loss")
                if len(texts[number - 1]) >= _LOW_TEXT_THRESHOLD and number not in map_candidates:
                    pending.pop(number)

        # Repair only remaining numeric/corrupted blocks, before extraction consumers.
        for i, row in enumerate(report["pages"]):
            if row.get('resumed'):
                continue
            texts[i], ai_result = pdf_ai_repair.repair_page(doc[i], row, texts[i], ai_budget)
            row["ai_repair"] = ai_result
            if any(r["status"] == "accepted" for r in ai_result["regions"]):
                row["method"] += "+ai_repair"
                if len(texts[i]) >= _LOW_TEXT_THRESHOLD and i + 1 not in map_candidates:
                    pending.pop(i + 1, None)
            if ai_result["unresolved_labels"]:
                row["warnings"].append("ai_fields_unresolved")

        if pending:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENT_PAGE_CALLS, len(pending))) as executor:
                futures = {executor.submit(_analyze_graphic_page, png): number for number, png in pending.items()}
                for future in concurrent.futures.as_completed(futures):
                    number = futures[future]
                    row = report["pages"][number - 1]
                    try:
                        extra, scene_map = future.result()
                    except Exception:  # noqa: BLE001 - retain other pages and record this failed fallback.
                        row["warnings"].append("vision_failed")
                        continue
                    if scene_map:
                        maps[number] = scene_map
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
                            report["derived_descriptions"][str(number)] = extra
                            # A labeled derived section is not a verbatim quote.
                            texts[number - 1] += "\n[地圖視覺解讀，非原文轉錄]\n" + extra
                        else:
                            texts[number - 1] = (texts[number - 1] + "\n[影像轉錄／描述]\n" + extra).strip()
                        row["warnings"].append("vision_review_required")
                    if not scene_map and number in map_candidates:
                        row["warnings"].append("floor_plan_graph_missing")

    review = []
    for i, text in enumerate(texts):
        row = report["pages"][i]
        unresolved = row["ai_repair"]["unresolved_labels"]
        if unresolved and not row.get('resumed'):
            text += "\n[PDF_UNRESOLVED_FIELDS: " + ",".join(unresolved) + "]"
            texts[i] = text
        row["extracted_chars"] = len(text)
        row["selected_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        row['selected_text'] = text
        if not row.get('resumed'):
            status = row['layout_decision']['status']
            failed_graphic = (row.get('graphic_evidence') and len(text.strip()) < _LOW_TEXT_THRESHOLD
                              and any(w in {'vision_failed', 'vision_empty', 'vision_pair_mismatch'}
                                      for w in row['warnings']))
            publication: PagePublication = {'disposition': _publication_disposition(
                status, bool(failed_graphic) or (i + 1 in map_candidates and i + 1 not in maps))}
            row.update(publication)
        if not text.strip():
            row["warnings"].append("empty_page")
        # Candidate diagnostics remain available, but do not imply that the
        # source-preserving winning candidate still has the rejected defect.
        informational = {"native_two_columns", "layout_unavailable", "no_native_text_geometry"}
        candidate_only = {"layout_numeric_loss", "layout_text_loss", "layout_pair_mismatch",
                          "numeric_pair_review", "ocr_evidence_loss", "ocr_pair_review", "ocr_pair_mismatch"}
        accepted_order = row['layout_decision']['status'] == 'accepted'
        review_reasons = []
        for warning in row['warnings']:
            if warning in informational:
                continue
            if warning == 'low_text' and accepted_order and not row.get('graphic_evidence'):
                continue
            if (accepted_order and warning.startswith(
                    ('native:', 'layout:', 'layout ordering accepted:', 'layout docling failed:'))):
                continue
            if accepted_order and warning in {"ambiguous_columns", "unsupported_spanning_region"}:
                continue
            if warning in candidate_only and row['method'] in {'native', 'multicolumn'}:
                continue
            review_reasons.append(warning)
        row['review_reasons'] = review_reasons
        if review_reasons:
            review.append(i + 1)
        if i and pdf_quality.continuation(texts[i - 1], text):
            report["continuations"].append({"from_page": i, "to_page": i + 1, "status": "candidate"})
    if not any(t.strip() for t in texts) and not any(row['disposition'] == 'needs_review' for row in report['pages']):
        raise ValueError("這份 PDF 抽不出任何文字內容；請確認 OCR 是否可用並檢查原稿。")
    full_text = render_source_pages(texts)
    report["review_pages"] = review
    report["source_chars"] = len(full_text)
    report["ai_repair_requests"] = max(0, ai_repair_limit) - ai_budget[0]
    report["local_ocr_attempts"] = max(0, local_ocr_limit) - local_budget[0]
    report['layout_budget'] = layout_budget
    report['blocked_pages'] = [row['page'] for row in report['pages'] if row['disposition'] == 'needs_review']
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
