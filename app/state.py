"""Per-LINE-group game state persistence (flat JSON files, one per group)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import DATA_DIR
from app.models import GroupState

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def _path_for(group_id: str) -> Path:
    safe_id = _SAFE_ID_RE.sub("_", group_id)
    return DATA_DIR / f"{safe_id}.json"


def load_state(group_id: str) -> GroupState:
    path = _path_for(group_id)
    if not path.exists():
        return GroupState(group_id=group_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    return GroupState.from_dict(data)


def save_state(state: GroupState) -> None:
    path = _path_for(state.group_id)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
