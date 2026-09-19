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

from app import character_matcher, dictionary
from app.config import LLM_PROVIDER
from app.models import BASE_SKILLS, Character, _roll, damage_bonus_and_build, move_rate
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
                        "occupation_original": {
                            "type": "string",
                            "description": "職業在劇本原文裡的寫法，一字不改照抄（例如劇本是英文就填 'Artist'，"
                            "已經是中文就跟 occupation_translated 填一樣的值）。",
                        },
                        "occupation_translated": {
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
                            "description": "技能名稱對應百分比數值，只填劇本裡明確寫出的技能。key 請直接"
                            "使用 skill_translations 裡對應的『中文翻譯』那一邊（不是原文），"
                            "跟 skill_translations 的翻譯值要完全一致。",
                            "additionalProperties": {"type": "integer"},
                        },
                        "skill_translations": {
                            "type": "object",
                            "description": "skills 裡每一個技能名稱，對應它在劇本原文裡的寫法——key 是"
                            "翻譯後的中文技能名（要跟 skills 的 key 完全一致），value 是劇本原文的寫法"
                            "（例如劇本是英文就填 'Spot Hidden'；劇本本來就是中文，value 跟 key 填一樣的"
                            "值）。這是為了讓系統學會這個劇本用的技能譯名，之後遇到同樣的原文能直接辨識。",
                            "additionalProperties": {"type": "string"},
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
                        "extra_fields": {
                            "type": "object",
                            "description": (
                                "角色卡上其他劇本自訂欄位，不能丟棄。例如信念、重要之人、住址、組織、"
                                "裝備備註、特殊能力、公開秘密、聯絡方式或任何不同劇本新增的欄位。key 使用"
                                "欄位原名，value 保留完整內容；不要把未看見的內容補出來。"
                            ),
                            "additionalProperties": {},
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["pregens"],
    },
}


# Every top-level scalar property _REPORT_TOOL's schema actually declares —
# used by _clean_pregen_keys below to recognize a hallucinated variant of one
# of these (see that function's own docstring). Deliberately excludes
# "skills"/"skill_translations" (nested objects, handled separately — a
# malformed key there just means one skill doesn't translate, not a whole
# attribute silently defaulting to a wrong number) and "name"/"notes"/
# "secret_goal"/"key_connection" (free text, no numeric default to protect).
_EXPECTED_PREGEN_ATTR_KEYS = (
    "str_", "con", "siz", "dex", "app", "int_", "pow_", "edu", "luck",
    "hp_max", "mp_max", "san_max",
)

_KNOWN_PREGEN_KEYS = {
    "name", "occupation", "occupation_original", "occupation_translated",
    "str_", "con", "siz", "dex", "app", "int_", "pow_", "edu", "luck",
    "hp_max", "mp_max", "san_max", "skills", "skill_translations", "notes",
    "secret_goal", "key_connection", "extra_fields", "source", "claimed_by",
    "weapons", "carried_items",
}


def _preserve_extra_fields(pregen: dict[str, Any]) -> None:
    """Move provider-specific top-level fields into a JSON-safe bucket."""
    extras = dict(pregen.get("extra_fields") or {}) if isinstance(pregen.get("extra_fields"), dict) else {}
    for key in list(pregen):
        if key not in _KNOWN_PREGEN_KEYS:
            value = pregen.pop(key)
            if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
                extras[str(key)] = value
    if extras:
        pregen["extra_fields"] = extras


