"""Extract plain text from an uploaded scenario PDF.

COC7e scenario PDFs are rarely plain text — they usually mix body copy with maps,
handouts, and stat-block graphics on the same page ("文圖並茂"). PyMuPDF4LLM is
the layout-aware page parser when installed: it preserves per-page Markdown,
picture/table/graphic evidence, and reading order. MarkItDown (+ the
markitdown-ocr plugin, see app/markitdown_shim.py) remains the embedded-image
OCR and compatibility fallback. PyMuPDF (fitz) stays in the loop for rendering
the whole page and as the final text fallback. The semantic labels (map,
handout, character sheet, illustration) still come from structural evidence,
OCR text, and Vision; PyMuPDF4LLM supplies the evidence rather than guessing
those labels by itself.

Floor plans and maps are a specific, real failure mode of plain text extraction:
room-name labels are positioned in 2D on the page, but a text-layer reader can
only emit them as a flat 1D sequence, so "the room to the right of the entrance"
routinely comes out as an unordered list of room names with no spatial relationship
between them — a Keeper reading that will genuinely guess wrong about which room
is where (confirmed in play: a player entering a door expected the bedroom on the
right, the Keeper — reading only the scrambled label order — placed the kitchen
there instead). This is exactly why the graphic-page fallback below still renders
the *whole page* to an image and asks Claude to describe spatial layout directly,
rather than relying on markitdown-ocr's embedded-image detection alone: a
vector-drawn floor plan (lines/rectangles, not a raster image object) has no
"image" for markitdown-ocr's pdfplumber-based detection to find, so it would
silently fall through untouched — this fallback is what actually catches it,
and stays wired to a whole-page-render regardless of what the text layer found.
Vision description (_analyze_graphic_page) is the fix: handing the actual page
image to a vision-capable Claude call and asking it to describe spatial layout
directly solves this, whereas OCR (_ocr_image) only recovers text that isn't
already selectable and still loses the spatial arrangement.
"""
from __future__ import annotations

import concurrent.futures
import io
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, cast

import pymupdf

from app.config import MAX_SCENARIO_CHARS
from app.markitdown_shim import build_markitdown
from app.scene_map import analyze_page_image

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
    "這是一份 COC7e 桌上角色扮演遊戲劇本 PDF 裡的一頁圖片，請判斷它屬於下面三種情況的哪一種：\n\n"
    "1. 如果是平面圖或地圖：詳細描述空間佈局與相對位置關係（例如：從正門進入後，右手邊第一個房間是"
    "什麼、左手邊是什麼、走廊盡頭是什麼、樓上/樓下有哪些房間），盡量具體、按方位描述，方便之後主持人"
    "依此正確描述場景給玩家，不要弄錯房間的相對位置。\n\n"
    "2. 如果是調查員角色卡／數值卡（有 STR/DEX/CON/APP/POW/SIZ/EDU/INT 等屬性欄位、HP/MP/SAN、"
    "或一排排技能名稱與百分比數字）：**逐一列出每一個看得到數字的欄位**，屬性、HP/MP/SAN/Luck、每一項"
    "技能的名稱與百分比都要完整列出來，不要只說「列出了完整技能」卻不寫出實際數字，這種摘要方式完全"
    "沒用；同時也要抄錄卡片上手寫或印刷填好的個人背景欄位，特別是角色姓名、職業、個人特質、信念、"
    "重要他人、珍藏物品，以及任何「角色扮演鉤子／秘密目標／Your goal」之類只屬於這個角色自己的動機"
    "段落，一字不漏抄下來；欄位是空白的就不用提。\n\n"
    "3. 如果只是插圖、封面、人物肖像等跟上面兩種都無關的內容：簡短描述畫面內容就好（一兩句話）。\n\n"
    "只描述圖片裡實際看到的內容，不要編造或推測沒看到的細節。"
)


def _page_has_graphic_content(page: "pymupdf.Page") -> bool:
    """True if this page has an embedded raster image OR at least
    _MIN_VECTOR_DRAWINGS vector drawing primitives (lines/rectangles/curves).
    The raster check alone (page.get_images()) misses a floor plan drawn
    entirely out of vector shapes — see _MIN_VECTOR_DRAWINGS' own comment for
    why that used to mean such a page was never even rendered for the
    vision/OCR fallback below, not just misclassified once it got there."""
    if page.get_images():
        return True
    return len(page.get_drawings()) >= _MIN_VECTOR_DRAWINGS


def _render_page_png(page: "pymupdf.Page", dpi: int = 200) -> bytes:
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
        except Exception:
            pass

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
            except Exception:
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


