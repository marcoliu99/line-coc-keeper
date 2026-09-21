"""Filesystem-backed, reusable library of parsed scenario PDFs.

The library owns immutable parsing artefacts. A conversation only keeps a
small current/next-chapter context window in GroupState.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.config import SCENARIO_LIBRARY_DIR

_ASSET_RE = re.compile(r"front cover|title page|table of contents|credits|handout|character sheet|pre-generated|appendix", re.IGNORECASE)
_SAFE_RE = re.compile(r"[^a-z0-9]+")
_PAGE_RE = re.compile(r"^--- 第 (\d+) 頁 ---$", re.MULTILINE)
_LIBRARY_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    result = _SAFE_RE.sub("-", text.lower()).strip("-")
    return result[:48] or "scenario"


def _path(scenario_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", scenario_id):
        raise ValueError("無效的劇本 ID")
    return SCENARIO_LIBRARY_DIR / scenario_id


def safe_import_path(import_dir: Path, filename: str) -> Path:
    name = Path(filename).name
    if name != filename or not name.lower().endswith(".pdf") or not name:
        raise ValueError("invalid import filename")
    root = import_dir.resolve()
    raw_candidate = root / name
    candidate = raw_candidate.resolve()
    if raw_candidate.is_symlink() or candidate.parent != root or not candidate.is_file():
        raise FileNotFoundError(name)
    return candidate


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _pages_in_range(text: str, start: int, end: int) -> str:
    pieces = _PAGE_RE.split(text)
    selected: list[str] = []
    for i in range(1, len(pieces), 2):
        page = int(pieces[i])
        if start <= page <= end:
            selected.append(f"--- 第 {page} 頁 ---\n{pieces[i + 1].strip()}")
    return "\n\n".join(selected)


def _dedupe_by_page(entries: list[tuple[str, int]]) -> list[tuple[str, int]]:
    deduped: list[tuple[str, int]] = []
    for title, page in entries:
        if not deduped or deduped[-1][1] != page:
            deduped.append((title, page))
    return deduped


def build_chapters(pdf_bytes: bytes, scenario_text: str) -> list[dict[str, Any]]:
    """Build generic, bookmark-based playable chapters.

    Only top-level non-asset bookmarks become chapters. This avoids treating
    every reference subsection as a scene, and stops the final playable chapter
    before a following appendix/handout section.
    """
    try:
        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        toc = [(int(level), title.strip(), int(page)) for level, title, page in doc.get_toc(simple=True) if title.strip() and page > 0]
        page_count = doc.page_count
    except Exception:  # noqa: BLE001 - malformed optional PDF outline falls back to page markers.
        toc, page_count = [], max((int(p) for p in _PAGE_RE.findall(scenario_text)), default=1)
    non_assets = [(level, title, page) for level, title, page in toc if not _ASSET_RE.search(title)]
    if not non_assets:
        return [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": 1, "end_page": page_count}]

    levels = sorted({level for level, _title, _page in non_assets})
    if len(levels) == 1:
        # A flat bookmark list (every non-asset entry at the same level) has
        # no structural signal separating "chapter" from "scene" — see the
        # Lightless Beacon sample in docs/scenario_library_design_spec.md,
        # whose 9 same-level bookmarks (Introduction, Background, Start:
        # Choppy Waters, Dead Beacon, ...) must stay inside one playable
        # chapter-01 with those entries as `sections`. Splitting each one
        # into its own playable chapter would make the two-chapter sliding
        # window (see GroupState.context_chapter_ids) cover only a page or
        # two at a time and exclude the actual opening scene until several
        # "advance_scenario_chapter" calls later.
        flat_deduped = _dedupe_by_page([(title, page) for _level, title, page in non_assets])
        first_asset_page = min((page for _level, title, page in toc if _ASSET_RE.search(title) and page > flat_deduped[0][1]), default=page_count + 1)
        end_page = min(page_count, first_asset_page - 1)
        sections = []
        for index, (title, page) in enumerate(flat_deduped):
            next_page = flat_deduped[index + 1][1] - 1 if index + 1 < len(flat_deduped) else end_page
            if page <= next_page:
                sections.append({"id": f"section-{index + 1:02d}", "title": title, "start_page": page, "end_page": next_page})
        return [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": flat_deduped[0][1], "end_page": end_page, "sections": sections}]

    top_level = levels[0]
    starts = [(title, page) for level, title, page in non_assets if level == top_level]
    # PDFs with a flat TOC still need a usable fallback, but deduplicate entries
    # sharing a page so metadata/bookmark aliases cannot create empty chapters.
    if not starts:
        starts = [(title, page) for _level, title, page in non_assets]
    deduped: list[tuple[str, int]] = []
    for title, page in starts:
        if not deduped or deduped[-1][1] != page:
            deduped.append((title, page))
    if not deduped:
        return [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": 1, "end_page": page_count}]

    # A later asset bookmark (Appendix, Handouts, character sheets, ...) bounds
    # the final playable chapter instead of leaking reference material into play.
    first_asset_page = min((page for _level, title, page in toc if _ASSET_RE.search(title) and page > deduped[-1][1]), default=page_count + 1)
    playable_end = min(page_count, first_asset_page - 1)
    chapters: list[dict[str, Any]] = []
    for index, (title, page) in enumerate(deduped):
        next_page = deduped[index + 1][1] - 1 if index + 1 < len(deduped) else playable_end
        if page <= next_page:
            chapters.append({"id": f"chapter-{len(chapters) + 1:02d}", "title": title, "kind": "playable", "start_page": page, "end_page": next_page})
    return chapters or [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": 1, "end_page": page_count}]


def list_scenarios() -> list[dict[str, Any]]:
    if not SCENARIO_LIBRARY_DIR.exists():
        return []
    entries = []
    for directory in SCENARIO_LIBRARY_DIR.iterdir():
        if directory.is_dir() and not directory.name.startswith("."):
            manifest = _read_json(directory / "manifest.json", None)
            if isinstance(manifest, dict) and manifest.get("id"):
                entries.append(manifest)
    return sorted(entries, key=lambda m: m.get("updated_at", ""), reverse=True)


def find_similar(title: str, preview: str, threshold: float = 0.82) -> list[dict[str, Any]]:
    normalized_title = _slug(title)
    preview_hash = hashlib.sha256(preview.encode("utf-8")).hexdigest()
    matches = []
    for manifest in list_scenarios():
        scenario_id = manifest.get("id")
        if not isinstance(scenario_id, str):
            continue
        score = 0.0
        if manifest.get("preview_hash") == preview_hash:
            score = 1.0
        elif _slug(str(manifest.get("title", ""))) == normalized_title:
            score = 0.95
        else:
            try:
                saved_preview = (_path(scenario_id) / "preview.txt").read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError):
                continue
            score = SequenceMatcher(None, preview[:12000], saved_preview[:12000]).ratio()
        if score >= threshold:
            matches.append({"id": scenario_id, "title": manifest.get("title", ""), "score": score})
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def content_similar(scenario_id: str, text: str, threshold: float = 0.75) -> bool:
    """Full-content ("二次比對") check used by reparse: is `text` still close
    enough to `scenario_id`'s existing scenario.txt to treat this as the same
    scenario? Exact-hash short-circuits (byte-identical re-upload); otherwise
    falls back to a capped SequenceMatcher ratio, since full scenario texts
    can be long enough that comparing them in full would be slow."""
    manifest = _read_json(_path(scenario_id) / "manifest.json", None)
    if not isinstance(manifest, dict):
        return False
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if manifest.get("content_hash") == content_hash:
        return True
    try:
        existing_text = (_path(scenario_id) / "scenario.txt").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return SequenceMatcher(None, text[:20000], existing_text[:20000]).ratio() >= threshold


def save_scenario(pdf_bytes: bytes, *, title: str, filename: str, preview: str, text: str, indexes: dict, pregens: list, page_maps: dict, page_images: dict[int, bytes], scenario_id: str | None = None, reparse_candidate_id: str | None = None) -> str:
    with _LIBRARY_LOCK:
        SCENARIO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # /coc scenario reparse's caller passes the KP-confirmed candidate here
        # instead of forcing scenario_id directly — content_similar re-verifies
        # it with the now-available full text (the spec's "完整內容二次比對") so a
        # reparse that turns out to be a genuinely different scenario still lands
        # in a new library entry instead of overwriting an unrelated one.
        if scenario_id is None and reparse_candidate_id and content_similar(reparse_candidate_id, text):
            scenario_id = reparse_candidate_id
        scenario_id = scenario_id or f"{_slug(title)}-{content_hash[:8]}"
        target = _path(scenario_id)
        temporary = Path(tempfile.mkdtemp(prefix=f".{scenario_id}-", dir=SCENARIO_LIBRARY_DIR))
        backup = target.with_name(f".{target.name}.backup")
        moved_previous = False
        try:
            chapters = build_chapters(pdf_bytes, text)
            assets = _build_image_assets(page_images, page_maps, text, chapters)
            previous_manifest = _read_json(target / "manifest.json", {})
            manifest = {"id": scenario_id, "title": title, "source_filename": filename, "created_at": previous_manifest.get("created_at", _now()), "updated_at": _now(), "preview_hash": hashlib.sha256(preview.encode("utf-8")).hexdigest(), "content_hash": content_hash, "page_count": max((int(p) for p in _PAGE_RE.findall(text)), default=1), "chapters": chapters, "image_assets": assets}
            (temporary / "images").mkdir()
            (temporary / "source.pdf").write_bytes(pdf_bytes)
            (temporary / "preview.txt").write_text(preview, encoding="utf-8")
            (temporary / "scenario.txt").write_text(text, encoding="utf-8")
            (temporary / "indexes.json").write_text(json.dumps(indexes, ensure_ascii=False), encoding="utf-8")
            (temporary / "pregens.json").write_text(json.dumps(pregens, ensure_ascii=False), encoding="utf-8")
            (temporary / "scene_maps.json").write_text(json.dumps(page_maps, ensure_ascii=False), encoding="utf-8")
            (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for page, image in page_images.items():
                (temporary / "images" / f"page_{page}.png").write_bytes(image)
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                target.replace(backup)
                moved_previous = True
            temporary.replace(target)
            if backup.exists():
                shutil.rmtree(backup)
            return scenario_id
        except Exception:
            if moved_previous and not target.exists() and backup.exists():
                backup.replace(target)
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def _filter_index(items: list[dict], pages: set[int]) -> list[dict]:
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("page"), int) and item["page"] in pages]


def load_context(scenario_id: str, active_chapter_id: str = "") -> dict[str, Any]:
    root = _path(scenario_id)
    manifest = _read_json(root / "manifest.json", None)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(scenario_id)
    chapters = [c for c in manifest.get("chapters", []) if c.get("kind") == "playable"]
    if not chapters:
        raise ValueError("劇本沒有可遊玩的章節")
    current_index = next((i for i, c in enumerate(chapters) if c["id"] == active_chapter_id), 0)
    window = chapters[current_index:current_index + 2]
    text = (root / "scenario.txt").read_text(encoding="utf-8")
    context_text = "\n\n".join(_pages_in_range(text, c["start_page"], c["end_page"]) for c in window)
    # Old/imported scenarios can predate page markers; preserve their text rather
    # than silently activating an empty context.
    if not context_text.strip() and text.strip():
        context_text = text
    page_set = {p for c in window for p in range(c["start_page"], c["end_page"] + 1)}
    maps = {str(k): v for k, v in _read_json(root / "scene_maps.json", {}).items() if str(k).isdigit() and int(k) in page_set}
    all_indexes = _read_json(root / "indexes.json", {"npcs": [], "locations": []})
    indexes = {"npcs": _filter_index(all_indexes.get("npcs", []), page_set), "locations": _filter_index(all_indexes.get("locations", []), page_set)}
    return {"manifest": manifest, "active_chapter_id": chapters[current_index]["id"], "context_chapter_ids": [c["id"] for c in window], "text": context_text, "indexes": indexes, "pregens": _read_json(root / "pregens.json", []), "scene_maps": maps, "images_dir": root / "images", "page_numbers": page_set}


def next_chapter_id(scenario_id: str, active_chapter_id: str) -> str | None:
    manifest = _read_json(_path(scenario_id) / "manifest.json", {})
    chapters = [c for c in manifest.get("chapters", []) if c.get("kind") == "playable"]
    index = next((i for i, c in enumerate(chapters) if c.get("id") == active_chapter_id), -1)
    if index < 0 or index + 1 >= len(chapters):
        return None
    return chapters[index + 1]["id"]


def copy_context_images(scenario_id: str, pages: set[int], save_image: Callable[[int, bytes], None]) -> None:
    root = _path(scenario_id) / "images"
    for page in pages:
        image = root / f"page_{page}.png"
        if image.exists():
            save_image(page, image.read_bytes())


def clean_scenario(scenario_id: str) -> None:
    with _LIBRARY_LOCK:
        target = _path(scenario_id)
        if not target.exists():
            raise FileNotFoundError(scenario_id)
        shutil.rmtree(target)


def stage_upload(pdf_bytes: bytes) -> str:
    """Persist a candidate PDF while the KP decides whether to reparse it."""
    directory = SCENARIO_LIBRARY_DIR / ".staging"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(pdf_bytes).hexdigest()
    (directory / f"{key}.pdf").write_bytes(pdf_bytes)
    return key


def read_staged_upload(key: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise FileNotFoundError(key)
    return (SCENARIO_LIBRARY_DIR / ".staging" / f"{key}.pdf").read_bytes()


def discard_staged_upload(key: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", key):
        (SCENARIO_LIBRARY_DIR / ".staging" / f"{key}.pdf").unlink(missing_ok=True)


def _build_image_assets(page_images: dict[int, bytes], page_maps: dict, text: str, chapters: list[dict]) -> list[dict[str, Any]]:
    assets = []
    map_pages = {str(k) for k in page_maps}
    for page in sorted(page_images):
        page_text = _pages_in_range(text, page, page)
        is_character_sheet = re.search(
            r"\bSTR\b|\bDEX\b|\bSAN\b|characteri\w*|investigator\s+skills|"
            r"weapon\s+regular\s+hard\s+extreme|\boccupation\b.*\b(weapon|damage|dodge|luck)\b",
            page_text,
            re.IGNORECASE | re.DOTALL,
        )
        # Only structural evidence (page_maps, from the vision model actually
        # detecting a floor plan — see _analyze_graphic_page) may override a
        # character_sheet classification here. A page mentioning "map" in
        # passing (a monster/NPC stat block with a "see map, p.X" reference is
        # a common scenario layout) is a much weaker signal than an actual
        # stat block, and character_sheet's default "kp_only" visibility (see
        # below) exists specifically to hide that kind of page from players —
        # letting the plain-text regex win here would silently defeat that.
        if str(page) in map_pages:
            kind = "map"
        elif is_character_sheet:
            kind = "character_sheet"
        elif re.search(r"\bmap\b|floor\s*plan|地圖|平面圖|房間圖", page_text, re.IGNORECASE):
            kind = "map"
        elif re.search(r"handout|手卡|玩家資料|報紙|剪報|信件|書信|日記|照片|文件|線索", page_text, re.IGNORECASE):
            kind = "handout"
        elif re.search(r"portrait|人物|肖像|character\s+(illustration|portrait)", page_text, re.IGNORECASE):
            kind = "portrait"
        else:
            kind = "illustration"
        chapter = next((c["id"] for c in chapters if c["start_page"] <= page <= c["end_page"]), "")
        # character_sheet pages default to KP-only: they're just as likely to be
        # an NPC/villain stat block or a pregen that reveals a "secret"
        # investigator connection as they are a player-facing pregen sheet —
        # the classifier here has no way to tell those apart, and the spoiler
        # risk of showing the wrong one to players outweighs the convenience
        # of never having to think about it. A KP can still reveal a specific
        # one via the KP-only show_scenario_image call (see keeper._execute_tool).
        # This is deliberately not the same channel as /coc pregens'
        # player-facing pregen selection, which never goes through this tool.
        visibility = "kp_only" if kind == "character_sheet" else "public"
        assets.append({"id": f"page-{page}-{kind}", "page": page, "type": kind, "chapter_id": chapter, "visibility": visibility, "tags": [kind], "description": page_text[:500]})
    return assets


def search_images(scenario_id: str, query: str = "", image_type: str = "", allowed_chapter_ids: set[str] | None = None) -> list[dict[str, Any]]:
    manifest = _read_json(_path(scenario_id) / "manifest.json", {})
    terms = query.lower().split()
    matches = []
    for asset in manifest.get("image_assets", []):
        if image_type and asset.get("type") != image_type:
            continue
        if allowed_chapter_ids is not None and asset.get("chapter_id") not in allowed_chapter_ids:
            continue
        haystack = " ".join([asset.get("id", ""), asset.get("type", ""), asset.get("description", ""), *asset.get("tags", [])]).lower()
        if not terms or all(term in haystack for term in terms):
            matches.append(asset)
    return matches


def get_image_asset(scenario_id: str, image_id: str) -> dict[str, Any] | None:
    return next((a for a in search_images(scenario_id) if a.get("id") == image_id), None)
