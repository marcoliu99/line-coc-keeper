"""Canonical skill-name resolution.

Skill names reach app/models.py's Character.skills from two independent freeform
sources — app/pregen_extractor.py's LLM extraction (translates whatever the
scenario's own text calls a skill into Traditional Chinese) and the Keeper's own
tool calls at runtime (skill_check/offer_check_choice, also freeform LLM text).
Nothing forced those two into the same vocabulary, so "手槍" (from one call) and
"射擊（手槍）" (BASE_SKILLS' own spelling, from another) would silently miss each
other — see app/keeper.py's resolve_skill_value, which is the only place this
dict is consulted.
"""
from __future__ import annotations

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
    "駕駛": "汽車駕駛",
    "開車": "汽車駕駛",
    "電腦": "電腦使用",
    "電腦操作": "電腦使用",
    "急救術": "急救",
    "急救包紮": "急救",
    "說服力": "說服",
    "話術溝通": "話術",
    "克蘇魯神話學": "克蘇魯神話",
    "神話學": "克蘇魯神話",
    "信用": "信用評級",
    "生存技能": "生存",
    "追蹤術": "追蹤",
    "潛行術": "潛行",
    "偽裝術": "偽裝",
    "開鎖技巧": "開鎖",
    "巧手技藝": "巧手",
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
    "估價鑑定": "估價",
    "會計學": "會計",
    "法律知識": "法律",
    "機械維修技術": "機械維修",
    "電器維修技術": "電器維修",
    "重機械操作技術": "重機械操作",
    "領航術": "領航",
    "外語": "外語（其他）",
    "投擲物": "投擲",
    "魅力": "魅惑",
    "恐嚇威脅": "恐嚇",
}


def canonical_skill_name(name: str) -> str:
    """Best-effort normalization: trim, then alias-lookup against SKILL_ALIASES.
    Falls through unchanged (including for attribute names like STR/POW, and
    for genuinely novel homebrew skill names) — callers needing the fuzzy
    substring fallback still do that themselves afterward."""
    key = (name or "").strip()
    if key in BASE_SKILLS:
        return key
    return SKILL_ALIASES.get(key, key)
