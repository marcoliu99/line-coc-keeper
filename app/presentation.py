"""What the player reads: internal tier names, ids and party size turned into the table's language, in one place.

The engine keeps English enums and opaque ids (a result tier is ``hard``, a check is ``check-<hex>``) because logs,
saved events and tests depend on them. They must not reach a player. Display sites call the label functions; the
supervisor also runs ``player_text`` over every reply as a last line, so a raw value that a model copied out of its
context is mapped or removed rather than shown. Everything here is idempotent and leaves ordinary prose alone.
"""
from __future__ import annotations

import re
from collections.abc import Collection

from app import config, dice

DIFFICULTY_ZH = {"regular": "一般", "hard": "困難", "extreme": "極難"}
_TIER_ENUM = "fumble|fail|regular|hard|extreme|critical"
_OUTCOME = re.compile(rf"(?<![A-Za-z])({_TIER_ENUM})(?![A-Za-z])\s*(成功|失敗)")
_LABELLED = re.compile(rf"(?P<label>(?:原始|所需|最終|實際)?(?:難度|等級)\s*[:：=]?\s*[「『\"]?)(?P<value>{_TIER_ENUM})(?![A-Za-z])")
# Internal ids: opaque handles for the engine, never something a player acts on.
_ID_LABELLED = re.compile(r"[（(\[【]?\s*(?:check_id|decision_id|event_id|timeline_id|source_check_id)\s*[=:：]\s*[\w.:-]+\s*[）)\]】]?")
_ID_BARE = re.compile(r"(?<![\w-])(?:check|decision)-[0-9a-f]{32}(?![\w-])")


def tier_label(value: str) -> str:
    """The table's name for a result tier; an unknown value is returned as it came."""
    return dice.TIER_ZH.get(value, value)


def difficulty_label(value: str) -> str:
    return DIFFICULTY_ZH.get(value, value)


def outcome_label(text: str) -> str:
    """``hard 成功`` -> ``困難成功``; ``regular 失敗`` -> ``失敗（擲出一般成功）`` (the task needed a higher tier)."""
    def replace(match: re.Match[str]) -> str:
        tier, verdict = match.group(1), match.group(2)
        if verdict == "成功":
            return tier_label(tier)
        return tier_label(tier) if tier in {"fail", "fumble"} else f"失敗（擲出{tier_label(tier)}）"

    return _OUTCOME.sub(replace, text)


def player_text(text: str) -> str:
    """``text`` with raw tier names mapped and internal ids removed, unless debugging asks to see them."""
    text = outcome_label(text)
    text = _LABELLED.sub(
        lambda m: m["label"] + (difficulty_label(m["value"]) if "難度" in m["label"] and m["value"] in DIFFICULTY_ZH
                                else tier_label(m["value"])),
        text,
    )
    if config.DEBUG_SHOW_INTERNAL_IDS:
        return text
    text = _ID_LABELLED.sub("", text)
    text = _ID_BARE.sub("", text)
    return re.sub(r"[ \t]+([，。；、）)])", r"\1", re.sub(r"（\s*）|\(\s*\)", "", text))


# --- party size ------------------------------------------------------------------------------------------------

_NUMERALS = {"二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CHINESE_DIGIT = {value: key for key, value in _NUMERALS.items() if key != "兩"}
_PARTY = re.compile(r"(?P<n>[二兩三四五六七八九十]|\d{1,2})(?P<unit>\s*[位名個])(?P<noun>調查員|探員|隊員|冒險者|隊友|夥伴)")


def enforce_party_size(text: str, actual: int) -> str:
    """Correct a claim that the party is larger than it is.

    The runtime knows how many investigators are playing; a scenario's pre-generated sheets or the model's own prose
    must not set it. Only a number above the real count is changed: a smaller one may be a subgroup ("兩位調查員留下").
    """
    if actual < 1:
        return text

    def fix(match: re.Match[str]) -> str:
        raw = match["n"]
        claimed = int(raw) if raw.isdigit() else _NUMERALS[raw]
        if claimed <= actual:
            return match.group(0)
        number = str(actual) if raw.isdigit() else _CHINESE_DIGIT.get(actual, str(actual))
        return f"{number}{match['unit']}{match['noun']}"

    return _PARTY.sub(fix, text)


def party_prompt(names: Collection[str]) -> str:
    """The narrator-facing statement of who is in the party."""
    roster = "、".join(names)
    return (
        f"【隊伍人數（權威）】目前共 {len(names)} 位調查員：{roster}。"
        "敘事不得依劇本內建的預設角色、人物卡數量或前文推測，說成其他人數；不需要時不必提人數。"
    )
