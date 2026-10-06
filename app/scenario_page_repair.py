"""Page-level repair of a published PDF scenario source from an uploaded file. No model calls, no Discord, no game state.

A repair replaces whole physical PDF pages of the PDF-derived scenario a conversation has loaded and is published as a
new immutable derived scenario; the parent is never touched. Parsing is strict, every failure aborts the whole repair,
and untouched pages keep their exact bytes (see docs/specs/feature/discord_page_level_scenario_repair_design_spec.md).
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

import pymupdf

from app import pdf_quality, scenario_numbers
from app import scenario_authoring as authoring
from app import scenario_library as library
from app import scenario_source_review as review
from app import trusted_scenario_source as trusted

VERSION = 1
MAX_PATCHES = 100
MAX_PAGE_CHARS = 20_000  # a physical page is a few thousand characters; this keeps the numeric comparison cheap
PageKind = Literal["text", "map", "image"]
PAGE_KINDS: tuple[PageKind, ...] = ("text", "map", "image")
TOP_KEYS = frozenset({"repair_version", "target", "patches"})
TARGET_KEYS = frozenset({"title", "page_count"})
PATCH_KEYS = frozenset({"page", "text", "page_kind", "review_note"})
QUALITY_METHOD = "operator-reviewed-discord"
TEMPLATE_HINT = "請依 docs/references/scenario_page_repair_template_zh.md 的範本重新填寫。"
# Suffixes the library adds to the title of a derived version; a repair file keeps matching a child.
_DERIVED_SUFFIXES = (" [page repaired]", " [source reviewed]", " [ai source]")


class RepairError(ValueError):
    """A repair file that cannot even be read as a repair. The message never quotes the file's page text."""


@dataclass(frozen=True)
class RepairTarget:
    title: str
    page_count: int


@dataclass(frozen=True)
class PageRepair:
    page: int
    text: str
    page_kind: PageKind
    review_note: str


@dataclass(frozen=True)
class RepairProposal:
    version: int
    target: RepairTarget
    patches: tuple[PageRepair, ...]  # sorted by page


@dataclass(frozen=True)
class RepairIssue:
    code: str  # identity | source | title | page_count | page_range | content
    page: int | None
    detail: str

    def __str__(self) -> str:
        return (f"第 {self.page} 頁：" if self.page else "") + self.detail


@dataclass(frozen=True)
class RepairCheck:
    ready: bool
    scenario_id: str
    candidate_digest: str
    patches_digest: str
    candidate_text: str
    repaired_pages: tuple[int, ...]
    issues: tuple[RepairIssue, ...]
    changes: tuple[dict[str, Any], ...]  # per page: hashes, kind, note, removed and added (context, token) pairs
    noop: bool = False  # text and parse-quality rows of every requested page are already in place: publish nothing
    text_unchanged: bool = False  # the candidate text equals the parent's (a metadata-only repair when not noop)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain_int(value: Any) -> bool:
    return type(value) is int


