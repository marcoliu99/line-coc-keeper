"""Pull any built-in pregenerated investigator sheets out of a scenario's text.

Many published COC7e scenarios ship with ready-made pregens so a group can skip
character creation entirely. This does a single forced tool-call (via whichever
LLM_PROVIDER is configured — see app/providers/*.py's analyze_text) asking it to
report only what's explicitly written in the text (never invent numbers), so
`/coc pregens` can offer them instead of everyone rolling a fresh investigator.
"""
from __future__ import annotations

import re
from typing import Any

from app.config import LLM_PROVIDER
from app.models import Character, damage_bonus_and_build, move_rate
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.skill_aliases import canonical_skill_name

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

_REPORT_TOOL = {
    "name": "report_pregens",
    "description": (
        "回報在這份劇本文字中找到的『預製調查員角色卡』（pregenerated investigators）。"
        "只回報文本裡明確寫出的數值，不要自己編造或推算缺漏的欄位；如果劇本裡沒有附任何"
        "角色卡，pregens 給空陣列。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pregens": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "調查員姓名（維持原文，通常是專有名詞）"},
                        "occupation": {
                            "type": "string",
                            "description": (
                                "職業，請翻譯成繁體中文（例如英文劇本寫 'Artist' 就回報'藝術家'，"
                                "'Antiques Dealer' 回報'骨董商'），方便玩家之後用 /coc pc 角色名 職業 "
                                "直接指定這個職業。只翻譯這個欄位的用詞，不要連帶編造或更動任何數值。"
                            ),
                        },
                        "str_": {"type": "integer"}, "con": {"type": "integer"}, "siz": {"type": "integer"},
                        "dex": {"type": "integer"}, "app": {"type": "integer"}, "int_": {"type": "integer"},
                        "pow_": {"type": "integer"}, "edu": {"type": "integer"}, "luck": {"type": "integer"},
                        "hp_max": {"type": "integer"}, "mp_max": {"type": "integer"}, "san_max": {"type": "integer"},
                        "skills": {
                            "type": "object",
                            "description": "技能名稱對應百分比數值，只填劇本裡明確寫出的技能",
                            "additionalProperties": {"type": "integer"},
                        },
                        "notes": {"type": "string", "description": "簡短背景介紹，若劇本有寫的話"},
                        "secret_goal": {
                            "type": "string",
                            "description": (
                                "這位調查員的秘密目標／個人動機／hook，若劇本裡有寫的話（通常會有類似"
                                "『你的目標是...』『Your goal:』這種段落，只屬於這位調查員自己，"
                                "劇本設計上通常不會讓其他玩家知道）。翻譯成繁體中文，維持原意，"
                                "不要編造劇本沒寫的內容；沒有的話留空字串即可。"
                            ),
                        },
                        "key_connection": {
                            "type": "string",
                            "description": (
                                "劇本角色卡裡如果有標示『重要之人』『重要地點』『珍藏物品』之類，且明確寫"
                                "這是這位調查員最重要、失去會很痛的一段連結，抄錄下來翻成繁體中文（跟"
                                "secret_goal 不同，這個是公開的，不是秘密）；劇本沒有特別標示哪個最重要"
                                "的話，留空字串，不要自己猜一個出來。"
                            ),
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["pregens"],
    },
}


