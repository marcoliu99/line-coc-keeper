"""Map uploads and the movement a player's words resolve to on the current scenario map.

Moved here unchanged from ``legacy_commands``; the map extraction and navigation algorithms live in
``app.scene_map``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from app import (
    intent_parser,
    locks,
    scenario_index,
    scenario_rag,
)
from app import scene_map as scene_map_engine
from app.config import SCENARIO_RAG_ENABLED
from app.models import (
    GroupState,
)
from app.repositories import state_transaction
from app.repositories.group_state import load_state
from app.services import mutation_admission

if TYPE_CHECKING:
    from app.commands.types import Reply


_logger = logging.getLogger(__name__)


@dataclass
class _MapActionResolution:
    context: dict | None = None
    needs_rag: bool = False


def _find_scene_map_by_location(state: GroupState, candidate: str) -> tuple[str, dict] | None:
    """Fuzzy match a raw "entering X" text candidate against the location_name
    of any map extracted from this scenario (see app/scene_map.py)."""
    norm = candidate.strip().lower()
    if not norm:
        return None
    for page_key, scene_map in state.scene_maps.items():
        name = str(scene_map.get("location_name", "")).strip().lower()
        if name and (norm == name or norm in name or name in norm):
            return page_key, scene_map
    return None


def _map_position_snapshot(state: GroupState, user_id: str) -> tuple[str, str, str]:
    return (
        state.current_map_page.get(user_id, ""),
        state.current_room_id.get(user_id, ""),
        state.party_facing.get(user_id, "N"),
    )


def _save_if_map_position_changed(state: GroupState, user_id: str, before: tuple[str, str, str]) -> None:
    if _map_position_snapshot(state, user_id) != before:
        state_transaction.commit_snapshot(state)


def resolve_map_action(conversation_id: str, user_id: str, text: str) -> dict | None:
    with locks.get_state_lock(conversation_id):
        mutation_admission.assert_admitted(conversation_id)
        state = load_state(conversation_id)
        before = _map_position_snapshot(state, user_id)
        result = _resolve_map_action_core(state, user_id, text, allow_rag=False)
        if not result.needs_rag:
            _save_if_map_position_changed(state, user_id, before)
            return result.context

        rag_page = state.current_map_page.get(user_id, "")
        rag_map = state.scene_maps.get(rag_page) if rag_page else None
        rag_text = state.scenario_text

    rag_room_id = None
    if rag_map and rag_text:
        rag_room = _find_room_via_rag(conversation_id, rag_text, rag_map, text)
        rag_room_id = rag_room.get("id") if rag_room else None

    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        before = _map_position_snapshot(state, user_id)
        result = _resolve_map_action_core(state, user_id, text, allow_rag=False)
        if result.needs_rag and rag_room_id and state.current_map_page.get(user_id, "") == rag_page:
            active_map = state.scene_maps.get(rag_page)
            rag_room = scene_map_engine.get_room(active_map, rag_room_id) if active_map else None
            if rag_room is not None:
                result = _resolve_map_action_core(state, user_id, text, allow_rag=False, rag_target_room=rag_room)
        _save_if_map_position_changed(state, user_id, before)
        return result.context


def _resolve_map_action_core(
    state: GroupState,
    user_id: str,
    text: str,
    *,
    allow_rag: bool = True,
    rag_target_room: dict | None = None,
) -> _MapActionResolution:
    """Runs the Map/Scene Engine (app/scene_map.py) against a player's raw
    message *before* any LLM call, exactly per this feature's whole point:
    the destination room is computed deterministically in code, not guessed
    by the Keeper from prose. Mutates state.current_map_page/current_room_id/
    party_facing **for this one user_id only** — see GroupState's own
    comment on why position tracking is per-character rather than a single
    shared party location: a scenario might split the group in ways this
    project has no reason to assume in advance, so each character just
    tracks their own position, and "the group" is whatever set of
    characters happens to share a (page, room) right now.

    Returns a small dict for the Keeper prompt (app/keeper.py's
    `resolved_location`), or None if the message didn't trigger a resolvable
    map action (no map loaded, no direction detected, or no matching exit) —
    callers should fall back to letting the Keeper narrate movement itself,
    exactly like before this feature existed."""
    resolved_room: dict | None = None
    current_page = state.current_map_page.get(user_id, "")
    current_room = state.current_room_id.get(user_id, "")
    facing = state.party_facing.get(user_id, "N")
    needs_rag = False

    location_candidate = intent_parser.extract_entered_location(text)
    if location_candidate:
        found = _find_scene_map_by_location(state, location_candidate)
        if found:
            page_key, scene_map = found
            if page_key != current_page:
                current_page = page_key
                facing = "N"
                current_room = scene_map.get("entry_room_id", "")
                state.current_map_page[user_id] = current_page
                state.current_room_id[user_id] = current_room
                state.party_facing[user_id] = facing
                resolved_room = scene_map_engine.get_room(scene_map, current_room)

    active_map = state.scene_maps.get(current_page) if current_page else None
    if active_map:
        movement = intent_parser.parse_movement_intent(text)
        if movement:
            result = scene_map_engine.resolve_move(
                state.scene_maps, current_page, current_room, facing, movement["relative_direction"], movement["order"],
            )
            if result["ok"]:
                if "map_key" in result:  # crossed into a different map — see scene_map.py's module docstring
                    state.current_map_page[user_id] = result["map_key"]
                state.current_room_id[user_id] = result["room"]["id"]
                state.party_facing[user_id] = result["facing"]
                resolved_room = result["room"]
            # result["ok"] is False (no matching exit): deliberately not
            # returned as an error here — let the Keeper's own dynamic prompt
            # (see build_dynamic_prompt) decide how to narrate a blocked or
            # ambiguous direction instead of the engine flatly refusing it.
        elif intent_parser.has_movement_verb(text):
            # No relative-direction word matched, but this still reads as a
            # movement attempt — most often the player named the destination
            # room directly ("我去廚房看看") instead of describing it by
            # direction. Try a free local match against the current map's own
            # room names first (no API call); only fall back to Scenario RAG
            # (a real embeddings call when configured — see scenario_rag.py)
            # if that comes up empty. This is deliberately best-effort: a miss
            # here just falls through to the Keeper narrating movement itself,
            # exactly like before this fallback existed.
            target_room = rag_target_room or scene_map_engine.find_room_by_text(active_map, text)
            if target_room is None and SCENARIO_RAG_ENABLED and state.scenario_text:
                if allow_rag:
                    target_room = _find_room_via_rag(state.group_id, state.scenario_text, active_map, text)
                else:
                    needs_rag = True
            if target_room is not None:
                state.current_room_id[user_id] = target_room["id"]
                state.party_facing[user_id] = "N"  # arbitrary jump, no direction to carry forward
                resolved_room = target_room

    if resolved_room is None:
        return _MapActionResolution(needs_rag=needs_rag)
    char = state.get_active_character(user_id)
    return _MapActionResolution(
        context={
            "character_name": char.name if char else "",
            "room_name": resolved_room.get("name", ""),
            "room_description": resolved_room.get("description", ""),
        },
        needs_rag=needs_rag,
    )


def _find_room_via_rag(group_id: str, scenario_text: str, scene_map: dict, text: str) -> dict | None:
    """Scenario RAG fallback for room-name resolution (see
    _resolve_map_action above) — RAG has no concept of room IDs, so the
    connection is made by searching the scenario text for the player's raw
    phrase and checking whether any of the current map's room names appear
    in whichever page(s) came back as relevant. This is genuinely a second
    real API call on top of the Keeper's own turn when embeddings are
    configured (see scenario_rag.py), so it's only reached after the free
    local name match in _resolve_map_action has already failed."""
    _logger.info("_find_room_via_rag query=%r", text)  # see app/keeper.py's search_scenario for why
    index = scenario_rag.get_index(group_id, scenario_text)
    results = scenario_rag.search(index, text, top_k=3)
    for result in results:
        room = scene_map_engine.find_room_by_text(scene_map, result["text"])
        if room:
            return room
    return None


@mutation_admission.guard_async_entry
async def handle_map_upload(
    conversation_id: str,
    reply: Reply,
    push: Reply,
    yaml_bytes: bytes,
    file_name: str,
) -> None:
    """A hand-authored alternative to app/scene_map.py's vision-extracted room
    graphs — same reply/push split as handle_pdf_upload above, though parsing
    a small YAML file is fast enough that both callbacks will usually land at
    the same time on any platform. Stored under a "custom_<filename>" key
    (never a bare digit, so it can't collide with a PDF page-number key) —
    `/coc enter custom_<filename>` loads it exactly like any extracted map."""
    try:
        data = yaml.safe_load(yaml_bytes)
    except yaml.YAMLError as exc:
        await reply(f"YAML 格式錯誤，請檢查語法：{exc}")
        return

    import_warnings: list[str] = []
    if isinstance(data, dict) and "nodes" in data and "rooms" not in data:
        # A different, richer authoring convention (node-graph: distances, named
        # routes, terrain, undirected "nearby" links) than this module's native
        # rooms/exits shape — see scene_map.import_node_graph's own docstring.
        data, import_warnings = scene_map_engine.import_node_graph(data)

    errors = scene_map_engine.validate_scene_map(data)
    if errors:
        error_list = "\n".join(f"・{e}" for e in errors)
        await reply(f"這份地圖資料有問題，尚未儲存：\n{error_list}")
        return

    key = f"custom_{Path(file_name).stem}"
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        previous = state.scene_maps.get(key)
        state.scene_maps[key] = data
        state.scenario_location_index = scenario_index.merge_scene_map_locations(
            state.scenario_location_index, state.scene_maps,
            replaced=str(previous.get("location_name") or "") if isinstance(previous, dict) else "")
        state_transaction.commit_snapshot(state)

    entry_room = scene_map_engine.get_room(data, data.get("entry_room_id", ""))
    entry_note = f"，入口房間「{entry_room['name']}」" if entry_room else ""
    warning_note = ""
    if import_warnings:
        warning_note = "\n\n⚠️ 轉換時有幾個地方略過了：\n" + "\n".join(f"・{w}" for w in import_warnings)
    await push(
        f"地圖「{data.get('location_name') or key}」已儲存（{len(data['rooms'])} 個房間{entry_note}）。\n"
        f"用「/coc enter {key}」載入這張地圖。" + warning_note
    )