def _exact(value: Any, keys: frozenset[str], what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RepairError(f"{what}必須剛好包含這些欄位：{', '.join(sorted(keys))}。{TEMPLATE_HINT}")
    return value


def _string(value: Any, what: str) -> str:
    if not isinstance(value, str) or len(value) > authoring.MAX_FILE_BYTES:
        raise RepairError(f"{what}必須是文字。{TEMPLATE_HINT}")
    try:
        value.encode("utf-8")  # a JSON escape can carry a lone surrogate, which cannot be hashed or stored
    except UnicodeEncodeError as exc:
        raise RepairError(f"{what}含有無法以 UTF-8 儲存的字元。") from exc
    return value


def _patch(row: Any) -> PageRepair:
    if not isinstance(row, dict) or not _plain_int(row.get("page")):
        raise RepairError(f"每個 patch 都需要整數的實體頁碼 page。{TEMPLATE_HINT}")
    number = row["page"]
    row = _exact(row, PATCH_KEYS, f"第 {number} 頁的 patch")
    if number < 1:
        raise RepairError("實體頁碼從 1 開始。")
    kind = row["page_kind"]
    if kind not in PAGE_KINDS:
        raise RepairError(f"第 {number} 頁的 page_kind 必須是 {'、'.join(PAGE_KINDS)} 其中之一。")
    body = _string(row["text"], f"第 {number} 頁的 text").strip()
    if len(body) > MAX_PAGE_CHARS:
        raise RepairError(f"第 {number} 頁的 text 超過 {MAX_PAGE_CHARS:,} 字元，一個實體頁不會這麼長，請確認內容。")
    if library.PAGE_MARKER_RE.search(body):
        raise RepairError(f"第 {number} 頁的 text 不能含頁碼標記（--- 第 N 頁 ---），頁碼標記由系統產生。")
    note = _string(row["review_note"], f"第 {number} 頁的 review_note").strip()
    if not note:
        raise RepairError(f"第 {number} 頁的 review_note 不能是空的，請寫明核對了什麼、修正了什麼。")
    if kind == "image" and body:
        raise RepairError(f"第 {number} 頁是 image，text 必須留空。")
    if kind != "image" and not body:
        raise RepairError(f"第 {number} 頁的 text 不能是空的；真的沒有可讀文字時請把 page_kind 設為 image。")
    return PageRepair(number, body, kind, note)


def parse_markdown_bytes(data: bytes) -> RepairProposal:
    """Strict parse of an uploaded repair file; anything unexpected raises ``RepairError``."""
    if len(data) > authoring.MAX_FILE_BYTES:
        raise RepairError("修復檔太大。")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RepairError("修復檔必須是 UTF-8 編碼。") from exc
    if content.strip().startswith(("{", "[")):  # the shared parser also takes a bare JSON document; a repair does not
        raise RepairError(f"修復檔必須把 JSON 放在 ```json 區塊裡。{TEMPLATE_HINT}")
    try:
        payload = authoring.parse_markdown(content.replace("\r\n", "\n"))
    except (authoring.Diagnostics, ValueError, RecursionError) as exc:  # json.loads: a huge integer, deep nesting
        raise RepairError(f"修復檔必須剛好含有一個 json 區塊。{TEMPLATE_HINT}") from exc
    payload = _exact(payload, TOP_KEYS, "修復檔")
    if not _plain_int(payload["repair_version"]) or payload["repair_version"] != VERSION:
        raise RepairError(f"repair_version 必須是 {VERSION}。{TEMPLATE_HINT}")
    raw = _exact(payload["target"], TARGET_KEYS, "target")
    title = raw["title"]
    if not isinstance(title, str) or not title.strip() or not _plain_int(raw["page_count"]) or raw["page_count"] < 1:
        raise RepairError(f"target 的 title 與 page_count 必須填入載入訊息顯示的劇本名稱與實體頁數。{TEMPLATE_HINT}")
    patches = payload["patches"]
    if not isinstance(patches, list) or not 1 <= len(patches) <= MAX_PATCHES:
        raise RepairError(f"patches 必須列出 1 到 {MAX_PATCHES} 頁。{TEMPLATE_HINT}")
    parsed = sorted((_patch(row) for row in patches), key=lambda patch: patch.page)
    pages = [patch.page for patch in parsed]
    if len(set(pages)) != len(pages):
        raise RepairError("同一個實體頁碼只能出現一次。")
    return RepairProposal(VERSION, RepairTarget(title.strip(), raw["page_count"]), tuple(parsed))


def locate_page_body_spans(text: str, count: int) -> list[tuple[int, int]]:
    """Offsets of each physical page's raw body in ``text``; lossless, so untouched pages are never re-serialized.

    A body runs from its marker line to the next marker, minus exactly one leading newline and, when another marker
    follows, exactly one blank-line separator. Untouched pages published by the source-review workflow keep their
    reviewed leading or trailing whitespace, which a splitter that strips every body would lose.
    """
    marks = list(library.PAGE_MARKER_RE.finditer(text))
    if not marks and count == 1:
        return [(0, len(text))]
    if [int(m.group(1)) for m in marks] != list(range(1, count + 1)) or text[:marks[0].start()].strip():
        raise ValueError("來源文字的實體頁碼標記不是依序且各出現一次的 1..N")
    spans = []
    for i, mark in enumerate(marks):
        start = mark.end() + (1 if text.startswith("\n", mark.end()) else 0)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        if i + 1 < len(marks) and end - 2 >= start and text[end - 2:end] == "\n\n":
            end -= 2
        spans.append((start, max(start, end)))
    return spans


def published_body(patch: PageRepair) -> str:
    """The text actually written into the page: the reviewed text, or the image placeholder for an image page."""
    return review.published_page_text({"image_only": patch.page_kind == "image", "page": patch.page, "text": patch.text})


def merge_pages(parent_text: str, spans: list[tuple[int, int]], patches: tuple[PageRepair, ...]) -> str:
    """One left-to-right pass over the original text; no offset is ever recomputed after a replacement."""
    pieces, cursor = [], 0
    for patch in sorted(patches, key=lambda p: p.page):
        start, end = spans[patch.page - 1]
        pieces += [parent_text[cursor:start], published_body(patch)]
        cursor = end
    pieces.append(parent_text[cursor:])
    return "".join(pieces)


def normalize_title(title: str) -> str:
    """Case, whitespace runs and the suffixes of derived versions do not matter; punctuation does."""
    value = re.sub(r"\s+", " ", title).strip().casefold()
    changed = True
    while changed:
        changed = False
        for suffix in _DERIVED_SUFFIXES:
            if value.endswith(suffix):
                value, changed = value[: -len(suffix)].rstrip(), True
    return value


def _normal_patches(patches: tuple[PageRepair, ...]) -> list[dict[str, Any]]:
    return [{"page": p.page, "text": p.text, "page_kind": p.page_kind, "review_note": p.review_note}
            for p in sorted(patches, key=lambda p: p.page)]


def patches_digest(proposal: RepairProposal) -> str:
    """Identifies 'this repair file' across the lineage, independent of any parent."""
    return authoring.digest(_normal_patches(proposal.patches))


def _quality_row(quality: dict[str, Any], page: int) -> dict[str, Any] | None:
    for row in quality.get("pages", []):
        if isinstance(row, dict) and row.get("page") == page:
            return row
    return None


def _quality_in_place(quality: dict[str, Any], patch: PageRepair, body: str) -> bool:
    row = _quality_row(quality, patch.page)
    return (row is not None and row.get("method") == QUALITY_METHOD and row.get("selected_sha256") == _sha(body)
            and row.get("page_kind") == patch.page_kind and row.get("warnings") == []
            and patch.page not in quality.get("review_pages", []))


def _fail(scenario_id: str, *issues: RepairIssue) -> RepairCheck:
    return RepairCheck(False, scenario_id, "", "", "", (), tuple(issues), ())


def _change(patch: PageRepair, old_body: str, new_body: str) -> dict[str, Any]:
    removed, added = scenario_numbers.ordered_diff(
        scenario_numbers.mechanics_contexts(old_body), scenario_numbers.mechanics_contexts(new_body))
    return {"page": patch.page, "page_kind": patch.page_kind, "review_note": patch.review_note,
            "before_sha256": _sha(old_body), "after_sha256": _sha(new_body),
            "removed": [list(pair) for pair in removed], "added": [list(pair) for pair in added]}


def check(proposal: RepairProposal, scenario_id: str) -> RepairCheck:
    """Validate against the loaded scenario's source and build the candidate; never writes anything."""
    try:
        snapshot = trusted.read_snapshot(scenario_id)
    except (FileNotFoundError, ValueError, OSError):
        return _fail(scenario_id, RepairIssue(
            "identity", None, "目前載入的劇本沒有可修復的 PDF 來源（只有 PDF 劇本能做頁面修復）。"))
    loaded_title = str(snapshot.manifest.get("title") or "")
    if normalize_title(proposal.target.title) != normalize_title(loaded_title):
        return _fail(scenario_id, RepairIssue(
            "title", None, f"這份修復檔是為《{proposal.target.title}》準備的，目前載入的是《{loaded_title}》。"))
    try:
        with pymupdf.open(stream=snapshot.pdf_bytes, filetype="pdf") as doc:
            if len(doc) != proposal.target.page_count:
                return _fail(scenario_id, RepairIssue(
                    "page_count", None, f"修復檔寫的是 {proposal.target.page_count} 頁，目前劇本的 PDF 有 {len(doc)} 頁。"))
            spans = locate_page_body_spans(snapshot.text, len(doc))
            issues = []
            for patch in proposal.patches:
                if patch.page > len(doc):
                    issues.append(RepairIssue("page_range", patch.page, "超出 PDF 的頁數。"))
                elif patch.page_kind == "image":
                    if not library.has_page_image(scenario_id, patch.page):
                        issues.append(RepairIssue("content", patch.page, "這一頁沒有儲存的頁面圖片可指向，不能標為 image。"))
                    elif pdf_quality.native_text(doc[patch.page - 1])[0].strip():
                        issues.append(RepairIssue("content", patch.page, "這一頁的 PDF 有可讀的文字，不能標為 image。"))
    except ValueError as exc:
        return _fail(scenario_id, RepairIssue("source", None, str(exc)))
    if issues:
        return _fail(scenario_id, *issues)

    candidate = merge_pages(snapshot.text, spans, proposal.patches)
    old_pages = [snapshot.text[a:b] for a, b in spans]
    new_spans = locate_page_body_spans(candidate, len(spans))
    new_pages = [candidate[a:b] for a, b in new_spans]
    by_page = {patch.page: patch for patch in proposal.patches}
    if (any(new_pages[n - 1] != old_pages[n - 1] for n in range(1, len(spans) + 1) if n not in by_page)
            or any(new_pages[n - 1] != published_body(patch) for n, patch in by_page.items())):
        return _fail(scenario_id, RepairIssue("content", None, "候選文字的頁面沒有通過還原檢查，沒有套用任何內容。"))
    quality = library.read_parse_quality(scenario_id)
    changes = tuple(_change(patch, old_pages[patch.page - 1], new_pages[patch.page - 1]) for patch in proposal.patches)
    text_unchanged = candidate == snapshot.text
    noop = text_unchanged and all(
        _quality_in_place(quality, patch, new_pages[patch.page - 1]) for patch in proposal.patches)
    normal = _normal_patches(proposal.patches)
    digest = authoring.digest([scenario_id, snapshot.manifest.get("content_hash"), snapshot.pdf_sha256, normal, candidate])
    return RepairCheck(True, scenario_id, digest, authoring.digest(normal), candidate,
                       tuple(patch.page for patch in proposal.patches), (), changes,
                       noop=noop, text_unchanged=text_unchanged)


def derived_scenario_id(parent_id: str, digest: str) -> str:
    return parent_id[:38].rstrip("-") + "-repair-" + digest[:16]


def repaired_quality(parent: dict[str, Any], proposal: RepairProposal, candidate_text: str) -> dict[str, Any]:
    """Parse-quality record of the child: untouched pages keep their rows and warnings, repaired ones are cleared."""
    repaired = {patch.page for patch in proposal.patches}
    spans = locate_page_body_spans(candidate_text, proposal.target.page_count)
    by_page = {patch.page: patch for patch in proposal.patches}
    rows = []
    for number, (start, end) in enumerate(spans, 1):
        if number in repaired:
            rows.append({"page": number, "method": QUALITY_METHOD, "warnings": [],
                         "selected_sha256": _sha(candidate_text[start:end]), "page_kind": by_page[number].page_kind})
        else:
            rows.append(deepcopy(_quality_row(parent, number) or {"page": number, "warnings": []}))
    quality = deepcopy(parent)
    quality.update(
        version="source-repair-v1", parent_parse_quality_version=parent.get("version"),
        source_chars=len(candidate_text), pages=rows,
        review_pages=[p for p in parent.get("review_pages", []) if p not in repaired],
        repaired_pages=sorted(repaired))
    return quality


def describe_changes(changes: tuple[dict[str, Any], ...]) -> list[str]:
    """One line per page for the private numeric report. Quotes numbers and their labels, never page prose."""
    def show(pairs: list[list[str]]) -> str:
        return "、".join(f"{context} {token}".strip() for context, token in pairs)

    lines = []
    for change in changes:
        if not change["removed"] and not change["added"]:
            lines.append(f"第 {change['page']} 頁：數值沒有變動")
            continue
        parts = []
        if change["removed"]:
            parts.append(f"移除 {show(change['removed'])}")
        if change["added"]:
            parts.append(f"新增 {show(change['added'])}")
        lines.append(f"第 {change['page']} 頁：" + "；".join(parts))
    return lines
