"""Deterministic, no-LLM scene digest snapshots."""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from app import db, locks
from app.models import GroupState

_logger = logging.getLogger(__name__)


def _log_group_id(group_id: str) -> str:
    return hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:12]


def _id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{uuid4().hex[:4]}"


def _public_state(state: GroupState) -> dict:
    active_characters = state.active_characters()
    locations = {
        owner_id: {
            "map_page": state.current_map_page.get(owner_id, ""),
            "room_id": state.current_room_id.get(owner_id, ""),
            "facing": state.party_facing.get(owner_id, "N"),
        }
        for owner_id in {char.owner_id for char in active_characters}
    }
    combat_public = {
        "active": state.combat.active,
        "round_number": state.combat.round_number,
        "current_index": state.combat.current_index,
        "order": [
            {
                "combatant_id": combatant.combatant_id,
                "display_name": combatant.display_name,
                "side": combatant.side,
                "dex": combatant.dex,
                "defeated": combatant.defeated,
                **({"hp": combatant.hp, "hp_max": combatant.hp_max} if combatant.side != "enemy" else {}),
            }
            for combatant in state.combat.order
        ],
    }
    return {
        "characters": {
            (c.character_id or f"legacy-user:{c.owner_id}"): {
                "character_id": c.character_id,
                "owner_id": c.owner_id,
                "name": c.name,
                "location": "",
                "away": c.away,
                "hp": c.hp, "hp_max": c.hp_max,
                "san": c.san, "san_max": c.san_max,
                "luck": c.luck, "mp": c.mp, "mp_max": c.mp_max,
                "carried_items": list(c.carried_items), "status_tags": list(c.status_tags),
            }
            for c in active_characters
        },
        "locations": locations,
        "combat": combat_public,
        "established_facts": [x for x in state.established_facts if x.get("visibility", "public") == "public"],
        "known_clues": [x for x in state.known_clues if x.get("visibility", "public") == "public"],
        "consumed_or_removed_items": state.consumed_or_removed_items,
    }


def create_digest(state: GroupState, *, scene_label: str = "") -> dict:
    started = time.monotonic()
    with locks.get_state_lock(state.group_id):
        digest_id = _id()
        from app import checkpoints

        recent_checkpoints = [
            {key: item.get(key) for key in ("checkpoint_id", "label", "reason", "created_at")}
            for item in checkpoints.list_checkpoints(state.group_id)[-5:]
        ]
        private_combat = state.combat.to_dict()
        private_npc_abilities = {
            combatant.combatant_id: {
                "display_name": combatant.display_name,
                "card_id": combatant.enemy_card_id,
                "abilities": [ability.to_dict() for ability in card.abilities],
            }
            for combatant in state.combat.order
            if not combatant.is_pc
            for card in [state.combat.enemy_cards.get(combatant.enemy_card_id)]
            if card is not None
        }
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
            "recent_checkpoints": recent_checkpoints,
            "private": {
                "note": "以下內容僅供 Keeper 使用，不可透露給玩家。",
                "combat": private_combat,
                "npc_abilities": private_npc_abilities,
                "facts": [x for x in state.established_facts if x.get("visibility") == "kp_only"],
                "clues": [x for x in state.known_clues if x.get("visibility") == "kp_only"],
            },
        }
        with db.transaction() as conn:
            db.set_json_tx(conn, "scene_digests", f"{state.group_id}:{digest_id}", entry)
    _logger.info(
        "scene_digest_success group_id=%s digest_id=%s revision=%s timeline_id=%s duration_ms=%s",
        _log_group_id(state.group_id), digest_id, state.state_revision, entry["timeline_id"],
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


def clean_digest(group_id: str, digest_id: str) -> None:
    """Delete one digest under the same lock used to create state snapshots."""
    started = time.monotonic()
    try:
        with locks.get_state_lock(group_id), db.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM scene_digests WHERE key = ?",
                (f"{group_id}:{digest_id}",),
            ).fetchone()
            if row is None:
                raise KeyError(digest_id)
            db.delete_json_tx(conn, "scene_digests", f"{group_id}:{digest_id}")
    except Exception:
        _logger.exception(
            "scene_digest_clean_failure group_id=%s digest_id=%s duration_ms=%s transaction=rolled_back",
            _log_group_id(group_id), digest_id, int((time.monotonic() - started) * 1000),
        )
        raise
    _logger.info(
        "scene_digest_clean_success group_id=%s digest_id=%s duration_ms=%s",
        _log_group_id(group_id), digest_id, int((time.monotonic() - started) * 1000),
    )
