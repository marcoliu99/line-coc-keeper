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

Deliberately out of scope for this first pass: multi-party split locations
(GroupState tracks one shared current_room_id, matching how split-party play
was already being handled narratively rather than with per-player state —
see app/keeper.py's system prompt), and non-compass exits like "up"/"down"
between floors, which are treated as their own direction tokens rather than
real 3D geometry.
"""
from __future__ import annotations

from typing import Any

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

# 8-way compass, plus up/down for stairs/floors. "N" is only ever a convention
# for "further into the page/building" — extraction doesn't have a real compass
# to read off a floor plan, it just needs to be internally consistent so two
# edges between the same two rooms agree on direction.
_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
_VERTICAL = ["U", "D"]

_MAP_TOOL = {
    "name": "report_scene_map",
    "description": (
        "回報這張圖片是否為平面圖／地圖。如果是，把圖上看得到的每個房間拆成節點，"
        "每個房門/通道拆成一條邊，並幫每條邊標一個『從起點房間看，通往終點房間的方位』"
        "（N/NE/E/SE/S/SW/W/NW，以圖片本身的上方當作 N 就好，只要整張圖前後一致即可；"
        "如果是樓梯往其他樓層，方位用 U（上樓）或 D（下樓））。只回報圖上實際畫出來、"
        "標示得出來的房間與連接關係，不要編造圖上沒有的房間、門，或看不出來的方位。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "is_map": {"type": "boolean", "description": "這張圖是不是平面圖/地圖（不是的話其他欄位可以留空）"},
            "location_name": {
                "type": "string",
                "description": "這張平面圖對應的地點名稱（例如「燈塔一樓」「莊園二樓」），劇本上下文有寫的話填，沒有就留空",
            },
            "entry_room_id": {"type": "string", "description": "從外面/正門進入時，第一個抵達的房間 id"},
            "rooms": {
                "type": "array",
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
                                        "description": "從這個房間出發，通往 to 房間的方位",
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
        "required": ["is_map"],
    },
}


def extract_scene_map(png_bytes: bytes) -> dict[str, Any] | None:
    """Best-effort: ask Claude whether a page image is a floor plan and, if so,
    extract its room graph. Returns None if unavailable (no API key), the page
    isn't a map, or the call fails for any reason — callers should treat that
    as "no structured map for this page" and fall back to the existing prose
    vision description (app/pdf_loader._vision_describe_image), not an error.
    """
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import base64

        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        image_b64 = base64.standard_b64encode(png_bytes).decode("utf-8")
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2048,
            tools=[_MAP_TOOL],
            tool_choice={"type": "tool", "name": "report_scene_map"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                    {"type": "text", "text": "這是一份 COC7e 劇本 PDF 裡的一頁圖片，請依工具欄位判斷並回報。"},
                ],
            }],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "report_scene_map":
                result = block.input
                if not result.get("is_map") or not result.get("rooms"):
                    return None
                return result
        return None
    except Exception:
        return None


_RELATIVE_TO_TURN = {"front": 0, "right": 2, "back": 4, "left": -2}  # steps around _COMPASS (45° each)


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


def get_room(scene_map: dict[str, Any], room_id: str) -> dict[str, Any] | None:
    for room in scene_map.get("rooms", []):
        if room.get("id") == room_id:
            return room
    return None


def resolve_move(
    scene_map: dict[str, Any], current_room_id: str, facing: str, relative_direction: str, order: int = 1
) -> dict[str, Any]:
    """The actual "player_location + facing + direction + door_index -> target
    room" resolution, computed in code before any LLM call is made — see this
    module's docstring. Returns {"ok": True, "room": {...}, "facing": <new
    absolute compass>} on success, or {"ok": False, "error": ...} when there's
    no matching exit (ambiguous, blocked, or the player named a direction that
    doesn't lead anywhere from here) — callers should fall back to letting the
    Keeper LLM handle the turn normally in that case, not treat it as a hard
    failure."""
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
    target = get_room(scene_map, matches[index]["to"])
    if target is None:
        return {"ok": False, "error": "地圖資料裡的出口指向一個不存在的房間"}

    return {"ok": True, "room": target, "facing": absolute}
