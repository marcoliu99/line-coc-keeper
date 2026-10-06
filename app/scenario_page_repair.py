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
    lines = content.lstrip("﻿").replace("\r\n", "\n").strip().split("\n")
    if lines and _FENCE.match(lines[0].strip()):  # the whole answer pasted inside a code block
        lines = lines[1:]
    if lines and _FENCE.match(lines[-1].strip()):
        lines = lines[:-1]
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


def apply_pages(scenario_text: str, pages: dict[int, str]) -> str:
    """Replace only the listed pages; every other byte of the scenario text is kept as it was."""
    marks = list(library.PAGE_MARKER_RE.finditer(scenario_text))
    count = len(marks)
    if not count or [int(m.group(1)) for m in marks] != list(range(1, count + 1)):
        raise PageRepairError("目前劇本沒有完整的實體頁碼標記，無法替換頁面。")
    outside = sorted(page for page in pages if page > count)
    if outside:
        raise PageRepairError(f"目前劇本只有 {count} 頁，沒有第 {'、'.join(map(str, outside))} 頁。")
    pieces, cursor = [], 0
    for page in sorted(pages):
        mark = marks[page - 1]
        start = mark.end() + (1 if scenario_text.startswith("\n", mark.end()) else 0)
        end = marks[page].start() if page < count else len(scenario_text)
        if page < count and end - 2 >= start and scenario_text[end - 2:end] == "\n\n":
            end -= 2  # the blank line between pages stays
        pieces += [scenario_text[cursor:start], pages[page]]
        cursor = max(start, end)
    pieces.append(scenario_text[cursor:])
    return "".join(pieces)