def extract_pregens(scenario_text: str) -> list[dict[str, Any]]:
    """Dispatches through LLM_PROVIDER (see app/providers/*.py's analyze_text
    functions) rather than being hard-coded to Anthropic — this used to always
    call ANTHROPIC_API_KEY regardless of which provider was actually
    configured for the Keeper (the same class of bug app/scene_map.py's
    analyze_page_image docstring describes fixing there), so /coc pregens
    could fail even with LLM_PROVIDER switched away from Anthropic."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None or not scenario_text.strip():
        return []

    result = provider.analyze_text(
        scenario_text,
        _REPORT_TOOL,
        "以下是一份 COC7e 劇本的文字內容。請找出裡面是否附有『預製調查員角色卡』"
        "（通常會列出角色姓名、職業、一串屬性數字如 STR/CON/SIZ/DEX/APP/INT/POW/EDU、"
        "以及一份技能列表）。用 report_pregens 工具回報結果。",
    )
    return (result or {}).get("pregens", []) or []


_SECTION_RE = re.compile(r"^【(.+?)】\s*$", re.MULTILINE)
_FIELD_RE = re.compile(r"^(.+?)[：:]\s*(.*)$")

# Chinese label -> Character/pregen attribute field, matched by substring so
# either the bare Chinese term or "力量 STR" (Chinese + English abbreviation on
# the same line) both work.
_ATTR_LABELS = [
    ("力量", "str_"), ("體質", "con"), ("體型", "siz"), ("敏捷", "dex"), ("外貌", "app"),
    ("智力", "int_"), ("意志", "pow_"), ("教育", "edu"), ("幸運", "luck"),
]

_NAME_PLACEHOLDER_RE = re.compile(r"玩家決定|由玩家")


def _split_sections(text: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group(1).strip()] = text[m.end() : end].strip()
    return sections


def _parse_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in body.splitlines():
        m = _FIELD_RE.match(line.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def _leading_number(value: str) -> int | None:
    """First number in a "60／30／12"-style value (COC7e's regular/hard/extreme
    triple — only the base value is needed, the other two are always
    derivable from it) or a plain "60"; None if nothing numeric is found."""
    m = re.match(r"\s*(\d+)", value.replace("/", "／").split("／")[0])
    return int(m.group(1)) if m else None


def _parse_weapon_ammo(weapons_text: str) -> dict[str, dict[str, int]]:
    """Pulls out just the ammo-tracked firearms from a 【武器】 section —
    e.g. ".38 左輪手槍\n技能：50／25／10\n...\n彈容量：6" — into {name:
    {"ammo": capacity, "ammo_max": capacity}} (starts fully loaded). A block
    whose first line is itself a "label：value" pair (e.g. "徒手：60／30／
    12", COC7e's unarmed entry) has no separate name line and is skipped —
    melee/thrown weapons have nothing to track anyway. Blocks are separated
    by a blank line, matching how these sheets lay out multiple weapons."""
    weapons: dict[str, dict[str, int]] = {}
    for block in re.split(r"\n\s*\n", weapons_text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        name = lines[0]
        if "：" in name or ":" in name:
            continue
        fields = _parse_fields("\n".join(lines[1:]))
        capacity_text = next((v for k, v in fields.items() if "彈容量" in k or "彈匣" in k), None)
        if capacity_text is None:
            continue
        capacity = _leading_number(capacity_text)
        if capacity:
            weapons[name] = {"ammo": capacity, "ammo_max": capacity}
    return weapons


def parse_role_sheet_text(text: str) -> dict[str, Any] | None:
    """Parses a hand-authored COC7e pregen character sheet in the
    【角色資料】/【屬性】/【技能】/... section format (see app/commands.py's
    handle_role_sheet_upload — triggered by a "role_"-prefixed .txt/.md
    upload) into the same pregen dict shape pregen_to_character below
    expects, as a deterministic alternative to extract_pregens' LLM-based
    extraction from raw scenario text.

    Deliberately doesn't parse hp_max/mp_max/san/move/damage_bonus/build from
    the text even though the sheet lists them — pregen_to_character already
    derives all of these from the nine base attributes via the same COC7e
    formulas the sheet itself was generated from, confirmed to reproduce the
    source numbers exactly against real example sheets; parsing them
    separately would only add another way for the two to silently disagree.

    Returns None if this doesn't look like a character sheet at all (no
    【屬性】 section) — callers should treat that as "not this format", not
    a partial/best-effort result to store anyway."""
    sections = _split_sections(text)
    if "屬性" not in sections:
        return None

    info = _parse_fields(sections.get("角色資料", ""))
    attrs = _parse_fields(sections["屬性"])

    name = info.get("姓名", "")
    if not name or _NAME_PLACEHOLDER_RE.search(name):
        name = ""
    occupation = info.get("職業", "")

    pregen: dict[str, Any] = {"name": name, "occupation": occupation}
    for label, field_name in _ATTR_LABELS:
        matched_value = next((v for k, v in attrs.items() if label in k), None)
        if matched_value is not None:
            number = _leading_number(matched_value)
            if number is not None:
                pregen[field_name] = number

    skills: dict[str, int] = {}
    for skill_name, value in _parse_fields(sections.get("技能", "")).items():
        number = _leading_number(value)
        if number is not None:
            skills[skill_name] = number
    pregen["skills"] = skills

    # Flavor fields our schema has no dedicated slot for, plus whole sections
    # (background, weapons, any scenario-specific extra section like "其他住
    # 戶") — folded into notes rather than dropped, matching this session's
    # "never silently discard authored content" approach for the map importer.
    notes_parts = []
    flavor = {k: v for k, v in info.items() if k not in ("姓名", "玩家", "職業") and v}
    if flavor:
        notes_parts.append("、".join(f"{k}：{v}" for k, v in flavor.items()))
    for section_name in ("角色背景", "武器"):
        if sections.get(section_name):
            notes_parts.append(f"【{section_name}】\n{sections[section_name]}")
    for section_name, body in sections.items():
        if section_name not in ("角色資料", "屬性", "技能", "角色背景", "武器", "角色扮演動機") and body:
            notes_parts.append(f"【{section_name}】\n{body}")
    pregen["notes"] = "\n\n".join(notes_parts)
    pregen["weapons"] = _parse_weapon_ammo(sections.get("武器", ""))

    pregen["secret_goal"] = sections.get("角色扮演動機", "")
    pregen["key_connection"] = ""
    return pregen


def _int_or(value: Any, default: int) -> int:
    return int(value) if isinstance(value, (int, float)) else default


def pregen_to_character(pregen: dict[str, Any], owner_id: str) -> Character:
    str_ = _int_or(pregen.get("str_"), 50)
    con = _int_or(pregen.get("con"), 50)
    siz = _int_or(pregen.get("siz"), 50)
    dex = _int_or(pregen.get("dex"), 50)
    app = _int_or(pregen.get("app"), 50)
    int_ = _int_or(pregen.get("int_"), 50)
    pow_ = _int_or(pregen.get("pow_"), 50)
    edu = _int_or(pregen.get("edu"), 50)
    luck = _int_or(pregen.get("luck"), 50)

    hp_max = _int_or(pregen.get("hp_max"), (con + siz) // 10) or (con + siz) // 10
    mp_max = _int_or(pregen.get("mp_max"), pow_ // 5) or pow_ // 5
    san_max = _int_or(pregen.get("san_max"), 99) or 99
    san = min(pow_, san_max)
    db, build = damage_bonus_and_build(str_, siz)
    move = move_rate(str_, dex, siz)

    # Canonicalize each incoming skill name (see app/skill_aliases.py) — the LLM
    # extraction translates whatever the scenario itself calls a skill into
    # Traditional Chinese, which won't necessarily match the exact spelling the
    # Keeper uses when it later calls skill_check for the same skill.
    skills = {
        canonical_skill_name(k): int(v)
        for k, v in (pregen.get("skills") or {}).items()
        if isinstance(v, (int, float))
    }
    skills.setdefault("閃避", dex // 2)
    skills.setdefault("母語", edu)

    return Character(
        name=pregen.get("name") or "無名調查員",
        owner_id=owner_id,
        occupation=pregen.get("occupation") or "未知",
        str_=str_, con=con, siz=siz, dex=dex, app=app, int_=int_, pow_=pow_, edu=edu, luck=luck,
        hp=hp_max, hp_max=hp_max,
        mp=mp_max, mp_max=mp_max,
        san=san, san_max=san_max,
        move=move, damage_bonus=db, build=build,
        skills=skills,
        weapons=pregen.get("weapons") or {},
        notes=pregen.get("notes", "") or "",
        key_connection=pregen.get("key_connection", "") or "",
        secret_goal=pregen.get("secret_goal", "") or "",
    )
