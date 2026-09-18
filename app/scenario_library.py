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
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.config import SCENARIO_LIBRARY_DIR

_ASSET_RE = re.compile(r"front cover|title page|table of contents|credits|handout|character sheet|pre-generated|appendix", re.I)
_SAFE_RE = re.compile(r"[^a-z0-9]+")
_PAGE_RE = re.compile(r"^--- 第 (\d+) 頁 ---$", re.M)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    result = _SAFE_RE.sub("-", text.lower()).strip("-")
    return result[:48] or "scenario"


def _path(scenario_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", scenario_id):
        raise ValueError("無效的劇本 ID")
    return SCENARIO_LIBRARY_DIR / scenario_id


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


def build_chapters(pdf_bytes: bytes, scenario_text: str) -> list[dict[str, Any]]:
    """Use PDF bookmarks when present; fall back to one safe playable chapter."""
    try:
        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        toc = [(title.strip(), page) for _level, title, page in doc.get_toc(simple=True) if title.strip() and page > 0]
        page_count = doc.page_count
    except Exception:
        toc, page_count = [], max((int(p) for p in _PAGE_RE.findall(scenario_text)), default=1)
    playable = [(title, page) for title, page in toc if not _ASSET_RE.search(title)]
    if not playable:
        return [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": 1, "end_page": page_count}]
    # Published one-shots often bookmark every reference section at the same
    # level. Prefer explicit scene transitions over covers/overview headings;
    # pre-scene background stays attached to the first playable scene.
    scene_starts = [(title, page) for title, page in playable if re.search(r"^(start:|dead beacon|amphibious assault|conclusion)", title, re.I)]
    if scene_starts:
        scene_starts[0] = (scene_starts[0][0], playable[0][1])
    else:
        scene_starts = playable
    chapters = []
    for index, (title, page) in enumerate(scene_starts):
        next_page = scene_starts[index + 1][1] - 1 if index + 1 < len(scene_starts) else page_count
        chapters.append({"id": f"chapter-{index + 1:02d}", "title": title, "kind": "playable", "start_page": page, "end_page": max(page, next_page)})
    return chapters


def list_scenarios() -> list[dict[str, Any]]:
    if not SCENARIO_LIBRARY_DIR.exists():
        return []
    entries = []
    for directory in SCENARIO_LIBRARY_DIR.iterdir():
        if directory.is_dir():
            manifest = _read_json(directory / "manifest.json", None)
            if manifest:
                entries.append(manifest)
    return sorted(entries, key=lambda m: m.get("updated_at", ""), reverse=True)


def find_similar(title: str, preview: str, threshold: float = 0.82) -> list[dict[str, Any]]:
    normalized_title = _slug(title)
    preview_hash = hashlib.sha256(preview.encode("utf-8")).hexdigest()
    matches = []
    for manifest in list_scenarios():
        score = 0.0
        if manifest.get("preview_hash") == preview_hash:
            score = 1.0
        elif _slug(manifest.get("title", "")) == normalized_title:
            score = 0.95
        else:
            saved_preview = (_path(manifest["id"]) / "preview.txt").read_text(encoding="utf-8", errors="ignore")
            score = SequenceMatcher(None, preview[:12000], saved_preview[:12000]).ratio()
        if score >= threshold:
            matches.append({"id": manifest["id"], "title": manifest.get("title", ""), "score": score})
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def save_scenario(pdf_bytes: bytes, *, title: str, filename: str, preview: str, text: str, indexes: dict, pregens: list, page_maps: dict, page_images: dict[int, bytes], scenario_id: str | None = None) -> str:
    SCENARIO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    scenario_id = scenario_id or f"{_slug(title)}-{content_hash[:8]}"
    target = _path(scenario_id)
    temporary = Path(tempfile.mkdtemp(prefix=f".{scenario_id}-", dir=SCENARIO_LIBRARY_DIR))
    try:
        chapters = build_chapters(pdf_bytes, text)
        manifest = {"id": scenario_id, "title": title, "source_filename": filename, "created_at": _read_json(target / "manifest.json", {}).get("created_at", _now()), "updated_at": _now(), "preview_hash": hashlib.sha256(preview.encode("utf-8")).hexdigest(), "content_hash": content_hash, "page_count": max((int(p) for p in _PAGE_RE.findall(text)), default=1), "chapters": chapters}
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
        backup = target.with_name(f".{target.name}.backup")
        if backup.exists(): shutil.rmtree(backup)
        if target.exists(): target.replace(backup)
        temporary.replace(target)
        if backup.exists(): shutil.rmtree(backup)
        return scenario_id
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_context(scenario_id: str, active_chapter_id: str = "") -> dict[str, Any]:
    root = _path(scenario_id)
    manifest = _read_json(root / "manifest.json", None)
    if manifest is None: raise FileNotFoundError(scenario_id)
    chapters = [c for c in manifest.get("chapters", []) if c.get("kind") == "playable"]
    if not chapters: raise ValueError("劇本沒有可遊玩的章節")
    current_index = next((i for i, c in enumerate(chapters) if c["id"] == active_chapter_id), 0)
    window = chapters[current_index:current_index + 2]
    text = (root / "scenario.txt").read_text(encoding="utf-8")
    context_text = "\n\n".join(_pages_in_range(text, c["start_page"], c["end_page"]) for c in window)
    page_set = {p for c in window for p in range(c["start_page"], c["end_page"] + 1)}
    maps = {str(k): v for k, v in _read_json(root / "scene_maps.json", {}).items() if int(k) in page_set}
    indexes = _read_json(root / "indexes.json", {"npcs": [], "locations": []})
    return {"manifest": manifest, "active_chapter_id": chapters[current_index]["id"], "context_chapter_ids": [c["id"] for c in window], "text": context_text, "indexes": indexes, "pregens": _read_json(root / "pregens.json", []), "scene_maps": maps, "images_dir": root / "images"}


def copy_context_images(scenario_id: str, pages: set[int], save_image) -> None:
    root = _path(scenario_id) / "images"
    for page in pages:
        image = root / f"page_{page}.png"
        if image.exists(): save_image(page, image.read_bytes())


def clean_scenario(scenario_id: str) -> None:
    target = _path(scenario_id)
    if not target.exists(): raise FileNotFoundError(scenario_id)
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