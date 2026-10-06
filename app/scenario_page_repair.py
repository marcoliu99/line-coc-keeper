"""Overwrite whole physical pages of the loaded scenario text from an uploaded ``repair_*.md`` file.

The file uses the same page markers as the scenario text (``--- 第 N 頁 ---``) followed by the complete corrected text
of that page, so a page re-extracted by ChatGPT, Gemini or any other tool can be pasted in as it is. Like a ``role_*.md``
card it only changes the running game: nothing is published to the scenario library.
"""
from __future__ import annotations

import re

from app import scenario_library as library

_FENCE = re.compile(r"^```[\w-]*[ \t]*$")


class PageRepairError(ValueError):
    """The upload cannot be applied; the message says why and is safe to show to the player."""


def parse_pages(content: str) -> dict[int, str]:
    """``{physical page: complete corrected text}``; anything before the first marker (a heading, a note) is ignored."""
    lines = content.lstrip("\ufeff").replace("\r\n", "\n").strip().split("\n")
    first = next((i for i, line in enumerate(lines) if library.PAGE_MARKER_RE.match(line)), None)
    if first is not None:  # the whole answer pasted inside a code block, possibly after a heading or a note
        opening = next((i for i in range(first - 1, -1, -1) if lines[i].strip()), None)
        if opening is not None and _FENCE.match(lines[opening].strip()):
            del lines[opening]
            if lines and _FENCE.match(lines[-1].strip()):
                lines.pop()
    text = "\n".join(lines)
    marks = list(library.PAGE_MARKER_RE.finditer(text))
    if not marks:
        raise PageRepairError("找不到頁碼標記，每一頁請以「--- 第 N 頁 ---」開頭，後面接該頁完整的修正後文字。")
    pages: dict[int, str] = {}
    for i, mark in enumerate(marks):
        page = int(mark.group(1))
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end():end].strip()
        if page < 1 or page in pages:
            raise PageRepairError(f"第 {page} 頁的標記不正確或重複，每個實體頁碼只能出現一次。")
        if not body:
            raise PageRepairError(f"第 {page} 頁沒有內容。")
        pages[page] = body
    return pages


def present_pages(scenario_text: str) -> set[int]:
    """The physical pages the text holds; a chapter window of a long scenario holds only some of them."""
    return {int(m.group(1)) for m in library.PAGE_MARKER_RE.finditer(scenario_text)}


def apply_pages(scenario_text: str, pages: dict[int, str]) -> str:
    """Replace the listed pages the text holds; every other byte is kept, and a page outside the text is skipped."""
    marks = list(library.PAGE_MARKER_RE.finditer(scenario_text))
    numbers = [int(m.group(1)) for m in marks]
    if not marks or numbers != sorted(set(numbers)):
        raise PageRepairError("目前劇本沒有完整的實體頁碼標記，無法替換頁面。")
    index = {number: i for i, number in enumerate(numbers)}
    pieces, cursor = [], 0
    for page in sorted(pages):
        if page not in index:
            continue
        i = index[page]
        start = marks[i].end() + (1 if scenario_text.startswith("\n", marks[i].end()) else 0)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(scenario_text)
        if i + 1 < len(marks):  # the newline or blank line before the next marker stays
            end -= 2 if end - 2 >= start and scenario_text[end - 2:end] == "\n\n" else int(end - 1 >= start)
        pieces += [scenario_text[cursor:start], pages[page]]
        cursor = max(start, end)
    pieces.append(scenario_text[cursor:])
    return "".join(pieces)
