"""Lightweight, rule-based intent detection for player movement messages.

Deliberately NOT another LLM call — this project has been consistently
cost-conscious (prompt caching, capped scenario/log sizes, vision only on
low-text pages), and a classifier call on every single message would undercut
that for a feature (Map/Scene Engine — see app/scene_map.py) whose whole point
is resolving movement in code instead of spending a model call on it. Regex
over a fixed set of direction/verb words is enough to catch the common
phrasings; anything it misses just falls through to the Keeper narrating
movement itself, exactly like before this feature existed — see
app/commands.py's handling of a None return here.
"""
from __future__ import annotations

import re

# (pattern, direction, self_sufficient) — "self_sufficient" phrases already
# embed a movement verb themselves (往左、上樓、回頭...) so they trigger on
# their own; the plain position words (左手邊、後方...) need a nearby
# movement verb (see _MOVEMENT_VERB_RE) too, since on their own they could just
# be describing where something is without anyone moving ("他右手邊有把刀").
_DIRECTION_PATTERNS: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"往左|向左|左轉"), "left", True),
    (re.compile(r"左手邊|左邊|左側"), "left", False),
    (re.compile(r"往右|向右|右轉"), "right", True),
    (re.compile(r"右手邊|右邊|右側"), "right", False),
    (re.compile(r"樓上|往上|上樓"), "up", True),
    (re.compile(r"樓下|往下|下樓"), "down", True),
    (re.compile(r"回頭|往回走|往後|向後"), "back", True),
    (re.compile(r"背後|後方"), "back", False),
    (re.compile(r"直走|往前|向前"), "front", True),
    (re.compile(r"正前方|前面|前方"), "front", False),
]

_MOVEMENT_VERB_RE = re.compile(
    r"進入|走進|走向|前往|進去|走到|查看|檢查|打開|穿過|移動到|走回|回到|走|去(?!過)"
)

_CN_DIGIT = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORDINAL_RE = re.compile(r"第([一二三四五六七八九十\d]+)[個間扇]")

_ENTER_LOCATION_RE = re.compile(r"(?:進入|走進|前往|抵達|來到)([一-鿿]{1,12}?)(?:[，,。.！!？?、\s]|$)")


def _parse_ordinal(token: str) -> int:
    if token.isdigit():
        return int(token)
    return _CN_DIGIT.get(token, 1)


def parse_movement_intent(text: str) -> dict | None:
    """Returns {"relative_direction": "left"/"right"/"front"/"back"/"up"/"down",
    "order": int} when the message plausibly describes moving/looking in a
    direction with a movement-flavored verb nearby, else None. `order` is the
    1-indexed "第 N 個" ordinal if present, else 1 (the nearest/first matching
    exit)."""
    has_verb = bool(_MOVEMENT_VERB_RE.search(text))
    for pattern, direction, self_sufficient in _DIRECTION_PATTERNS:
        if pattern.search(text) and (self_sufficient or has_verb):
            m = _ORDINAL_RE.search(text)
            order = _parse_ordinal(m.group(1)) if m else 1
            return {"relative_direction": direction, "order": order}
    return None


def has_movement_verb(text: str) -> bool:
    """True if the message contains a movement-flavored verb even when
    parse_movement_intent couldn't extract a specific direction — e.g. the
    player named a destination room directly ("我去廚房看看") instead of
    describing it relative to where they're standing. Callers (see
    app/commands.py's _resolve_map_action) use this as the cheap gate before
    trying a room-name match, and only fall back to Scenario RAG (a real API
    call when embeddings are configured) if that also fails — this function
    itself makes no API call and costs nothing."""
    return bool(_MOVEMENT_VERB_RE.search(text))


def extract_entered_location(text: str) -> str | None:
    """Returns a raw location-name candidate when the message says the party
    is entering/arriving somewhere (e.g. "我進入燈塔" -> "燈塔"), for the
    caller to fuzzy-match against known scene_map location names. Returns None
    if no such phrasing is found; the candidate is unvalidated free text, not
    guaranteed to match anything."""
    m = _ENTER_LOCATION_RE.search(text)
    if not m:
        return None
    candidate = m.group(1).strip()
    return candidate or None
