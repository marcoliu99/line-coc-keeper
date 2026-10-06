"""Page-level repair of a published PDF scenario source from an uploaded workfile. No model calls.

A repair replaces whole physical PDF pages of one exact source version and is published as a new immutable derived
scenario; the parent is never touched. Parsing is strict, every failure aborts the whole repair, and nothing here
knows about Discord or the running game (see docs/specs/feature/discord_page_level_scenario_repair_design_spec.md).
"""
from __future__ import annotations

import hashlib
import json
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
PageKind = Literal["text", "map", "image"]
_PAGE_KINDS = ("text", "map", "image")
_TOP_KEYS = {"repair_version", "target", "patches"}
_TARGET_KEYS = {"scenario_id", "content_hash", "pdf_sha256", "page_count"}
_PATCH_KEYS = {"page", "base_page_sha256", "text", "page_kind", "review_note", "evidence", "expected_numeric_delta"}
_SHA = frozenset("0123456789abcdef")


class RepairError(ValueError):
    """A repair file that cannot even be read as a repair; ``code`` names the family."""

    def __init__(self, message: str, code: str = "schema") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RepairTarget:
    scenario_id: str
    content_hash: str
    pdf_sha256: str
    page_count: int


@dataclass(frozen=True)
class PageRepair:
    page: int
    base_page_sha256: str
    text: str
    page_kind: PageKind
    review_note: str
    evidence: tuple[dict[str, Any], ...]
    removed: dict[str, int]
    added: dict[str, int]


@dataclass(frozen=True)
class RepairProposal:
    version: int
    target: RepairTarget
    patches: tuple[PageRepair, ...]


@dataclass(frozen=True)
class RepairIssue:
    code: str  # stale | identity | source | page_range | evidence | content | numeric | no_change
    page: int | None
    detail: str

    def __str__(self) -> str:
        return (f"page {self.page}: " if self.page else "") + f"{self.code}: {self.detail}"


@dataclass(frozen=True)
class RepairCheck:
    ready: bool
    target: RepairTarget
    candidate_digest: str
    candidate_text: str
    repaired_pages: tuple[int, ...]
    issues: tuple[RepairIssue, ...]
    changes: tuple[dict[str, Any], ...]

    @property
    def stale(self) -> bool:
        return any(issue.code == "stale" for issue in self.issues)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain_int(value: Any) -> bool:
    return type(value) is int


