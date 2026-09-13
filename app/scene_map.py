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
"""
from __future__ import annotations

from typing import Any

from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

# 8-way compass, plus up/down for stairs/floors. "N" is only ever a convention
# for "further into the page/building" — extraction doesn't have a real compass
# to read off a floor plan, it just needs to be internally consistent so two
# edges between the same two rooms agree on direction.
_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
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


def find_room_by_text(scene_map: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Fuzzy: does any of this map's room names appear as a substring of
    `text`? Used when a player names a destination room directly (e.g. "我去
    廚房看看") rather than describing it by relative direction, or against a
    Scenario RAG search result's text — see app/commands.py's
    _resolve_map_action, which tries a direct match here first and only
    falls back to a Scenario RAG search when that fails."""
    for room in scene_map.get("rooms", []):
        name = str(room.get("name", "")).strip()
        if name and name in text:
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
