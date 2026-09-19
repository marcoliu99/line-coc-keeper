"""Deterministic, no-LLM scene digest snapshots."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from app import db
from app.models import GroupState

_logger = logging.getLogger(__name__)


def _id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{uuid4().hex[:4]}"


def _public_state(state: GroupState) -> dict:
    return {
        "characters": {
            c.name: {
                "location": "",
                "away": c.away,
                "hp": c.hp, "hp_max": c.hp_max,
                "san": c.san, "san_max": c.san_max,
                "luck": c.luck, "mp": c.mp, "mp_max": c.mp_max,
                "carried_items": list(c.carried_items), "status_tags": list(c.status_tags),
            }
            for c in state.characters.values()
        },
        "combat": state.combat.to_dict(),
        "established_facts": [x for x in state.established_facts if x.get("visibility", "public") == "public"],
        "known_clues": [x for x in state.known_clues if x.get("visibility", "public") == "public"],
        "consumed_or_removed_items": state.consumed_or_removed_items,
    }


def create_digest(state: GroupState, *, scene_label: str = "") -> dict:
    started = time.monotonic()
    digest_id = _id()
    entry = {
        "group_id": state.group_id,
        "digest_id": digest_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeline_id": state.timeline_id or f"legacy-{state.group_id}",
        "state_revision": state.state_revision,
        "schema_version": state.schema_version,
        "log_length": len(state.log),
        "scene_label": scene_label or state.active_chapter_id or state.scenario_title or "目前場景",
        "public": _public_state(state),
        "private": {
            "note": "以下內容僅供 Keeper 使用，不可透露給玩家。",
            "facts": [x for x in state.established_facts if x.get("visibility") == "kp_only"],
            "clues": [x for x in state.known_clues if x.get("visibility") == "kp_only"],
        },
    }
    with db.transaction() as conn:
        db.set_json_tx(conn, "scene_digests", f"{state.group_id}:{digest_id}", entry)
    _logger.info(
        "scene_digest_success group_id=%s digest_id=%s revision=%s timeline_id=%s duration_ms=%s",
        state.group_id, digest_id, state.state_revision, entry["timeline_id"],
        int((time.monotonic() - started) * 1000),
    )
    return entry


def list_digests(group_id: str) -> list[dict]:
    return [value for _, value in db.list_json("scene_digests", prefix=f"{group_id}:")]


def latest_digest(group_id: str, timeline_id: str) -> dict | None:
    entries = [x for x in list_digests(group_id) if x.get("timeline_id") == timeline_id]
    return max(entries, key=lambda x: (x.get("state_revision", 0), x.get("updated_at", "")), default=None)


def get_digest(group_id: str, digest_id: str) -> dict:
    value = db.get_json("scene_digests", f"{group_id}:{digest_id}")
    if value is None:
        raise KeyError(digest_id)
    return value
