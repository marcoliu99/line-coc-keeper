"""Structured room-graph extraction and deterministic movement resolution.

The original real bug this replaces: a floor-plan page's room labels get
described in prose by app/pdf_loader.py's vision fallback ("進門右手邊是廚房,
左手邊是客廳..."), and the Keeper (an LLM) has to correctly re-parse that
prose, every single turn, to know what's where — which is exactly how a
player entering a door "expecting the bedroom on the right" got put in the
kitchen instead (see app/pdf_loader.py's module docstring).

This module extracts an explicit graph instead — rooms as nodes, doors/
passages as edges carrying an absolute compass direction — so "what's through
the door to the player's right" becomes a dict lookup the code performs
*before* ever calling the LLM, not a re-derivation the LLM has to get right
from paragraphs of text on every turn. The Keeper is then handed the already-
resolved destination and told not to override it (see app/keeper.py's
`resolved_location` prompt block and app/commands.py's use of resolve_move).

Deliberately out of scope for this first pass: non-compass exits like
"up"/"down" between floors, which are treated as their own direction tokens
rather than real 3D geometry. (Split-party locations ARE tracked — see
GroupState.current_room_id, keyed per character rather than one shared room.)

Cross-map exits: a scenario often has more than one floor plan (a building's
interior plus a separate map of the grounds/island it sits on, say) —
GroupState.scene_maps already stores each as its own independent entry, but
by default an exit's `to` only ever names a room *within the same map*, so
walking out the front door of one map has no way to land you on a room in
another. Writing `"to": "<other_map_key>:<room_id>"` instead of the plain
`"<room_id>"` on any exit crosses into that other map — resolve_move detects
the ":" and looks the target room up there instead, returning a "map_key"
field so the caller (app/commands.py's _resolve_map_action) knows to switch
GroupState.current_map_page for that character too, not just current_room_id.
`<other_map_key>` is whatever key that other map is stored under in
scene_maps — a PDF page number as a string, or a "custom_<filename>" key for
a hand-authored YAML upload (see handle_map_upload) — so cross-linking two
maps means knowing (or checking, e.g. via /coc where) the other one's key
before writing the exit.
"""
from __future__ import annotations

import difflib
from typing import Any

from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

# 16-way compass, plus up/down for stairs/floors. "N" is only ever a convention
# for "further into the page/building" — extraction doesn't have a real compass
# to read off a floor plan, it just needs to be internally consistent so two
# edges between the same two rooms agree on direction.
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
_VERTICAL = ["U", "D"]

