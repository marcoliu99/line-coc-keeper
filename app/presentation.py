"""What the player reads: internal tier names, ids and party size turned into the table's language, in one place.

The engine keeps English enums and opaque ids (a result tier is ``hard``, a check is ``check-<hex>``) because logs,
saved events and tests depend on them. They must not reach a player. Display sites call the label functions; the
supervisor also runs ``player_text`` over every reply as a last line, so a raw value that a model copied out of its
context is mapped or removed rather than shown. Everything here is idempotent and leaves ordinary prose alone.
Character display aliases are the exception: they are written when text is sent (``delivery.shown``), after the log
has kept the registered name.
"""
from __future__ import annotations

import functools
import re
from collections.abc import Collection

from app import config, dice

DIFFICULTY_ZH = {"regular": "一般", "hard": "困難", "extreme": "極難"}
_ASCII_WORD = "A-Za-z0-9"
_TIER_ENUM = "fumble|fail|regular|hard|extreme|critical"
_OUTCOME = re.compile(rf"(?<![A-Za-z])({_TIER_ENUM})(?![A-Za-z])\s*(成功|失敗)")
_LABELLED = re.compile(rf"(?P<label>(?:原始|所需|最終|實際)?(?:難度|等級)\s*[:：=]?\s*[「『\"]?)(?P<value>{_TIER_ENUM})(?![A-Za-z])")
# Internal ids: opaque handles for the engine, never something a player acts on. A label is removed with whatever value
# follows it, including none ("check_id=" with nothing after it was shown to players). Ids are ASCII, so the value stops
# at the first non-ASCII character instead of eating the sentence that follows ("check_id: 請擲骰"), and an empty label does
# not reach across a line break to take the next line's first word.
_ID_LABELLED = re.compile(
    r"[（(\[【]?\s*(?:check_id|decision_id|event_id|timeline_id|source_check_id)\s*[=:：][ \t]*[A-Za-z0-9_.:-]*[ \t]*[）)\]】]?")
# A Luck decision's id is its check's plus ":luck"; the suffix and any code quotes go with it, or the reply showed
# 「仍待你作答：`:luck`。」 (rerun8 turn 44).
_ID_BARE = re.compile(r"`?(?<![\w-])(?:check|decision)-[0-9a-f]{32}(?::luck)?(?![\w-])`?")


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


# A command runs to the end of its sentence (a newline or Chinese or ASCII sentence punctuation): placeholders and mentions ("<@玩家>", "[角色名]") sit between its words.
_COMMAND_SPAN = re.compile(r"/coc\b[^\n。！？；.!?;]*", re.IGNORECASE)
_COMMAND_WORD = re.compile(r"[a-z][a-z0-9_]*")


@functools.lru_cache(maxsize=1)
def command_words() -> frozenset[str]:
    """Every word of ``/coc`` syntax the help pages tell a player to type ("check", "luck", "roll", "status", ...)."""
    from app import (
        help_registration,  # imported when first needed: help pages sit above this module
    )

    words = {"coc"}
    for entry in help_registration.entries_for_policy():
        for line in (*entry.usage, *entry.examples):
            if "/coc" in line:
                words.update(_COMMAND_WORD.findall(re.sub(r"[|\[\]]", " ", line).lower()))
    return frozenset(words)


def character_aliases(text: str) -> str:
    """Registered character names written the way the table says them (``CHARACTER_DISPLAY_ALIASES``); applied by the transport on send.

    One pass over the text with the longest name first, so a name inside a longer configured one is left to the longer
    one and a replacement is never searched again. A name written in ASCII does not match inside other ASCII letters
    or digits. A name that is also a word of ``/coc`` syntax ("coc", "check", "roll", ...; see ``command_words``) is left alone
    inside a ``/coc ...`` command, because the command a player is told to type has to keep working. Chinese has no word boundary to check: a registered name that is part of a longer name nobody
    configured ("馬可" inside "馬可波羅") is replaced; list the longer name too, mapped to itself, to keep it.
    """
    aliases = config.CHARACTER_DISPLAY_ALIASES
    if not aliases:
        return text
    alternatives = []
    for name in sorted(aliases, key=len, reverse=True):
        before = rf"(?<![{_ASCII_WORD}])" if name[0].isascii() and name[0].isalnum() else ""
        after = rf"(?![{_ASCII_WORD}])" if name[-1].isascii() and name[-1].isalnum() else ""
        alternatives.append(f"{before}{re.escape(name)}{after}")
    spans = [(m.start(), m.end()) for m in _COMMAND_SPAN.finditer(text)]
    reserved = command_words() if spans else frozenset()

    def replace(match: re.Match[str]) -> str:
        name = match.group(0)
        if name.lower() in reserved and any(start <= match.start() < end for start, end in spans):
            return name  # the command the player is told to type keeps working
        return aliases[name]

    return re.compile("|".join(alternatives)).sub(replace, text)