def _clean_pregen_keys(pregen: dict[str, Any]) -> None:
    """Defends against occasional LLM tool-call noise where a property name
    comes back with stray punctuation stuck to it — observed directly during
    testing (2026-09-17): a real extraction returned "edu?:" instead of
    "edu", which pregen_to_character's _int_or then silently treated as a
    missing attribute and defaulted to 50, discarding whatever number the
    LLM actually read off the page. Mutates `pregen` in place: strips common
    stray punctuation from each of _EXPECTED_PREGEN_ATTR_KEYS' names and, if
    the result exactly matches a key actually present in `pregen`, renames it
    back to the correct schema key (only when the correct key doesn't
    already exist — never overwrites a legitimately-present correct value
    with a stray duplicate's). Every other key is left completely untouched:
    this is purely a punctuation-noise fix for the fixed, known attribute
    names, never a fuzzy/typo matcher that could misfire on a genuinely
    different field."""
    renames = {}
    for expected in _EXPECTED_PREGEN_ATTR_KEYS:
        if expected in pregen:
            continue
        for key in pregen:
            if key.strip(" \t?:：？，,.") == expected:
                renames[key] = expected
                break
    for old_key, new_key in renames.items():
        pregen[new_key] = pregen.pop(old_key)


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
        "以及一份技能列表）。職業請同時回報原文（occupation_original）跟中文翻譯"
        "（occupation_translated）；每個技能除了 skills 裡的中文名稱＋數值，也請在"
        "skill_translations 裡附上這個技能在劇本原文裡的寫法，讓系統可以學會這個劇本用的"
        "譯名。用 report_pregens 工具回報結果。",
    )
    pregens = (result or {}).get("pregens", []) or []
    for pregen in pregens:
        _clean_pregen_keys(pregen)
        _preserve_extra_fields(pregen)
        # Tagged "llm_extracted" vs parse_role_sheet_text's "manual" above —
        # see that function's own comment for why reconciliation needs this.
        pregen["source"] = "llm_extracted"
        _learn_translations_from_pregen(pregen)
        pregen["skills"] = _translate_skill_names(pregen.get("skills") or {})
    return pregens


def _learn_translations_from_pregen(pregen: dict[str, Any]) -> None:
    """Captures the LLM's own original<->translated pairing for THIS
    extraction (see _REPORT_TOOL's occupation_original/occupation_translated
    and skill_translations fields) and teaches it to app/dictionary.py
    *before* _translate_skill_names runs below — so a term this exact
    scenario just used is already learned by the time the lookup-only
    translation pass needs it, no second LLM call required. Without this,
    dictionary.py's learn_skill/learn_occupation were dead code: nothing in
    the project ever called them, so the dictionary could only ever be as
    good as its initial seed (see dictionary.py's _SEED_SKILLS) and would
    never actually grow from real scenario extractions the way its own
    "self-learning" name promises.

    Collapses occupation_original/occupation_translated back into a single
    "occupation" key afterward — every other caller (pregen_to_character,
    character_matcher, reconcile_pregen_into_pool, display text) expects
    that one field, not the two-field split this schema needs only to
    capture the pairing."""
    original = (pregen.pop("occupation_original", "") or "").strip()
    translated = (pregen.pop("occupation_translated", "") or "").strip()
    if original and translated and original.lower() != translated.lower():
        dictionary.learn_occupation(original, translated)
    pregen["occupation"] = translated or original or pregen.get("occupation", "")

    for translated_skill, original_skill in (pregen.pop("skill_translations", None) or {}).items():
        translated_skill, original_skill = (translated_skill or "").strip(), (original_skill or "").strip()
        if original_skill and translated_skill and original_skill.lower() != translated_skill.lower():
            dictionary.learn_skill(original_skill, translated_skill)