_ANALYZE_TOOL = {
    "name": "analyze_page_image",
    "description": (
        "分析這是一份 COC7e 劇本 PDF 裡的一頁圖片，判斷它屬於 map（平面圖/地圖）、"
        "character_sheet（調查員角色卡/數值卡）、還是 other（插圖、封面、人物肖像等其他內容），"
        "並依分類回報對應欄位。只描述圖片裡實際看到的內容，不要編造或推測沒看到的細節。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "page_type": {
                "type": "string",
                "enum": ["map", "character_sheet", "other"],
                "description": (
                    "map：平面圖或地圖。character_sheet：調查員角色卡／數值卡（有 STR/DEX/CON/APP/POW/SIZ/"
                    "EDU/INT 等屬性欄位、HP/MP/SAN、或一排排技能名稱與百分比數字）。"
                    "other：插圖、封面、人物肖像等跟前兩者都無關的內容。"
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "依 page_type 決定寫法：\n"
                    "- map：詳細描述空間佈局與相對位置關係（例如：從正門進入後，右手邊第一個房間是什麼、"
                    "左手邊是什麼、走廊盡頭是什麼、樓上/樓下有哪些房間），盡量具體、按方位描述，方便之後"
                    "主持人依此正確描述場景給玩家，不要弄錯房間的相對位置。\n"
                    "- character_sheet：**逐一列出每一個看得到數字的欄位**，屬性、HP/MP/SAN/Luck、每一項"
                    "技能的名稱與百分比都要完整列出來，不要只說「列出了完整技能」卻不寫出實際數字，這種"
                    "摘要方式完全沒用；同時也要抄錄卡片上手寫或印刷填好的個人背景欄位，特別是角色姓名、"
                    "職業、個人特質、信念、重要他人、珍藏物品，以及任何「角色扮演鉤子／秘密目標／Your "
                    "goal」之類只屬於這個角色自己的動機段落，一字不漏抄下來；欄位是空白的就不用提。\n"
                    "- other：簡短描述畫面內容就好（一兩句話）。"
                ),
            },
            "location_name": {
                "type": "string",
                "description": "page_type 為 map 時，這張平面圖對應的地點名稱（例如「燈塔一樓」），劇本上下文有寫的話填，沒有就留空；其他 page_type 留空",
            },
            "entry_room_id": {"type": "string", "description": "page_type 為 map 時，從外面/正門進入時第一個抵達的房間 id；其他 page_type 留空"},
            "rooms": {
                "type": "array",
                "description": "page_type 為 map 時才需要填；其他 page_type 留空陣列",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "簡短英數 ID，例如 room_1，同一張圖內不要重複"},
                        "name": {"type": "string", "description": "房間名稱，例如「主臥室」"},
                        "description": {"type": "string", "description": "這個房間的簡短描述（圖上有標註的話）"},
                        "exits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "to": {"type": "string", "description": "這條邊通往哪個房間的 id"},
                                    "compass": {
                                        "type": "string",
                                        "enum": _COMPASS + _VERTICAL,
                                        "description": "從這個房間出發，通往 to 房間的方位（N/NE/E/SE/S/SW/W/NW，以圖片本身的上方當作 N 就好，只要整張圖前後一致即可；樓梯往其他樓層用 U 上樓或 D 下樓）",
                                    },
                                    "label": {"type": "string", "description": "這個連接的簡短描述，例如「木門」「走廊盡頭」"},
                                },
                                "required": ["to", "compass"],
                            },
                        },
                    },
                    "required": ["id", "name"],
                },
            },
        },
        "required": ["page_type", "description"],
    },
}


def analyze_page_image(png_bytes: bytes) -> tuple[str, dict[str, Any] | None]:
    """Single combined vision call — replaces what used to be two separate
    API calls per low-text page (a free-text description pass, and a forced
    tool call to check "is this a map"). Now one forced tool call does both,
    halving vision cost for scenarios with floor plans.

    Returns (description_text, scene_map_or_None):
    - description_text: prose description matching the old three-branch
      prompt (map spatial layout / character-sheet exhaustive number
      transcription / brief description for anything else) — this is what
      app/pdf_loader.py appends to the page's extracted text.
    - scene_map: the structured room graph (see resolve_move below) when the
      page was classified as a map with rooms, else None.
    Dispatches through LLM_PROVIDER (see app/providers/*.py's analyze_image
    functions) rather than being hard-coded to Anthropic — this was a real
    problem in practice: this call used to always use ANTHROPIC_API_KEY
    regardless of which provider was actually configured for the Keeper, so
    a scenario upload could still fail here even after switching LLM_PROVIDER
    away from Anthropic (e.g. because that account ran out of credit).

    On any failure (no API key for the configured provider, the call raised,
    or LLM_PROVIDER isn't a recognized provider) returns ("", None) —
    callers should fall back to local OCR for the text half; there is no
    fallback for the map half.
    """
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        return "", None
    result = provider.analyze_image(png_bytes, _ANALYZE_TOOL, "請依工具欄位分析這張圖片。")
    if not result:
        return "", None
    description = (result.get("description") or "").strip()
    scene_map = None
    if result.get("page_type") == "map" and result.get("rooms"):
        scene_map = result
    return description, scene_map


_RELATIVE_TO_TURN = {"front": 0, "right": 4, "back": 8, "left": -4}  # steps around _COMPASS (22.5° each)


_VERTICAL_ALIASES = {"up": "U", "down": "D"}