def registered_name(typed: str, known: Collection[str]) -> str:
    """The registered name a player means by ``typed``, which may be the alias they were shown.

    Players copy what they read ("/coc switch 硬漢"), so a command that looks a character up by name accepts the alias
    too. A registered name always wins; an alias is used only when exactly one of ``known`` is shown under it.
    """
    if typed in known:
        return typed
    matches = [name for name in known if config.CHARACTER_DISPLAY_ALIASES.get(name) == typed]
    return matches[0] if len(matches) == 1 else typed


def leading_name(args: list[str], known: Collection[str], *, after: int) -> tuple[str, list[str]]:
    """Split ``args`` into a character name and the ``after`` or more arguments that follow it.

    A name or alias may itself contain spaces, so the longest leading run of arguments that is one of ``known`` (or the
    alias shown for one) wins. With no match the first argument is the name, as before.
    """
    for count in range(len(args) - after, 0, -1):
        candidate = registered_name(" ".join(args[:count]), known)
        if candidate in known:
            return candidate, args[count:]
    return args[0], args[1:]


def player_text(text: str) -> str:
    """``text`` with raw tier names mapped and internal ids removed, unless debugging asks to see them.

    Character names are not touched here: the saved log keeps the registered name, and the transport writes the table's
    alias on the way out (``app.discord_transport.delivery.shown``).
    """
    return _map_internal_terms(text)


def _map_internal_terms(text: str) -> str:
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
    text = re.sub(r"（\s*）|\(\s*\)", "", text)
    return re.sub(r"[ \t]+([，。；、）)])|[：:][ \t]*([。；，])", lambda m: m[1] or m[2], text)


# --- party size ------------------------------------------------------------------------------------------------

_NUMERALS = {"二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CHINESE_DIGIT = {value: key for key, value in _NUMERALS.items() if key != "兩"}
# Only a count the text ties to the player party: a cue that names the party, optionally a totalizing word, then the
# number. "敵方有六名探員" and "你們看到有六名探員" name some other group and are left alone.
_PARTY = re.compile(
    r"(?P<cue>(?:你們|我們|全隊|團隊|隊伍|小隊|調查小組|調查團|一行)(?:一共有|總共有|共有|一共|總共|總計|合計|全部|共|有|為|是|由)?\s*)"
    r"(?P<n>[二兩三四五六七八九十]|\d{1,2})(?P<unit>\s*[位名個])(?P<noun>調查員|探員|隊員|冒險者|隊友|夥伴)"
)


def enforce_party_size(text: str, actual: int) -> str:
    """Correct a claim that the party is larger than it is.

    The runtime knows how many investigators are playing; a scenario's pre-generated sheets or the model's own prose
    must not set it. Only a count tied to the player party ("你們六位調查員", "隊伍共有六名隊員") is considered, and
    only a number above the real count is changed: a smaller one may be a subgroup ("你們兩位調查員留下").
    """
    if actual < 1:
        return text

    def fix(match: re.Match[str]) -> str:
        raw = match["n"]
        claimed = int(raw) if raw.isdigit() else _NUMERALS[raw]
        if claimed <= actual:
            return match.group(0)
        number = str(actual) if raw.isdigit() else _CHINESE_DIGIT.get(actual, str(actual))
        return f"{match['cue']}{number}{match['unit']}{match['noun']}"

    return _PARTY.sub(fix, text)


def party_prompt(names: Collection[str]) -> str:
    """The narrator-facing statement of who is in the party."""
    roster = "、".join(names)
    return (
        f"【隊伍人數（權威）】目前共 {len(names)} 位調查員：{roster}。"
        "敘事不得依劇本內建的預設角色、人物卡數量或前文推測，說成其他人數；不需要時不必提人數。"
    )