def _exact(value: Any, keys: set[str], what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RepairError(f"{what} must contain exactly: {', '.join(sorted(keys))}")
    return value


def _text(value: Any, what: str, *, limit: int = authoring.MAX_FILE_BYTES) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise RepairError(f"{what} must be a string")
    return value


def _delta(value: Any, page: int) -> tuple[dict[str, int], dict[str, int]]:
    row = _exact(value, {"removed", "added"}, f"page {page}: expected_numeric_delta")
    parsed = []
    for side in ("removed", "added"):
        counts = row[side]
        if not isinstance(counts, dict):
            raise RepairError(f"page {page}: expected_numeric_delta.{side} must be an object")
        for token, count in counts.items():
            if (not isinstance(token, str) or scenario_numbers.mechanics_tokens(token) != [token]
                    or not _plain_int(count) or count < 1):
                raise RepairError(f"page {page}: expected_numeric_delta.{side} needs canonical number tokens and counts")
        parsed.append(dict(counts))
    return parsed[0], parsed[1]


def _patch(row: Any) -> PageRepair:
    if not isinstance(row, dict) or not _plain_int(row.get("page")):
        raise RepairError("Each patch needs an integer physical page number")
    number = row["page"]
    row = _exact(row, _PATCH_KEYS, f"page {number}")
    if number < 1:
        raise RepairError("Physical page numbers start at 1")
    base = _text(row["base_page_sha256"], f"page {number}: base_page_sha256", limit=64)
    if len(base) != 64 or not set(base) <= _SHA:
        raise RepairError(f"page {number}: base_page_sha256 must be a lowercase sha256")
    body = _text(row["text"], f"page {number}: text").strip()
    if library.PAGE_MARKER_RE.search(body):
        raise RepairError(f"page {number}: text cannot inject physical page markers")
    kind = row["page_kind"]
    if kind not in _PAGE_KINDS:
        raise RepairError(f"page {number}: page_kind must be one of {', '.join(_PAGE_KINDS)}")
    evidence = row["evidence"]
    if not isinstance(evidence, list):
        raise RepairError(f"page {number}: evidence must be a list")
    removed, added = _delta(row["expected_numeric_delta"], number)
    return PageRepair(number, base, body, kind, _text(row["review_note"], f"page {number}: review_note").strip(),
                      tuple(evidence), removed, added)


def parse_markdown_bytes(data: bytes) -> RepairProposal:
    """Strict parse of an uploaded repair file; anything unexpected raises ``RepairError``."""
    if len(data) > authoring.MAX_FILE_BYTES:
        raise RepairError("repair file is too large")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RepairError("repair file must be UTF-8") from exc
    try:
        payload = authoring.parse_markdown(content.replace("\r\n", "\n"))
    except authoring.Diagnostics as exc:
        raise RepairError("repair file must contain exactly one JSON payload") from exc
    payload = _exact(payload, _TOP_KEYS, "repair file")
    if not _plain_int(payload["repair_version"]) or payload["repair_version"] != VERSION:
        raise RepairError(f"unsupported repair_version (expected {VERSION})")
    raw = _exact(payload["target"], _TARGET_KEYS, "target")
    if (not all(isinstance(raw[key], str) and raw[key].strip() for key in ("scenario_id", "content_hash", "pdf_sha256"))
            or not _plain_int(raw["page_count"]) or raw["page_count"] < 1):
        raise RepairError("target identity fields are invalid")
    patches = payload["patches"]
    if not isinstance(patches, list) or not 1 <= len(patches) <= MAX_PATCHES:
        raise RepairError(f"patches must list 1 to {MAX_PATCHES} pages")
    parsed = sorted((_patch(row) for row in patches), key=lambda patch: patch.page)
    pages = [patch.page for patch in parsed]
    if len(set(pages)) != len(pages):
        raise RepairError("a physical page can be patched only once")
    return RepairProposal(VERSION, RepairTarget(raw["scenario_id"], raw["content_hash"], raw["pdf_sha256"],
                                                raw["page_count"]), tuple(parsed))


def locate_page_bodies(text: str, count: int) -> list[tuple[int, int]]:
    """Offsets of each physical page's raw body; lossless, so untouched pages are never re-serialized.

    A body runs from its marker line to the next marker, minus exactly one leading newline and, when another marker
    follows, exactly one blank-line separator.
    """
    marks = list(library.PAGE_MARKER_RE.finditer(text))
    if not marks and count == 1:
        return [(0, len(text))]
    if [int(m.group(1)) for m in marks] != list(range(1, count + 1)) or text[:marks[0].start()].strip():
        raise ValueError("Original extraction needs unique, ordered physical page markers")
    spans = []
    for i, mark in enumerate(marks):
        start = mark.end() + (1 if text.startswith("\n", mark.end()) else 0)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        if i + 1 < len(marks) and end - 2 >= start and text[end - 2:end] == "\n\n":
            end -= 2
        spans.append((start, max(start, end)))
    return spans


def page_bodies(text: str, count: int) -> list[str]:
    return [text[a:b] for a, b in locate_page_bodies(text, count)]


def _fail(target: RepairTarget, *issues: RepairIssue) -> RepairCheck:
    return RepairCheck(False, target, "", "", (), tuple(issues), ())


def _numeric_issue(patch: PageRepair, old: str) -> RepairIssue | None:
    before, after = scenario_numbers.mechanics_counts(old), scenario_numbers.mechanics_counts(patch.text)
    removed, added = dict(before - after), dict(after - before)
    if removed == patch.removed and added == patch.added:
        return None
    return RepairIssue("numeric", patch.page,
                       f"actual removed {removed} added {added}; declared removed {patch.removed} added {patch.added}")


def _content_issues(patch: PageRepair, page: Any, old: str) -> list[RepairIssue]:
    issues = []
    if not patch.review_note:
        issues.append(RepairIssue("content", patch.page, "missing review note"))
    if patch.page_kind == "image":
        native = pdf_quality.native_text(page)[0]
        if patch.text or native.strip():
            issues.append(RepairIssue("content", patch.page,
                                      "image pages cannot discard native text or supply inferred prose"))
    elif not patch.text:
        issues.append(RepairIssue("content", patch.page, "missing transcription"))
    try:
        issues.extend(RepairIssue("evidence", patch.page, text)
                      for text in review.validate_evidence(list(patch.evidence), list(page.rect), patch.page))
    except ValueError as exc:
        issues.append(RepairIssue("evidence", patch.page, str(exc)))
    numeric = _numeric_issue(patch, old)
    if numeric:
        issues.append(numeric)
    return issues


def check(proposal: RepairProposal) -> RepairCheck:
    """Validate against the live parent source and build the candidate; never writes anything."""
    target = proposal.target
    try:
        snapshot = trusted.read_snapshot(target.scenario_id)
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _fail(target, RepairIssue("identity", None, f"scenario source unavailable for repair: {exc}"))
    if (snapshot.manifest.get("content_hash") != target.content_hash or snapshot.pdf_sha256 != target.pdf_sha256):
        return _fail(target, RepairIssue("stale", None, "source content or PDF differs from the repair target"))
    try:
        with pymupdf.open(stream=snapshot.pdf_bytes, filetype="pdf") as doc:
            if len(doc) != target.page_count:
                return _fail(target, RepairIssue("stale", None, "page count differs from the repair target"))
            spans = locate_page_bodies(snapshot.text, len(doc))
            old_pages = [snapshot.text[a:b] for a, b in spans]
            issues: list[RepairIssue] = []
            for patch in proposal.patches:
                if patch.page > len(doc):
                    issues.append(RepairIssue("page_range", patch.page, "beyond the PDF page count"))
                    continue
                old = old_pages[patch.page - 1]
                if _sha(old) != patch.base_page_sha256:
                    issues.append(RepairIssue("stale", patch.page, "base page changed since the workfile was exported"))
                    continue
                issues.extend(_content_issues(patch, doc[patch.page - 1], old))
    except ValueError as exc:
        return _fail(target, RepairIssue("source", None, str(exc)))
    if issues:
        return _fail(target, *issues)

    by_page = {patch.page: patch for patch in proposal.patches}
    touched = sorted(by_page)
    replacement = {n: review.published_page_text({"image_only": by_page[n].page_kind == "image", "page": n,
                                                  "text": by_page[n].text}) for n in touched}
    candidate = snapshot.text
    for n in reversed(touched):  # splice from the end so earlier offsets stay valid
        a, b = spans[n - 1]
        candidate = candidate[:a] + replacement[n] + candidate[b:]
    new_pages = page_bodies(candidate, len(old_pages))
    if (any(new_pages[n - 1] != old_pages[n - 1] for n in range(1, len(old_pages) + 1) if n not in by_page)
            or any(new_pages[n - 1] != replacement[n] for n in touched)):
        return _fail(target, RepairIssue("content", None, "candidate pages did not round-trip"))
    if all(new_pages[n - 1] == old_pages[n - 1] for n in touched):
        return _fail(target, RepairIssue("no_change", None, "the repair does not change any page"))
    changes = tuple({
        "page": n, "page_kind": by_page[n].page_kind, "before_sha256": _sha(old_pages[n - 1]),
        "after_sha256": _sha(new_pages[n - 1]), "review_note": by_page[n].review_note,
        "evidence": list(by_page[n].evidence), "numeric_removed": by_page[n].removed,
        "numeric_added": by_page[n].added,
    } for n in touched)
    digest = authoring.digest([target.scenario_id, target.content_hash, target.pdf_sha256, list(changes), candidate])
    return RepairCheck(True, target, digest, candidate, tuple(touched), (), changes)


def derived_scenario_id(parent_id: str, digest: str) -> str:
    return parent_id[:38].rstrip("-") + "-repair-" + digest[:16]


def _parent_quality(parent_id: str) -> dict[str, Any]:
    path = library.scenario_path(parent_id) / "parse_quality.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _quality(parent: dict[str, Any], checked: RepairCheck, bodies: list[str], kinds: dict[int, str],
             pdf_sha256: str, source_repair: dict[str, Any]) -> dict[str, Any]:
    repaired = set(checked.repaired_pages)
    parent_rows = {row["page"]: row for row in parent.get("pages", [])
                   if isinstance(row, dict) and type(row.get("page")) is int}
    rows = []
    for number, body in enumerate(bodies, 1):
        if number in repaired:
            rows.append({"page": number, "method": "operator-reviewed-discord", "warnings": [],
                         "selected_sha256": _sha(body), "page_kind": kinds[number]})
        else:
            rows.append(deepcopy(parent_rows[number]) if number in parent_rows else {"page": number, "warnings": []})
    quality = deepcopy(parent)
    quality.update(
        version="source-repair-v1", parent_parse_quality_version=parent.get("version"),
        source_chars=len(checked.candidate_text), pdf_sha256=pdf_sha256, pages=rows,
        review_pages=[p for p in parent.get("review_pages", []) if p not in repaired],
        repaired_pages=sorted(repaired), source_repair=source_repair,
    )
    return quality