def resolve_direction(facing: str, relative: str) -> str:
    """Turn a player-relative direction (front/right/back/left/up/down) into
    an absolute compass direction (or "U"/"D") given the party's current
    facing. "up"/"down" and already-absolute compass/vertical tokens don't
    depend on facing, so they pass straight through (after normalizing the
    lowercase up/down aliases app/intent_parser.py produces)."""
    if relative in _VERTICAL_ALIASES:
        return _VERTICAL_ALIASES[relative]
    if relative in _VERTICAL or relative in _COMPASS:
        return relative
    if relative not in _RELATIVE_TO_TURN:
        return facing
    if facing not in _COMPASS:
        facing = "N"
    idx = _COMPASS.index(facing)
    return _COMPASS[(idx + _RELATIVE_TO_TURN[relative]) % len(_COMPASS)]


def validate_scene_map(data: Any) -> list[str]:
    """Structural validation for a hand-authored scene_map (see
    app/commands.py's handle_map_upload) — the same shape analyze_page_image
    above produces, just typed by a human instead of extracted by vision.
    Returns a list of human-readable problems (empty = valid); callers should
    refuse to store anything if this is non-empty rather than silently
    accepting a map resolve_move would later choke on."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["最外層必須是一個物件（YAML mapping），不是列表或純文字"]

    rooms = data.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        errors.append("rooms 必須是至少一筆的房間列表")
        return errors  # nothing else here is checkable without rooms

    seen_ids: set[str] = set()
    valid_compass = set(_COMPASS) | set(_VERTICAL)
    for i, room in enumerate(rooms):
        if not isinstance(room, dict) or not room.get("id") or not room.get("name"):
            errors.append(f"第 {i + 1} 個房間缺少必要欄位 id/name")
            continue
        room_id = room["id"]
        if room_id in seen_ids:
            errors.append(f"房間 id「{room_id}」重複")
        seen_ids.add(room_id)

    for room in rooms:
        if not isinstance(room, dict):
            continue
        for exit_ in room.get("exits", []) or []:
            if not isinstance(exit_, dict):
                errors.append(f"房間「{room.get('id')}」有一個格式錯誤的 exit")
                continue
            if exit_.get("compass") not in valid_compass:
                errors.append(f"房間「{room.get('id')}」的 exit 方位「{exit_.get('compass')}」不是合法值（{'/'.join(sorted(valid_compass))}）")
            to_id = exit_.get("to")
            if isinstance(to_id, str) and ":" in to_id:
                # Cross-map exit ("<other_map_key>:<room_id>") — can't check the
                # target map/room actually exists here, since this function only
                # ever sees one map's own data, and the referenced map might
                # legitimately not be uploaded yet (or this one might be
                # uploaded first). Just check the format isn't degenerate
                # (neither side of the colon empty); resolve_move reports a
                # clear error at actual move time if the target turns out
                # missing.
                other_map_key, _, other_room_id = to_id.partition(":")
                if not other_map_key or not other_room_id:
                    errors.append(f"房間「{room.get('id')}」的跨地圖 exit「{to_id}」格式錯誤，應為「地圖key:房間id」")
            elif to_id not in seen_ids:
                errors.append(f"房間「{room.get('id')}」的 exit 指向不存在的房間「{to_id}」")

    entry_room_id = data.get("entry_room_id")
    if entry_room_id and entry_room_id not in seen_ids:
        errors.append(f"entry_room_id「{entry_room_id}」不是 rooms 裡任何一個房間的 id")

    return errors


# English direction words (as used by a hand-authored node-graph map — see
# import_node_graph below) -> this module's own compass tokens.
_DIRECTION_WORD_TO_COMPASS = {
    "north": "N", "north_northeast": "NNE", "northeast": "NE", "east_northeast": "ENE",
    "east": "E", "east_southeast": "ESE", "southeast": "SE", "south_southeast": "SSE",
    "south": "S", "south_southwest": "SSW", "southwest": "SW", "west_southwest": "WSW",
    "west": "W", "west_northwest": "WNW", "northwest": "NW", "north_northwest": "NNW",
    "up": "U", "down": "D",
}


def import_node_graph(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Converts a hand-authored node-graph map (top-level `nodes`: a dict of
    room-slug -> {name, connections: [{to, direction, ...}], adjacent: [...]})
    into this module's canonical scene_map shape (location_name/entry_room_id/
    rooms/exits — see validate_scene_map). This is a *different*, richer
    authoring convention than a native rooms/exits YAML (distances, named
    routes, terrain, undirected "nearby" links) — see app/commands.py's
    handle_map_upload for how the two are told apart.

    A room's id is its own dict key in `nodes` (not the separate, sometimes-
    missing/inconsistent `id` field some nodes carry) — always present, never
    colliding. The first key in `nodes` (dict/YAML insertion order) becomes
    entry_room_id, since this format has no explicit entry marker.

    Fields this module's schema has no dedicated slot for (distance_m, route,
    terrain) are folded into the exit's label text rather than silently
    dropped. `adjacent` entries carry no direction at all, so they can't
    become a traversable exit — folded into the room's description as a
    "鄰近地點" line instead; still reachable in play via find_room_by_text's
    name-based fallback, just not via directional movement.

    Returns (scene_map, warnings) — warnings for anything skipped (an
    unrecognized direction word, a malformed connection), never a hard
    failure; callers should still run validate_scene_map on the result."""
    warnings: list[str] = []
    nodes = data.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return {}, ["nodes 必須是至少一筆的節點物件"]

    node_keys = list(nodes.keys())
    rooms: list[dict[str, Any]] = []
    for key in node_keys:
        node = nodes[key]
        if not isinstance(node, dict):
            warnings.append(f"節點「{key}」格式錯誤，已略過")
            continue

        exits: list[dict[str, Any]] = []
        for conn in node.get("connections", []) or []:
            if not isinstance(conn, dict) or not conn.get("to"):
                warnings.append(f"節點「{key}」有一筆格式錯誤的 connection，已略過")
                continue
            direction_word = str(conn.get("direction", "")).strip().lower()
            compass = _DIRECTION_WORD_TO_COMPASS.get(direction_word)
            if compass is None:
                warnings.append(f"節點「{key}」的連結方向「{conn.get('direction')}」無法辨識，已略過這條連結")
                continue
            label_parts = [str(conn[f]) for f in ("route", "terrain") if conn.get(f)]
            if conn.get("distance_m") is not None:
                label_parts.append(f"約{conn['distance_m']}公尺")
            exits.append({"to": conn["to"], "compass": compass, "label": "，".join(label_parts)})

        description = str(node.get("description", "") or "")
        adjacent = [str(a) for a in (node.get("adjacent") or [])]
        if adjacent:
            nearby_line = f"鄰近地點：{'、'.join(adjacent)}"
            description = f"{description}\n{nearby_line}".strip()

        rooms.append({"id": key, "name": str(node.get("name", key)), "description": description, "exits": exits})

    scene_map = {
        "location_name": str(data.get("map", "")),
        "entry_room_id": node_keys[0],
        "rooms": rooms,
    }
    return scene_map, warnings


