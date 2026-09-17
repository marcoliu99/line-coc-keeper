"""Find a scenario's own "read this to the players" opening text, if it has
one — /coc start's first choice before falling back to having the Keeper
improvise an opening narration (see app/commands.py's "start" subcommand).

Why this exists: many published COC7e scenarios ship a literal boxed/italic
passage meant to be read aloud (or closely paraphrased) to kick the session
off — written by the scenario's own author to set tone and hook precisely.
Letting the Keeper freely improvise every time would throw that away even
when it's sitting right there in the uploaded text. This mirrors
app/scenario_index.py's extraction pattern (a single forced tool-call) rather
than trying to regex/heading-match for it — scenario formatting (especially
after OCR) is too inconsistent to rely on a fixed heading like "Introduction"
actually surviving text extraction.
"""
from __future__ import annotations

from typing import Any

from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

_REPORT_TOOL = {
    "name": "report_opening_narration",
    "description": (
        "回報這份 COC7e 劇本裡有沒有明確寫給守密人、要唸給玩家聽（或改寫後唸給玩家聽）的"
        "開場白／開場介紹文字——通常會是劇本開頭附近一段獨立的引導性段落，直接對「你」"
        "（調查員）說話，描述他們此刻身處的場景、氛圍或接到的委託，用來帶開整場遊戲。"
        "只有在劇本明確包含這種段落時才回報 found=true；如果劇本只有給守密人看的背景說明、"
        "調查員背景、劇情大綱，但沒有真的寫一段可以直接唸給玩家聽的開場文字，回報 found=false，"
        "不要自己把背景說明硬套成開場白。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "found": {"type": "boolean", "description": "劇本裡是否有明確的開場白／開場介紹文字"},
            "text": {
                "type": "string",
                "description": (
                    "found 為 true 時，這段開場文字翻成繁體中文——用自己的話改寫成適合直接唸給"
                    "玩家聽的版本（不要逐字照抄劇本原文），但內容、語氣、揭露的資訊量要忠於原文，"
                    "不要自己延伸或加入原文沒有的細節。found 為 false 時留空字串。"
                ),
            },
            "page": {"type": "integer", "description": "這段開場文字主要出現在劇本的第幾頁，找不到就填 0"},
            "opening_check": {
                "type": ["object", "null"],
                "description": (
                    "只有在這段開場白／開場介紹文字本身**明確要求**每位調查員在遊戲一開始就做一次"
                    "檢定時才填（例如開場文字寫「請所有調查員進行一次偵查檢定」「請做一次理智檢定」）"
                    "——這是全隊共同的開場檢定，不是劇本某個later章節、某個特定場景才會觸發的檢定，"
                    "也不是你自己覺得「這邊感覺應該要檢定」而推薦的。沒有這種明確要求就填 null，"
                    "不要自己發明一個檢定。"
                ),
                "properties": {
                    "type": {
                        "type": "string", "enum": ["skill", "sanity"],
                        "description": "skill＝技能檢定（例如偵查／聆聽），sanity＝理智檢定",
                    },
                    "skill": {
                        "type": "string",
                        "description": "type 為 skill 時，官方技能的繁體中文名稱（例如「偵查」）；type 為 sanity 時不用填",
                    },
                    "loss_success": {
                        "type": "string",
                        "description": "type 為 sanity 時，檢定成功的理智損失（如 '0'、'1'、'1d4'）；type 為 skill 時不用填",
                    },
                    "loss_failure": {
                        "type": "string",
                        "description": "type 為 sanity 時，檢定失敗的理智損失（如 '1d6'、'1d10'）；type 為 skill 時不用填",
                    },
                    "reason": {
                        "type": "string",
                        "description": "為什麼要做這個檢定的簡短說明，翻成繁體中文（例如「注意到巷子裡有東西在動」）",
                    },
                },
                "required": ["type"],
            },
        },
        "required": ["found", "text"],
    },
}


def extract_opening_narration(scenario_text: str) -> dict[str, Any]:
    """Dispatches through LLM_PROVIDER (see app/providers/*.py's analyze_text),
    same provider-agnostic pattern as app/scenario_index.py/app/pregen_extractor.py.
    Returns {"found": bool, "text": str, "page": int, "opening_check": dict | None}
    — found=False (with an empty text, opening_check=None) on any failure (no
    provider configured, empty scenario text, the call itself failing, or the
    scenario genuinely not having one) so callers can treat all of those the
    same way: fall back to having the Keeper improvise instead.

    opening_check, when present, is {"type": "skill"|"sanity", "skill"?: str,
    "loss_success"?: str, "loss_failure"?: str, "reason"?: str} — some
    published scenarios open with an explicit "everyone roll a Spot Hidden"
    (or a Sanity check) as part of the prologue itself, not something that
    only comes up once play is already underway; see app/commands.py's
    "start" subcommand, which registers this as a pending_checks entry for
    every bound character (the same mechanism app/keeper.py's skill_check/
    sanity_check tools use during normal play) so it gets real Discord
    buttons via the existing pending_checks diff-and-post machinery, instead
    of needing a separate one-off code path."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None or not scenario_text.strip():
        return {"found": False, "text": "", "page": 0, "opening_check": None}

    result = provider.analyze_text(
        scenario_text,
        _REPORT_TOOL,
        "以下是一份 COC7e 劇本的文字內容。請判斷裡面有沒有明確寫給守密人、可以直接唸給玩家聽的"
        "開場白／開場介紹文字，以及這段開場白本身有沒有明確要求全隊在遊戲一開始就做一次檢定，"
        "用 report_opening_narration 工具回報。",
    )
    if not result or not result.get("found"):
        return {"found": False, "text": "", "page": 0, "opening_check": None}
    text = (result.get("text") or "").strip()
    if not text:
        return {"found": False, "text": "", "page": 0, "opening_check": None}

    opening_check = result.get("opening_check")
    if isinstance(opening_check, dict):
        check_type = opening_check.get("type")
        if check_type == "skill" and not (opening_check.get("skill") or "").strip():
            opening_check = None  # malformed — a skill check with no skill name is unusable
        elif check_type not in ("skill", "sanity"):
            opening_check = None
    else:
        opening_check = None

    return {"found": True, "text": text, "page": result.get("page") or 0, "opening_check": opening_check}
