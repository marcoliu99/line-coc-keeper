"""Pull a canonical NPC/monster and location stat index out of a scenario's
text — the "模組框架.md" idea from coc-kp-host (see
docs/references/prep_persistence.md's "Keeper 專屬地點/NPC 索引" section),
adapted into a single forced tool-call the same way app/pregen_extractor.py
pulls out pregens.

Why this exists: with the full scenario text sitting in the Keeper's own
context (or retrieved piecemeal via app/scenario_rag.py's search_scenario),
the Keeper can end up re-deriving the same NPC/monster's stats from scratch
each time it comes up — and drift, especially when a scenario describes
several individuals or life stages of the same creature type (a young Deep
One vs. a mature one, a minion cultist vs. the named ringleader) with
different HP each. Building this index once and feeding it into the
(cached) static prompt gives the Keeper one authoritative table to check
against instead of re-reading and re-guessing every time.
"""
from __future__ import annotations

from typing import Any

from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

_REPORT_TOOL = {
    "name": "report_scenario_index",
    "description": (
        "回報這份 COC7e 劇本裡明確寫出的 NPC／怪物數值索引，以及主要地點索引。"
        "只回報文本裡明確寫出的資訊，不要自己編造或推算劇本沒寫的數值；沒有明確數值的"
        "NPC（例如純粹的路人、沒有數值的背景角色）不用列入。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "npcs": {
                "type": "array",
                "description": "有明確數值（尤其是生命值）的 NPC 或怪物條目",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "這個條目的名稱，翻成繁體中文。如果劇本對同一種生物描述了不同型態"
                                "或不同個體（例如幼體與成年、雜兵與頭目），務必分開列成不同條目，"
                                "並在名稱裡清楚區分型態（例如「深潛者（幼體）」「深潛者（頭目）」），"
                                "不要合併成同一筆、也不要只留一個籠統的名稱。"
                            ),
                        },
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "劇本裡對這個條目的其他稱呼方式（別名、簡稱、英文原名），沒有就給空陣列",
                        },
                        "hp": {"type": "integer", "description": "生命值（若劇本給的是骰子公式，換算成一個代表數字）"},
                        "hp_formula": {
                            "type": "string",
                            "description": "劇本原文給的生命值骰子公式（例如「3d6+3」），沒有明確公式就留空字串",
                        },
                        "key_stats": {
                            "type": "string",
                            "description": "其餘關鍵數值/能力的簡短摘要（例如 DEX、攻擊方式、護甲、特殊能力），劇本沒寫就留空字串",
                        },
                        "page": {"type": "integer", "description": "這個條目數值主要出現在劇本的第幾頁，找不到就填 0"},
                    },
                    "required": ["name"],
                },
            },
            "locations": {
                "type": "array",
                "description": "劇本裡的主要地點條目",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "地點名稱，翻成繁體中文"},
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "這個地點的其他稱呼方式，沒有就給空陣列",
                        },
                        "summary": {"type": "string", "description": "一到兩句話的簡短描述，只摘要劇本原文，不要自己延伸"},
                        "page": {"type": "integer", "description": "主要描述出現在第幾頁，找不到就填 0"},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["npcs", "locations"],
    },
}


def extract_scenario_index(scenario_text: str) -> dict[str, list[dict[str, Any]]]:
    """Dispatches through LLM_PROVIDER (see app/providers/*.py's analyze_text),
    same provider-agnostic pattern as app/pregen_extractor.py's
    extract_pregens — never hardcoded to a specific SDK regardless of which
    one is actually configured. Returns {"npcs": [...], "locations": [...]},
    both empty lists on any failure (no provider configured, empty scenario
    text, or the call itself failing) rather than raising — callers should
    treat that the same as "nothing extracted yet", not an error."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None or not scenario_text.strip():
        return {"npcs": [], "locations": []}

    result = provider.analyze_text(
        scenario_text,
        _REPORT_TOOL,
        "以下是一份 COC7e 劇本的文字內容。請找出裡面所有有明確數值（尤其是生命值 HP）的 "
        "NPC／怪物，以及主要的地點，用 report_scenario_index 工具回報。",
    )
    if not result:
        return {"npcs": [], "locations": []}
    return {"npcs": result.get("npcs") or [], "locations": result.get("locations") or []}


def format_npc_index_block(npcs: list[dict[str, Any]]) -> str:
    """Renders the NPC/monster index into the block app/keeper.py's
    _build_static_prompt injects into the cached system prompt. Empty input
    renders to "" so callers can skip the whole section header when there's
    nothing to show yet."""
    if not npcs:
        return ""
    lines = []
    for n in npcs:
        name = n.get("name") or "（未命名）"
        aliases = n.get("aliases") or []
        alias_note = f"（別名：{'、'.join(aliases)}）" if aliases else ""
        hp = n.get("hp")
        hp_note = f"HP {hp}" if isinstance(hp, (int, float)) else ""
        formula = n.get("hp_formula") or ""
        if formula:
            hp_note = f"{hp_note}（原文公式：{formula}）" if hp_note else f"原文公式：{formula}"
        stats = n.get("key_stats") or ""
        page = n.get("page")
        page_note = f"第{page}頁" if isinstance(page, (int, float)) and page else ""
        detail = "　".join(part for part in (hp_note, stats, page_note) if part)
        lines.append(f"・{name}{alias_note}" + (f"：{detail}" if detail else ""))
    return "\n".join(lines)


def format_location_index_block(locations: list[dict[str, Any]]) -> str:
    if not locations:
        return ""
    lines = []
    for loc in locations:
        name = loc.get("name") or "（未命名）"
        aliases = loc.get("aliases") or []
        alias_note = f"（別名：{'、'.join(aliases)}）" if aliases else ""
        summary = loc.get("summary") or ""
        page = loc.get("page")
        page_note = f"第{page}頁" if isinstance(page, (int, float)) and page else ""
        detail = "　".join(part for part in (summary, page_note) if part)
        lines.append(f"・{name}{alias_note}" + (f"：{detail}" if detail else ""))
    return "\n".join(lines)