def get_room(scene_map: dict[str, Any], room_id: str) -> dict[str, Any] | None:
    for room in scene_map.get("rooms", []):
        if room.get("id") == room_id:
            return room
    return None


_FUZZY_ROOM_MATCH_THRESHOLD = 0.6  # difflib ratio — calibrated against real
# near-miss/typo pairs ("藏書室"/"藏書間" -> 0.67, want to match) vs. genuine
# synonyms with different vocabulary ("地下室"/"地窖" -> 0.40, must NOT match
# here — that needs actual semantic understanding, i.e. Scenario RAG below).


def find_room_by_text(scene_map: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Does any of this map's room names appear (exactly, or as a close
    near-miss) in `text`? Used when a player names a destination room
    directly (e.g. "我去廚房看看") rather than describing it by relative
    direction, or against a Scenario RAG search result's text — see
    app/commands.py's _resolve_map_action, which tries a direct match here
    first and only falls back to a Scenario RAG search when that fails.

    Two passes:
    1. Exact substring — cheap, unambiguous, the common case.
    2. Approximate — a sliding window (roughly the room name's own length)
       across `text`, scored by difflib's character-similarity ratio. This
       is meant to forgive typos/minor phrasing near-misses ("藏書間" for a
       room actually named "藏書室"), not real synonyms with genuinely
       different vocabulary ("地窖" for "地下室") — those score too low to
       clear _FUZZY_ROOM_MATCH_THRESHOLD on purpose, since that's a semantic
       gap only Scenario RAG (real language understanding) can close, not
       string similarity."""
    rooms = scene_map.get("rooms", [])

    for room in rooms:
        name = str(room.get("name", "")).strip()
        if name and name in text:
            return room

    best_room, best_ratio = None, 0.0
    for room in rooms:
        name = str(room.get("name", "")).strip()
        if not name:
            continue
        for window_len in (len(name) - 1, len(name), len(name) + 1):
            if window_len < 1:
                continue
            for i in range(max(1, len(text) - window_len + 1)):
                window = text[i : i + window_len]
                ratio = difflib.SequenceMatcher(None, name, window).ratio()
                if ratio > best_ratio:
                    best_ratio, best_room = ratio, room
    if best_ratio >= _FUZZY_ROOM_MATCH_THRESHOLD:
        return best_room
    return None


def resolve_move(
    scene_maps: dict[str, dict[str, Any]],
    current_map_key: str,
    current_room_id: str,
    facing: str,
    relative_direction: str,
    order: int = 1,
) -> dict[str, Any]:
    """The actual "player_location + facing + direction + door_index -> target
    room" resolution, computed in code before any LLM call is made — see this
    module's docstring. Returns {"ok": True, "room": {...}, "facing": <new
    absolute compass>, "map_key": <only present if the move crossed into a
    different map>} on success, or {"ok": False, "error": ...} when there's
    no matching exit (ambiguous, blocked, or the player named a direction that
    doesn't lead anywhere from here) — callers should fall back to letting the
    Keeper LLM handle the turn normally in that case, not treat it as a hard
    failure.

    Takes the *whole* scene_maps dict (keyed by map key — a page number
    string or a "custom_<filename>" key, same as GroupState.scene_maps)
    rather than a single map, so an exit's `to` can point at a room in a
    *different* map: `"to": "<other_map_key>:<room_id>"` instead of the
    plain `"<room_id>"` used for a same-map exit — see this module's
    docstring for how a map author writes one. Most calls still only ever
    touch `current_map_key`'s own map; the cross-map lookup only kicks in
    when an exit's `to` actually contains that "<map_key>:" prefix."""
    scene_map = scene_maps.get(current_map_key)
    if scene_map is None:
        return {"ok": False, "error": f"目前所在地圖 {current_map_key!r} 不存在"}

    room = get_room(scene_map, current_room_id)
    if room is None:
        return {"ok": False, "error": f"目前所在房間 {current_room_id!r} 不在這張地圖裡"}

    absolute = resolve_direction(facing, relative_direction)
    matches = [e for e in room.get("exits", []) if e.get("compass") == absolute]
    if not matches:
        return {"ok": False, "error": f"「{room.get('name', current_room_id)}」沒有通往 {absolute} 方向的出口"}

    index = max(1, order) - 1
    if index >= len(matches):
        index = len(matches) - 1
    to_ref = matches[index]["to"]

    if ":" in to_ref:
        target_map_key, target_room_id = to_ref.split(":", 1)
        target_map = scene_maps.get(target_map_key)
        if target_map is None:
            return {"ok": False, "error": f"地圖資料裡的跨地圖出口指向不存在的地圖「{target_map_key}」"}
        target = get_room(target_map, target_room_id)
        if target is None:
            return {"ok": False, "error": "跨地圖出口指向一個不存在的房間"}
        return {"ok": True, "room": target, "facing": absolute, "map_key": target_map_key}

    target = get_room(scene_map, to_ref)
    if target is None:
        return {"ok": False, "error": "地圖資料裡的出口指向一個不存在的房間"}
    return {"ok": True, "room": target, "facing": absolute}
