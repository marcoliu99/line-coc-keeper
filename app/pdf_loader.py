"""Extract plain text from an uploaded scenario PDF.

COC7e scenario PDFs are rarely plain text — they usually mix body copy with maps,
handouts, and stat-block graphics on the same page ("文圖並茂"). PyMuPDF (fitz) is
used instead of a bare text-layer reader because it keeps reading order sane on
multi-column/graphic layouts, and because it lets us render a page to an image for
the two graphic-page fallbacks below.

Floor plans and maps are a specific, real failure mode of plain text extraction:
room-name labels are positioned in 2D on the page, but a text-layer reader can
only emit them as a flat 1D sequence, so "the room to the right of the entrance"
routinely comes out as an unordered list of room names with no spatial relationship
between them — a Keeper reading that will genuinely guess wrong about which room
is where (confirmed in play: a player entering a door expected the bedroom on the
right, the Keeper — reading only the scrambled label order — placed the kitchen
there instead). Vision description (_vision_describe_image) is the fix: handing
the actual page image to a vision-capable Claude call and asking it to describe
spatial layout directly solves this, whereas OCR (_ocr_image) only recovers text
that isn't already selectable and still loses the spatial arrangement.
"""
from __future__ import annotations

import concurrent.futures
import io
import re

import pymupdf

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MAX_SCENARIO_CHARS
from app.scene_map import extract_scene_map

# Below this many extracted characters, a page that also contains an image is
# treated as "probably graphic content" (handout/map/cover) and gets a
# vision-description or OCR pass. 200 rather than a tighter cutoff: checked
# against a real scenario PDF, its graphic pages (pregen sheets, floor plans,
# illustrations) topped out at 137 chars while the shortest genuine narrative
# page had 369 — plenty of margin either side of that gap. The two floor-plan
# pages that actually caused a real in-game room-layout mistake had 53 and 70
# chars each, comfortably past the old 40-char cutoff undetected.
_LOW_TEXT_THRESHOLD = 200

# How many low-text pages to describe concurrently. A scenario can easily have
# 15-20+ such pages (character sheets, maps, illustrations); doing them one at
# a time risked the whole upload taking minutes — long enough that a LINE reply
# token would expire before the "scenario loaded" confirmation ever went out.
# 6 workers against a real 24-page batch still took ~60s wall time; 12 is the
# next thing to try if that's still too slow in practice.
_MAX_CONCURRENT_PAGE_CALLS = 12

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


def _render_page_png(page: "pymupdf.Page", dpi: int = 200) -> bytes:
    return page.get_pixmap(dpi=dpi).tobytes("png")


def _ocr_image(png_bytes: bytes) -> str:
    """Best-effort OCR of a page image. Returns "" if OCR isn't available/fails
    (missing pytesseract, missing the tesseract binary, missing language pack, ...).
    Recovers text-in-image content, but — unlike _vision_describe_image — has no
    way to reconstruct the spatial relationships between what it reads.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        image = Image.open(io.BytesIO(png_bytes))
        return pytesseract.image_to_string(image, lang="chi_tra+eng").strip()
    except Exception:
        return ""


def _vision_describe_image(png_bytes: bytes) -> str:
    """Best-effort: ask Claude to describe a page image, emphasizing spatial
    layout when it looks like a floor plan or map. Returns "" if unavailable
    (no ANTHROPIC_API_KEY — this is independent of LLM_PROVIDER, since Gemini
    isn't wired up for this yet — or the call fails for any reason), so callers
    should fall back to plain OCR.
    """
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        import base64

        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        image_b64 = base64.standard_b64encode(png_bytes).decode("utf-8")
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            # Generous on purpose: this prompt (asking for careful spatial
            # reasoning about a floor plan) reliably triggers extended thinking
            # on claude-sonnet-5, and 1024 was observed being fully consumed by
            # the thinking block alone — stop_reason "max_tokens" with no
            # TextBlock at all, so the caller silently got "" back. 4096 leaves
            # room for both the thinking and the actual answer.
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()
    except Exception:
        return ""


def _describe_graphic_page(png_bytes: bytes) -> str:
    """Vision description first (handles spatial layout), OCR as a fallback
    (no ANTHROPIC_API_KEY, or the vision call failed for any reason)."""
    return _vision_describe_image(png_bytes) or _ocr_image(png_bytes)


def extract_text(pdf_bytes: bytes) -> tuple[str, list[int], bool, dict[int, bytes], dict[int, dict]]:
    """Extract scenario text.

    Returns (full_text, low_text_pages, truncated, page_images, page_maps):
    - low_text_pages: 1-indexed pages that had little extractable text despite
      containing images — likely a handout, map, or heavily-styled page whose
      content may not be fully captured.
    - truncated: True if the scenario exceeded MAX_SCENARIO_CHARS and everything
      past that cut-off point was dropped.
    - page_images: 1-indexed page number -> rendered PNG bytes, for every page in
      low_text_pages (the maps/handouts/character-sheet pages already rendered
      for vision/OCR here). Lets a caller show a player the actual picture
      instead of just the Keeper's text description of it — see /coc showpage
      and the show_scenario_image tool in app/keeper.py.
    - page_maps: 1-indexed page number -> structured room-graph dict (see
      app/scene_map.py), for whichever low_text_pages turned out to actually be
      a floor plan/map (most won't be — character sheets and illustrations are
      also low-text pages, extract_scene_map returns None for those and they're
      just not in this dict). This is a second vision call per low-text page on
      top of the existing prose description below, since there's no way to know
      a page is a map without asking; real added cost, only pays off for
      scenarios that actually have floor plans.
    Callers should surface low_text_pages/truncated to the uploader so nothing
    silently goes missing.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page_texts: list[str] = []
    low_text_pages: list[int] = []
    pending: dict[int, bytes] = {}  # page index -> rendered PNG, needs vision/OCR

    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        has_images = len(page.get_images()) > 0
        if len(text) < _LOW_TEXT_THRESHOLD and has_images:
            low_text_pages.append(i + 1)
            pending[i] = _render_page_png(page)

        page_texts.append(text)

    page_maps: dict[int, dict] = {}

    if pending:
        workers = min(_MAX_CONCURRENT_PAGE_CALLS, len(pending) * 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            desc_futures = {
                executor.submit(_describe_graphic_page, png_bytes): idx
                for idx, png_bytes in pending.items()
            }
            map_futures = {
                executor.submit(extract_scene_map, png_bytes): idx
                for idx, png_bytes in pending.items()
            }
            for future in concurrent.futures.as_completed(desc_futures):
                idx = desc_futures[future]
                extra = future.result()
                if extra:
                    page_texts[idx] = f"{page_texts[idx]}\n{extra}".strip() if page_texts[idx] else extra
            for future in concurrent.futures.as_completed(map_futures):
                idx = map_futures[future]
                scene_map = future.result()
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

    page_images = {idx + 1: png_bytes for idx, png_bytes in pending.items()}
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
