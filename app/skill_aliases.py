"""Canonical skill-name resolution.

Skill names reach app/models.py's Character.skills from two independent freeform
sources — app/pregen_extractor.py's LLM extraction (translates whatever the
scenario's own text calls a skill into Traditional Chinese) and the Keeper's own
tool calls at runtime (skill_check/offer_check_choice, also freeform LLM text).
Nothing forced those two into the same vocabulary, so "手槍" (from one call) and
"射擊（手槍）" (BASE_SKILLS' own spelling, from another) would silently miss each
other — see app/keeper.py's resolve_skill_value, which is the only place this
dict is consulted.

Beyond this file's own static SKILL_ALIASES, canonical_skill_name also falls
back to app/dictionary.py's self-learning skills table — the dynamic
counterpart, populated by app/pregen_extractor.py's extract_pregens whenever a
scenario's own text uses a term neither this file nor BASE_SKILLS already
knows (e.g. an English scenario's "Spot Hidden"). Without this fallback, a
term the dictionary had genuinely learned would still only resolve inside
pregen_extractor.py's own extraction pass — an in-game skill check
referencing that same term (a player typing it, or the Keeper's own tool call
echoing it) would have no way to find it, since this function is the only
place resolve_skill_value's canonicalization happens.
"""
from __future__ import annotations

from app import dictionary
from app.models import BASE_SKILLS

# Deliberately only unambiguous variants — a generic term that could plausibly
# mean more than one BASE_SKILLS entry (e.g. bare "科學"、"藝術") is left out on
# purpose; resolve_skill_value's substring fallback still catches those, just
# without the guaranteed-correct short-circuit this table gives the clear cases.
SKILL_ALIASES: dict[str, str] = {
    "手槍": "射擊（手槍）",
    "步槍": "射擊（步槍/霰彈槍）",
    "霰彈槍": "射擊（步槍/霰彈槍）",
    "長槍": "射擊（步槍/霰彈槍）",
    "格鬥": "格鬥（鬥毆）",
    "鬥毆": "格鬥（鬥毆）",
    "近戰": "格鬥（鬥毆）",
    "圖書館": "圖書館使用",
    "圖書館研究": "圖書館使用",
    "偵察": "偵查",
    "聆聲": "聆聽",
    "聽力": "聆聽",
    # NOT "駕駛": "汽車駕駛" — that used to be here, back when bare "駕駛" was
    # an ambiguous term this project chose to assume meant car-driving. Now
    # that BASE_SKILLS' official term for Pilot IS bare "駕駛" (see the
    # 2026-09-17 terminology-table rename), that entry would be unreachable
    # anyway (canonical_skill_name checks BASE_SKILLS membership first) and,
    # worse, its presence would misleadingly suggest "駕駛" still means Drive
    # Auto. Car-driving still has its own unambiguous "汽車駕駛"/"開車".
    "開車": "汽車駕駛",
    "電腦": "電腦使用",
    "電腦操作": "電腦使用",
    "急救術": "急救",
    "急救包紮": "急救",
    "說服力": "說服",
    "話術溝通": "快速交談",
    "克蘇魯神話學": "克蘇魯神話",
    "神話學": "克蘇魯神話",
    "信用": "信用評級",
    "生存技能": "生存",
    "追蹤術": "追蹤",
    "潛行術": "潛行",
    "偽裝術": "偽裝",
    "開鎖技巧": "開鎖",
    "巧手技藝": "妙手",
    "醫療": "醫學",
    "騎馬": "騎術",
    "游泳術": "游泳",
    "跳躍力": "跳躍",
    "攀爬能力": "攀爬",
    "精神分析學": "精神分析",
    "心理學分析": "心理學",
    "自然學識": "自然學",
    "神秘學識": "神秘學",
    "歷史學": "歷史",
    "人類學識": "人類學",
    "考古學識": "考古學",
    "估價鑑定": "鑑定",
    "會計學": "會計",
    "法律知識": "法律",
    "機械維修技術": "機械維修",
    "電器維修技術": "電氣維修",
    "重機械操作技術": "重型機械操作",
    "領航術": "導航",
    "外語": "其他語言",
    "投擲物": "投擲",
    "魅力": "魅惑",
    "恐嚇威脅": "恐嚇",

    # Reverse aliases for this project's OLD terminology (2026-09-17
    # terminology-table rename — see BASE_SKILLS' own comment) — so text
    # still using the old names (a player's habit, an old saved macro, a
    # not-yet-migrated pregen — see scripts/migrate_skill_names.py)
    # continues to normalize to the current official term instead of being
    # treated as an unrecognized skill.
    "估價": "鑑定",
    "話術": "快速交談",
    "領航": "導航",
    "巧手": "妙手",
    "駕駛（其他載具）": "駕駛",
    "電器維修": "電氣維修",
    "外語（其他）": "其他語言",
    "重機械操作": "重型機械操作",

    # Additional unambiguous synonyms found in real scenario pregen
    # extractions. Scenario-defined specialization skills stay out of this
    # static table because they do not have one universal spelling.
    "求生": "生存",
    "鎖匠": "開鎖",
    "自然世界": "自然學",
    "喬裝": "偽裝",
    "威嚇": "恐嚇",
    "駕駛／飛行": "駕駛",
    "駕駛/飛行": "駕駛",
    "步槍／霰彈槍": "射擊（步槍/霰彈槍）",
    "步槍/霰彈槍": "射擊（步槍/霰彈槍）",
}


def canonical_skill_name(name: str) -> str:
    """Best-effort normalization: trim, check BASE_SKILLS, then this file's
    static SKILL_ALIASES, then app/dictionary.py's self-learning table (see
    this module's own docstring for why the third step matters). Falls
    through unchanged (including for attribute names like STR/POW, and for
    genuinely novel homebrew skill names) — callers needing the fuzzy
    substring fallback still do that themselves afterward."""
    key = (name or "").strip()
    if key in BASE_SKILLS:
        return key
    if key in SKILL_ALIASES:
        return SKILL_ALIASES[key]
    return dictionary.lookup_skill(key) or key