def publish(checked: RepairCheck, *, reviewer_user_id: str, reviewer_display_name: str,
            uploaded_filename: str) -> str:
    """Publish the checked candidate as a new immutable scenario; the same candidate yields the same id."""
    if not checked.ready:
        raise ValueError("Repair is not ready: " + "; ".join(str(issue) for issue in checked.issues))
    for value in (reviewer_user_id, uploaded_filename):
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("Reviewer identity and uploaded filename are required")
    display = str(reviewer_display_name or "").strip()[:200]
    target = checked.target
    with library.publication_lock():
        snapshot = trusted.read_snapshot(target.scenario_id)
        if (snapshot.manifest.get("content_hash") != target.content_hash or snapshot.pdf_sha256 != target.pdf_sha256):
            raise ValueError("Source changed since the repair was checked")
        scenario_id = derived_scenario_id(target.scenario_id, checked.candidate_digest)
        text = checked.candidate_text
        text_hash = _sha(text)
        bodies = page_bodies(text, target.page_count)
        kinds = {change["page"]: change["page_kind"] for change in checked.changes}
        identity = {"candidate_digest": checked.candidate_digest, "parent_scenario_id": target.scenario_id}
        parent_quality = _parent_quality(target.scenario_id)

        def build_metadata(now: str) -> tuple[dict, dict, dict]:
            source_repair = {
                "version": VERSION, **identity, "parent_content_hash": target.content_hash,
                "pages": list(checked.repaired_pages), "reviewer_user_id": reviewer_user_id,
                "reviewer_display_name": display, "uploaded_filename": uploaded_filename, "reviewed_at": now,
            }
            manifest = deepcopy(snapshot.manifest)
            manifest.update(id=scenario_id, content_hash=text_hash,
                            title=manifest.get("title", target.scenario_id) + " [page repaired]",
                            preview_hash=_sha(text[:2000]), created_at=now, updated_at=now,
                            source_review=dict(identity), source_repair=source_repair,
                            page_count=target.page_count)
            audit = {"version": VERSION, "kind": "page_repair", **identity, "source_hash_before": target.content_hash,
                     "source_hash_after": text_hash, "pdf_sha256": target.pdf_sha256,
                     "reviewer": {"discord_user_id": reviewer_user_id, "display_name": display},
                     "uploaded_filename": uploaded_filename, "reviewed_at": now,
                     "pages": list(checked.changes),
                     "derived_artifacts": "invalidated: indexes, pregens, scene_maps"}
            return manifest, audit, _quality(parent_quality, checked, bodies, kinds, target.pdf_sha256, source_repair)

        return trusted.publish_derived(
            snapshot, scenario_id, text, list(range(1, target.page_count + 1)), identity, build_metadata,
            lambda: None)
