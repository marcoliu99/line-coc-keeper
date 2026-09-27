"""Conservative movement hints. These never authorize or persist arrival.

Unknown phrasing remains with the existing Executor, which may propose an exact
IC source clause to the shared movement service without another classifier call.
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
    (re.compile(r"往上|上樓"), "up", True),
    (re.compile(r"往下|下樓"), "down", True),
    (re.compile(r"回頭|往回走|往後|向後"), "back", True),
    (re.compile(r"背後|後方"), "back", False),
    (re.compile(r"直走|往前|向前"), "front", True),
    (re.compile(r"正前方|前面|前方"), "front", False),
]

_MOVEMENT_VERB_RE = re.compile(
    r"離開|進入|走進|走向|前往|進去|走到|穿過|移動到|走回|回到|走|去(?!過)"
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
    for clause in movement_clauses(text):
        has_verb = bool(_MOVEMENT_VERB_RE.search(clause))
        matches = [(m.start(), direction) for pattern, direction, sufficient in _DIRECTION_PATTERNS
                   if (sufficient or has_verb) and (m := pattern.search(clause))]
        if matches:
            _, direction = min(matches)
            m = _ORDINAL_RE.search(clause)
            return {"relative_direction": direction, "order": _parse_ordinal(m.group(1)) if m else 1}
    return None


def movement_clauses(text: str) -> list[str]:
    """Conservative hints only; unknown language remains with Executor.

    Exclude quoted, negated, observational and interrogative clauses locally,
    so an unrelated negative clause cannot cancel an affirmative movement.
    """
    unquoted = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"', '', text)
    clauses = re.split(r'[，,。；;！!\n]|(?:然後|接著)', unquoted)
    return [c.strip(' （）()') for c in clauses if c.strip()
            and not re.search(r'不要|不想|不會|不去|別|不往|不向|沒有要|沒(?:有)?(?:往|向|走|去|進|離)|假如|如果|是否|能否|嗎|呢|[？?]|他說|她說|據說', c)
            and not re.match(r'\s*(?:他|她|他們|她們|有人|NPC)', c)
            and not re.search(r'查看|檢查|觀察|望向|看向|看著|打量|看看.*(?:左|右|樓上|樓下)', c)]


def has_movement_verb(text: str) -> bool:
    """True if the message contains a movement-flavored verb even when
    parse_movement_intent couldn't extract a specific direction — e.g. the
    player named a destination room directly ("我去廚房看看") instead of
    describing it relative to where they're standing. Callers (see
    app/commands.py's _resolve_map_action) use this as the cheap gate before
    trying a room-name match, and only fall back to Scenario RAG (a real API
    call when embeddings are configured) if that also fails — this function
    itself makes no API call and costs nothing."""
    return any(_MOVEMENT_VERB_RE.search(c) or parse_movement_intent(c) for c in movement_clauses(text))


def extract_entered_location(text: str) -> str | None:
    """Returns a raw location-name candidate when the message says the party
    is entering/arriving somewhere (e.g. "我進入燈塔" -> "燈塔"), for the
    caller to fuzzy-match against known scene_map location names. Returns None
    if no such phrasing is found; the candidate is unvalidated free text, not
    guaranteed to match anything."""
    m = next((m for c in movement_clauses(text) if (m := _ENTER_LOCATION_RE.search(c))), None)
    if not m:
        return None
    candidate = m.group(1).strip()
    return candidate or None
