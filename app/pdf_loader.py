"""Extract plain text from an uploaded scenario PDF.

COC7e scenario PDFs are rarely plain text — they usually mix body copy with maps,
handouts, and stat-block graphics on the same page ("文圖並茂"). PyMuPDF (fitz) is
used instead of a bare text-layer reader because it keeps reading order sane on
multi-column/graphic layouts, and because it lets us render a page to an image so
we can OCR pages that turn out to be mostly graphics (e.g. a handout whose text was
baked into an image rather than kept as selectable text).
"""
from __future__ import annotations

import io
import re

import pymupdf

from app.config import MAX_SCENARIO_CHARS

# Below this many extracted characters, a page that also contains an image is
# treated as "probably graphic content" (handout/map/cover) and gets an OCR pass.
_LOW_TEXT_THRESHOLD = 40


def _ocr_page(page: "pymupdf.Page") -> str:
    """Best-effort OCR of a page image. Returns "" if OCR isn't available/fails
    (missing pytesseract, missing the tesseract binary, missing language pack, ...).
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        pix = page.get_pixmap(dpi=200)
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(image, lang="chi_tra+eng").strip()
    except Exception:
        return ""


def extract_text(pdf_bytes: bytes) -> tuple[str, list[int], bool]:
    """Extract scenario text.

    Returns (full_text, low_text_pages, truncated):
    - low_text_pages: 1-indexed pages that had little extractable text despite
      containing images — likely a handout, map, or heavily-styled page whose
      content may not be fully captured.
    - truncated: True if the scenario exceeded MAX_SCENARIO_CHARS and everything
      past that cut-off point was dropped.
    Callers should surface both to the uploader so nothing silently goes missing.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    parts: list[str] = []
    low_text_pages: list[int] = []

    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        has_images = len(page.get_images()) > 0
        if len(text) < _LOW_TEXT_THRESHOLD and has_images:
            low_text_pages.append(i + 1)
            ocr_text = _ocr_page(page)
            if ocr_text:
                text = f"{text}\n{ocr_text}".strip() if text else ocr_text

        if text:
            parts.append(f"--- 第 {i + 1} 頁 ---\n{text}")

    full_text = "\n\n".join(parts).strip()
    if not full_text:
        raise ValueError(
            "這份 PDF 抽不出任何文字內容（可能整份都是掃描圖片，且沒有安裝 OCR，"
            "或本機沒有裝 tesseract）"
        )
    truncated = len(full_text) > MAX_SCENARIO_CHARS
    if truncated:
        full_text = full_text[:MAX_SCENARIO_CHARS] + "\n\n[...劇本內容過長，已截斷...]"
    return full_text, low_text_pages, truncated


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