def _translate_skill_names(skills: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize extracted names and merge duplicate aliases safely."""
    merged: dict[str, Any] = {}
    for name, value in skills.items():
        canonical = canonical_skill_name(name)
        if canonical in merged and isinstance(merged[canonical], (int, float)) and isinstance(value, (int, float)):
            merged[canonical] = max(merged[canonical], value)
        else:
            merged[canonical] = value
    return merged


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


# Section headers this project treats as interchangeable (see docs/
# character_and_dictionary_system_spec.md's Module 2) — GMs mix weapons and
# plain items under whichever of these titles they feel like using (a
# "裝備" list often has a pistol's stats sitting right next to a flashlight),
# so _classify_item_blocks below classifies per-BLOCK by content, not by
# which of these headers a block happened to sit under.
_ITEM_SECTION_NAMES = ("武器", "裝備", "隨身物品", "攜帶物品", "個人物品", "道具")

# Broad enough to recognize "this block is a weapon" even with zero
# structured fields (a bare "左輪手槍" line, nothing else) — kept separate
# from _AMMO_CATEGORY_KEYWORDS below, since classification only needs to know
# "is this a gun", not which of the 5 tracked categories it is.
_WEAPON_NAME_KEYWORDS = (
    "武器", "槍", "左輪", "半自動", "霰彈", "步槍", "來福", "衝鋒",
    "weapon", "pistol", "revolver", "rifle", "shotgun", "smg",
)

# A block's "技能" field naming one of these implies a combat/weapon skill —
# COC7e's own official skill names for anything you'd carry a weapon to use.
_COMBAT_SKILL_HINTS = ("射擊", "格鬥", "投擲")

# Generic-category keyword -> canonical ammo-table key (see _AMMO_TABLE). A
# bare "手槍" with neither "左輪" nor "半自動" is deliberately NOT mapped
# here — COC7e's own skill list treats revolvers and semi-autos as the same
# skill, but they don't share a magazine size, so there's no single
# defensible default to guess; such a weapon still gets recorded in
# `weapons`, just without ammo tracking, same as a name this table has never
# heard of at all.
_AMMO_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("左輪", "revolver"), ("revolver", "revolver"),
    ("半自動", "semi_auto_pistol"),
    ("霰彈", "shotgun"), ("shotgun", "shotgun"),
    ("衝鋒", "smg"), ("smg", "smg"),
    ("步槍", "rifle"), ("來福", "rifle"), ("rifle", "rifle"),
]

# Reasonable default magazine/cylinder capacities for a GENERIC category name
# with no specific model given, split by setting period (see GroupState.era)
# — the same generic term implies a different real gun (and a different
# capacity) in a 1920s game than a modern one. These are approximate,
# representative values for the category, not the exact stats of any one
# official COC7e weapon entry; a sheet naming a specific model with its own
# explicit ammo capacity always overrides this (see _classify_item_blocks).
_AMMO_TABLE: dict[str, dict[str, int]] = {
    "1920s": {
        "revolver": 6,          # .38/.45 break-top or swing-out revolver
        "semi_auto_pistol": 7,  # Colt M1911-era semi-auto
        "shotgun": 2,           # double-barrel break-action
        "rifle": 5,             # bolt-action internal magazine
        "smg": 20,              # Thompson-style box magazine
    },
    "modern": {
        "revolver": 6,           # capacity is largely unchanged across eras
        "semi_auto_pistol": 15,  # Glock-style
        "shotgun": 5,            # pump-action tube magazine
        "rifle": 10,             # common bolt-action/hunting magazine
        "smg": 30,               # MP5-style box magazine
    },
}


def _ammo_category(name: str) -> str | None:
    lowered = name.lower()
    for keyword, category in _AMMO_CATEGORY_KEYWORDS:
        if keyword in name or keyword in lowered:
            return category
    return None


def _looks_like_weapon(name: str, fields: dict[str, str]) -> bool:
    lowered_name = name.lower()
    if any(kw in name or kw in lowered_name for kw in _WEAPON_NAME_KEYWORDS):
        return True
    skill_value = next((v for k, v in fields.items() if "技能" in k), None)
    return bool(skill_value) and any(hint in (skill_value or "") for hint in _COMBAT_SKILL_HINTS)


def _classify_item_blocks(text: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Splits `text` (the combined body of every recognized weapon/item
    section — see _ITEM_SECTION_NAMES) into blank-line-separated blocks and
    classifies each one as a weapon or a plain carried item by CONTENT.
    Returns (weapons, carried_items):

    - weapons: {name: {"ammo": int, "ammo_max": int}} when the block gave an
      explicit ammo capacity; {name: {"ammo_category": str}} for a
      recognized gun with no stated ammo (resolved to a real number later —
      see pregen_to_character, which is the first place era is known);
      {name: {}} for a weapon this table doesn't recognize at all (still
      recorded, just without ammo tracking — better than the previous
      behavior of dropping it from `weapons` entirely).
    - carried_items: plain item names for every block that isn't a weapon —
      previously these (along with unrecognized ammo-less weapons) only ever
      showed up as raw text in `notes`, a STATIC field the Keeper only sees
      once at character creation; carried_items is refreshed into the
      Keeper's prompt every turn (see app/models.py's Character), which is
      what actually fixes players' items being "forgotten" over a long game.
    """
    weapons: dict[str, dict[str, Any]] = {}
    carried_items: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        # Two authoring conventions share this same combined text (see this
        # function's own docstring): a multi-line weapon record (name, then
        # "label：value" fields), or a flat list of plain item names, one per
        # line, with no fields at all — the common way to write "隨身物品"/
        # "道具". A block with zero colons anywhere is the latter: treat
        # every line as its own independent entry, not "name + N ignored
        # garbage lines" (which is what re-using the single-record path
        # below would silently do, dropping every line after the first).
        if not any("：" in ln or ":" in ln for ln in lines):
            for item_name in lines:
                if _looks_like_weapon(item_name, {}):
                    category = _ammo_category(item_name)
                    weapons[item_name] = {"ammo_category": category} if category else {}
                else:
                    carried_items.append(item_name)
            continue

        name = lines[0]
        if "：" in name or ":" in name:
            # No separate name line (e.g. "徒手：60／30／12", COC7e's unarmed
            # entry) — nothing to track as a discrete item either.
            continue
        fields = _parse_fields("\n".join(lines[1:]))
        capacity_text = next((v for k, v in fields.items() if "彈容量" in k or "彈匣" in k), None)
        capacity = _leading_number(capacity_text) if capacity_text is not None else None

        if capacity:
            weapons[name] = {"ammo": capacity, "ammo_max": capacity}
        elif _looks_like_weapon(name, fields):
            category = _ammo_category(name)
            weapons[name] = {"ammo_category": category} if category else {}
        else:
            carried_items.append(name)
    return weapons, carried_items


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

    # Tagged "manual" vs extract_pregens' "llm_extracted" below (see docs/
    # character_and_dictionary_system_spec.md's Module 4) — reconciliation
    # needs to know which of two matched pregens is the human-verified one
    # (attributes/background take priority) versus the scenario's own
    # LLM-extracted version (secret_goal takes priority from here instead).
    pregen: dict[str, Any] = {"name": name, "occupation": occupation, "source": "manual"}
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

    # Combine every recognized weapon/item section into one blob before
    # classifying — see _ITEM_SECTION_NAMES' own comment for why this can't
    # be "whichever header, whichever parser".
    combined_items_text = "\n\n".join(sections[name] for name in _ITEM_SECTION_NAMES if sections.get(name))
    weapons, carried_items = _classify_item_blocks(combined_items_text)
    pregen["weapons"] = weapons
    pregen["carried_items"] = carried_items

    # Flavor fields our schema has no dedicated slot for, plus any whole
    # scenario-specific extra section (e.g. "其他住戶") — folded into notes
    # rather than dropped, matching this session's "never silently discard
    # authored content" approach for the map importer. Weapon/item sections
    # are deliberately EXCLUDED here now that they're parsed structurally
    # above (into weapons/carried_items) — duplicating them into notes too
    # would just be the same content twice.
    notes_parts = []
    flavor = {k: v for k, v in info.items() if k not in ("姓名", "玩家", "職業") and v}
    if flavor:
        notes_parts.append("、".join(f"{k}：{v}" for k, v in flavor.items()))
    if sections.get("角色背景"):
        notes_parts.append(f"【角色背景】\n{sections['角色背景']}")
    excluded_sections = ("角色資料", "屬性", "技能", "角色背景", "角色扮演動機", *_ITEM_SECTION_NAMES)
    for section_name, body in sections.items():
        if section_name not in excluded_sections and body:
            notes_parts.append(f"【{section_name}】\n{body}")
    pregen["notes"] = "\n\n".join(notes_parts)
    pregen["extra_fields"] = {
        section_name: body
        for section_name, body in sections.items()
        if section_name not in excluded_sections and body
    }

    pregen["secret_goal"] = sections.get("角色扮演動機", "")
    pregen["key_connection"] = ""
    return pregen


def _coerce_int(value: Any) -> int | None:
    """Best-effort int coercion tolerating a numeric string (an occasionally
    observed LLM tool-call quirk — a real extraction returned skill values
    as "65" instead of 65; see _clean_pregen_keys' docstring for the sibling
    issue on property *names* rather than values) as well as the expected
    int/float. Returns None for anything genuinely non-numeric (including
    bool — an int subclass in Python, but never a real attribute/skill
    value) rather than raising, so callers can tell "not a number" apart
    from "coerced to 0"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip().rstrip("%")
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    return None


def _int_or(value: Any, default: int) -> int:
    coerced = _coerce_int(value)
    return coerced if coerced is not None else default


def _resolve_weapon_ammo(weapons: dict[str, dict[str, Any]], era: str) -> dict[str, dict[str, int]]:
    """Turns each weapon's raw parse-time result (see _classify_item_blocks)
    into the plain {"ammo": int, "ammo_max": int} (or {}) shape Character
    actually stores — resolving any "ammo_category" marker against `era`'s
    table happens here, at conversion time, because era (GroupState.era) is
    a group-level setting that parse_role_sheet_text has no access to (it
    only ever sees the sheet's raw text)."""
    table = _AMMO_TABLE.get(era, _AMMO_TABLE["1920s"])
    resolved: dict[str, dict[str, int]] = {}
    for name, info in weapons.items():
        if "ammo" in info:
            resolved[name] = {"ammo": info["ammo"], "ammo_max": info["ammo_max"]}
        elif "ammo_category" in info:
            capacity = table.get(info["ammo_category"])
            resolved[name] = {"ammo": capacity, "ammo_max": capacity} if capacity else {}
        else:
            resolved[name] = {}
    return resolved


def pregen_to_character(pregen: dict[str, Any], owner_id: str, era: str = "1920s") -> Character:
    str_ = _int_or(pregen.get("str_"), 50)
    con = _int_or(pregen.get("con"), 50)
    siz = _int_or(pregen.get("siz"), 50)
    dex = _int_or(pregen.get("dex"), 50)
    app = _int_or(pregen.get("app"), 50)
    int_ = _int_or(pregen.get("int_"), 50)
    pow_ = _int_or(pregen.get("pow_"), 50)
    edu = _int_or(pregen.get("edu"), 50)
    # Roll at claim time so the shared scenario library is never mutated and
    # different players claiming the same pregen receive independent values.
    luck = _roll(3, 6, 5)

    # _int_or already falls back to `default` on anything non-numeric — no
    # need for a trailing `or default` here, which would (confusingly) also
    # re-trigger the fallback on a legitimately-extracted 0.
    hp_max = _int_or(pregen.get("hp_max"), (con + siz) // 10)
    mp_max = _int_or(pregen.get("mp_max"), pow_ // 5)
    san_max = _int_or(pregen.get("san_max"), 99)
    san = min(pow_, san_max)
    db, build = damage_bonus_and_build(str_, siz)
    move = move_rate(str_, dex, siz)

    # Canonicalize each incoming skill name (see app/skill_aliases.py) — the LLM
    # extraction translates whatever the scenario itself calls a skill into
    # Traditional Chinese, which won't necessarily match the exact spelling the
    # Keeper uses when it later calls skill_check for the same skill.
    # Start from the full official skill list (see app/models.py's
    # generate_investigator, which does the same for /coc pc quick-gen) —
    # this used to only setdefault 閃避/母語, leaving all other 46 official
    # skills entirely absent whenever an uploaded sheet didn't happen to list
    # them, which could make a later skill_check for e.g. "聆聽" find no
    # value at all instead of the correct 20% default.
    skills = dict(BASE_SKILLS)
    skills["閃避"] = dex // 2
    skills["母語"] = edu
    skills.update({
        canonical_skill_name(k): coerced
        for k, v in (pregen.get("skills") or {}).items()
        if (coerced := _coerce_int(v)) is not None
    })

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
        weapons=_resolve_weapon_ammo(pregen.get("weapons") or {}, era),
        carried_items=list(pregen.get("carried_items") or []),
        notes=pregen.get("notes", "") or "",
        key_connection=pregen.get("key_connection", "") or "",
        secret_goal=pregen.get("secret_goal", "") or "",
        extra_fields=dict(pregen.get("extra_fields") or {}),
    )


# Attribute/derived-value fields the Module 4 merge treats as a unit —
# manual wins whichever of these it has; whatever it's missing backfills
# from the llm_extracted side.
_MERGE_ATTR_KEYS = ("str_", "con", "siz", "dex", "app", "int_", "pow_", "edu", "luck",
                     "hp_max", "mp_max", "san_max")


_SOURCE_PRIORITY = {"manual": 2, "merged": 1, "llm_extracted": 0}


def _merge_pregens(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Field-level "best of both" merge (see docs/character_and_dictionary_
    system_spec.md's Module 4 table) for two pregens character_matcher.
    is_same_character has confirmed describe the same investigator, coming
    from DIFFERENT sources (manual vs llm_extracted) — see
    reconcile_pregen_into_pool, which only calls this when sources differ;
    a same-source match is a plain replace instead, since there's no
    manual/llm_extracted priority to apply between two records of the same
    kind.

    The higher-priority side (_SOURCE_PRIORITY) is treated as "manual" below.
    This must be a priority rank, not a literal `source == "manual"` check:
    this function's own output is tagged "merged" (below) and gets written
    back into the pool, so a SECOND reconciliation against that pool entry
    can see existing.source == "merged" — a literal check would then treat
    the freshly re-extracted llm_extracted side as "manual" by exclusion,
    silently overwriting previously-preserved manual data with re-extracted
    (and possibly noisier) LLM output. "merged" already carries forward
    whatever manual data it was built from, so it outranks a fresh
    llm_extracted but yields to an actual new "manual" upload."""
    existing_rank = _SOURCE_PRIORITY.get(str(existing.get("source") or ""), 0)
    new_rank = _SOURCE_PRIORITY.get(str(new.get("source") or ""), 0)
    manual = existing if existing_rank >= new_rank else new
    llm = new if manual is existing else existing

    merged: dict[str, Any] = {
        "source": "merged",
        "name": manual.get("name") or llm.get("name", ""),
        "occupation": manual.get("occupation") or llm.get("occupation", ""),
    }
    for key in _MERGE_ATTR_KEYS:
        if key in manual:
            merged[key] = manual[key]
        elif key in llm:
            merged[key] = llm[key]

    # Skills: union, manual's value wins on overlap — a scenario-exclusive
    # skill the manual sheet never mentioned still survives from llm.
    merged_skills = dict(llm.get("skills") or {})
    merged_skills.update(manual.get("skills") or {})
    merged["skills"] = merged_skills

    # Weapons/carried_items: same "manual wins presence" pattern as
    # attributes — these are specific, player-authored details a generic
    # LLM extraction (which doesn't even produce them — see extract_pregens'
    # schema) is unlikely to have anyway.
    merged["weapons"] = manual.get("weapons") or llm.get("weapons") or {}
    merged["carried_items"] = manual.get("carried_items") or llm.get("carried_items") or []

    # Secret goal is explicitly inherited from the scenario's own
    # LLM-extracted version, not the manual side — a hand-typed sheet
    # re-inventing a scenario's hidden hook would risk contradicting the
    # actual plot the GM is running.
    merged["secret_goal"] = llm.get("secret_goal") or manual.get("secret_goal", "")

    # Background/notes: manual wins — a human's own write-up beats whatever
    # generic notes the LLM extraction produced.
    merged["notes"] = manual.get("notes") or llm.get("notes", "")
    merged["key_connection"] = manual.get("key_connection") or llm.get("key_connection", "")
    merged_extra = dict(llm.get("extra_fields") or {})
    merged_extra.update(manual.get("extra_fields") or {})
    if merged_extra:
        merged["extra_fields"] = merged_extra

    # Preserve an existing claim across the merge rather than silently
    # dropping it — a re-upload that happens to also match an already-
    # claimed pregen shouldn't un-claim it.
    claimed_by = existing.get("claimed_by") or new.get("claimed_by")
    if claimed_by:
        merged["claimed_by"] = claimed_by

    return merged


def reconcile_pregen_into_pool(
    pool: list[dict[str, Any]], new_pregen: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Merges `new_pregen` into `pool` (a new list; the input is not
    mutated), replacing the previous "same occupation string -> overwrite"
    dedup key with character_matcher's identity gates — see docs/character_
    and_dictionary_system_spec.md's Module 4. Returns (updated_pool, action):

    - "added": no existing entry matched — appended as a new pregen.
    - "merged": matched an existing entry with a DIFFERENT source
      (manual/llm_extracted) — full field-level merge applied (see
      _merge_pregens).
    - "replaced": matched an existing entry with the SAME source — no
      manual/llm_extracted priority to apply between two records of the same
      kind, so the newer one simply overwrites the old, carrying over its
      claimed_by if the new upload doesn't specify one (a corrected
      re-upload of an already-claimed character shouldn't silently unclaim
      it).

    Matching only considers pool entries that are NOT yet claimed by anyone
    (see docs/character_and_dictionary_system_spec.md's Module 4 note: this
    reconciliation exists to de-duplicate the unclaimed pick list, not to
    hot-patch a character a player has already claimed) — an already-claimed
    entry is left untouched even if it would otherwise match, so a later
    scenario-correction re-upload can't silently rewrite attributes/skills a
    player already picked. If nothing unclaimed matches, the new pregen is
    simply appended (it'll show up as its own unclaimed pool entry rather
    than merging into someone's claimed sheet).
    """
    pool = list(pool)
    for i, existing in enumerate(pool):
        if existing.get("claimed_by"):
            continue
        if not character_matcher.is_same_character(existing, new_pregen):
            continue
        if existing.get("source") == new_pregen.get("source"):
            replacement = dict(new_pregen)
            if not replacement.get("claimed_by") and existing.get("claimed_by"):
                replacement["claimed_by"] = existing["claimed_by"]
            pool[i] = replacement
            return pool, "replaced"
        pool[i] = _merge_pregens(existing, new_pregen)
        return pool, "merged"
    return pool + [new_pregen], "added"
