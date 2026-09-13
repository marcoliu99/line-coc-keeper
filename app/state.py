"""Per-group game state persistence (flat JSON files, one per conversation),
plus on-disk storage for scenario page images (kept as separate binary files
rather than inline base64 in the JSON, which would bloat every read/write of
state that doesn't even touch images)."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from app.config import DATA_DIR
from app.models import GroupState

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_id(group_id: str) -> str:
    return _SAFE_ID_RE.sub("_", group_id)


def _path_for(group_id: str) -> Path:
    return DATA_DIR / f"{_safe_id(group_id)}.json"


def load_state(group_id: str) -> GroupState:
    path = _path_for(group_id)
    if not path.exists():
        return GroupState(group_id=group_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    return GroupState.from_dict(data)


def save_state(state: GroupState) -> None:
    path = _path_for(state.group_id)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _images_dir(group_id: str) -> Path:
    return DATA_DIR / f"{_safe_id(group_id)}_images"


def save_page_image(group_id: str, page_number: int, png_bytes: bytes) -> None:
    directory = _images_dir(group_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"page_{page_number}.png").write_bytes(png_bytes)


def load_page_image(group_id: str, page_number: int) -> bytes | None:
    path = _images_dir(group_id) / f"page_{page_number}.png"
    if not path.exists():
        return None
    return path.read_bytes()


def clear_page_images(group_id: str) -> None:
    """Called whenever a new PDF is uploaded, so a scenario switch doesn't leave
    a previous scenario's page images (and their page numbers) lying around."""
    shutil.rmtree(_images_dir(group_id), ignore_errors=True)