def _markitdown_page_texts(pdf_bytes: bytes) -> dict[int, str] | None:
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
    md = build_markitdown(_VISION_PROMPT)
    if md is None:
        return None
    try:
        from markitdown import StreamInfo

        result = md.convert(io.BytesIO(pdf_bytes), stream_info=StreamInfo(extension=".pdf"))
        text = result.text_content
    except Exception:
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
        pages[page_num] = page_text
    return pages


def _pymupdf4llm_page_chunks(pdf_bytes: bytes) -> dict[int, dict] | None:
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
    except Exception:
        # pymupdf4llm imports pymupdf.layout, which can eagerly load an
        # optional ONNX model. A broken/missing model is a parser capability
        # problem, not a reason to reject an otherwise readable PDF.
        return None

    doc = None
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            write_images=False,
            embed_images=False,
        )
    except Exception:
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


def extract_text(pdf_bytes: bytes) -> tuple[str, list[int], bool, dict[int, bytes], dict[int, dict]]:
    """Extract scenario text.

    Returns (full_text, low_text_pages, truncated, page_images, page_maps):
    - low_text_pages: 1-indexed pages that had little extractable text despite
      containing images — likely a handout, map, or heavily-styled page whose
      content may not be fully captured.
    - truncated: True if the scenario exceeded MAX_SCENARIO_CHARS and everything
      past that cut-off point was dropped.
    - page_images: 1-indexed page number -> rendered PNG bytes, for every page
      with graphic content (including pages whose OCR/MarkItDown text is long).
      This is deliberately independent from low_text_pages: callers can show
      the actual map/handout/character sheet even when OCR already supplied a
      lot of text — see /coc showpage and show_scenario_image.
    - page_maps: 1-indexed page number -> structured room-graph dict (see
      app/scene_map.py), for whichever low_text_pages turned out to actually be
      a floor plan/map (most won't be — character sheets and illustrations are
      also low-text pages, scene_map.analyze_page_image returns None for those
      and they're just not in this dict). Comes from the same single vision
      call as the prose description below (see analyze_page_image's own
      docstring on why this used to be two separate calls per low-text page).
    Callers should surface low_text_pages/truncated to the uploader so nothing
    silently goes missing.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    layout_pages = _pymupdf4llm_page_chunks(pdf_bytes)
    markitdown_pages = _markitdown_page_texts(pdf_bytes)  # dict[int, str] or None — see that function's docstring

    page_texts: list[str] = []
    low_text_pages: list[int] = []
    page_images: dict[int, bytes] = {}
    vision_pending: dict[int, bytes] = {}  # page index -> low-text PNG for Vision/OCR

    for i, page in enumerate(cast(Iterable[Any], doc)):
        page_number = i + 1
        layout_text = _pymupdf4llm_page_text(layout_pages.get(page_number) if layout_pages else None)
        if markitdown_pages is not None and page_number in markitdown_pages:
            text = markitdown_pages[page_number]
        elif layout_text:
            text = layout_text
        else:
            text = page.get_text("text") or ""
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

        # Image persistence and whole-page Vision are separate decisions.
        # MarkItDown/OCR can make a character sheet exceed the text threshold;
        # that must skip the redundant Vision call, not discard the image.
        has_graphic_content = _page_has_graphic_content(page) or _pymupdf4llm_has_graphic_evidence(
            layout_pages.get(page_number) if layout_pages else None
        )
        if has_graphic_content:
            png_bytes = _render_page_png(page)
            page_images[page_number] = png_bytes
            if len(text) < _LOW_TEXT_THRESHOLD:
                low_text_pages.append(page_number)
                vision_pending[i] = png_bytes

        page_texts.append(text)

    page_maps: dict[int, dict] = {}

    if vision_pending:
        workers = min(_MAX_CONCURRENT_PAGE_CALLS, len(vision_pending))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            analyze_futures = {
                executor.submit(_analyze_graphic_page, png_bytes): idx
                for idx, png_bytes in vision_pending.items()
            }
            for future in concurrent.futures.as_completed(analyze_futures):
                idx = analyze_futures[future]
                extra, scene_map = future.result()
                if extra:
                    page_texts[idx] = f"{page_texts[idx]}\n{extra}".strip() if page_texts[idx] else extra
                if scene_map:
                    page_maps[idx + 1] = scene_map

    parts = [f"--- 第 {i + 1} 頁 ---\n{t}" for i, t in enumerate(page_texts) if t]
    full_text = "\n\n".join(parts).strip()
    if not full_text:
        raise ValueError(
            "這份 PDF 抽不出任何文字內容（可能整份都是掃描圖片，且沒有安裝 OCR，"
            "或本機沒有裝 tesseract）"
        )
    truncated = len(full_text) > MAX_SCENARIO_CHARS
    if truncated:
        full_text = full_text[:MAX_SCENARIO_CHARS] + "\n\n[...劇本內容過長，已截斷...]"

    return full_text, low_text_pages, truncated, page_images, page_maps


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
