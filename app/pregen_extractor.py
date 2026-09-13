"""Pull any built-in pregenerated investigator sheets out of a scenario's text.

Many published COC7e scenarios ship with ready-made pregens so a group can skip
character creation entirely. This does a single forced tool-call to Claude asking
it to report only what's explicitly written in the text (never invent numbers),
so `/coc pregens` can offer them instead of everyone rolling a fresh investigator.
"""
from __future__ import annotations

from typing import Any

import anthropic

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from app.models import Character, damage_bonus_and_build, move_rate

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
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["pregens"],
    },
}


def extract_pregens(scenario_text: str) -> list[dict[str, Any]]:
    if not ANTHROPIC_API_KEY or not scenario_text.strip():
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        tools=[_REPORT_TOOL],
        tool_choice={"type": "tool", "name": "report_pregens"},
        messages=[{
            "role": "user",
            "content": (
                "以下是一份 COC7e 劇本的文字內容。請找出裡面是否附有『預製調查員角色卡』"
                "（通常會列出角色姓名、職業、一串屬性數字如 STR/CON/SIZ/DEX/APP/INT/POW/EDU、"
                "以及一份技能列表）。用 report_pregens 工具回報結果。\n\n" + scenario_text
            ),
        }],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "report_pregens":
            return block.input.get("pregens", []) or []
    return []


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

    skills = {k: int(v) for k, v in (pregen.get("skills") or {}).items() if isinstance(v, (int, float))}
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
        notes=pregen.get("notes", "") or "",
        secret_goal=pregen.get("secret_goal", "") or "",
    )
