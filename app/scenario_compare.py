"""Compares this bot's own extracted scenario text against an independently
produced alternate parse of the same PDF (a different OCR/parsing tool's
output), to catch content our pipeline (app/pdf_loader.py) missed, garbled, or
mis-attributed — a GM-triggered QA check, not something run during play.

Single forced tool-call (via whichever LLM_PROVIDER is configured — see
app/providers/*.py's analyze_text), same dispatch pattern as
app/pregen_extractor.py.
"""
from __future__ import annotations

from typing import Any

from app.config import LLM_PROVIDER, MAX_SCENARIO_CHARS
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

_REPORT_TOOL = {
    "name": "report_discrepancies",
    "description": (
        "比較兩份同一份 COC7e 劇本 PDF 的文字擷取結果（「本系統擷取」與「另一套工具擷取」），"
        "找出實質內容上的落差——例如某段線索、NPC 數值、劇情描述只出現在其中一份，或是同一段內容"
        "在兩邊被讀成不同的數字/名稱。不要回報排版、換行、空白這類無關緊要的差異，只回報會影響"
        "跑團判斷的實質內容落差。如果兩份內容大致一致、沒有實質落差，discrepancies 給空陣列。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "discrepancies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "location_hint": {"type": "string", "description": "這段內容大概在講什麼、附近有什麼關鍵字，方便人工去劇本裡定位"},
                        "issue": {"type": "string", "description": "具體的落差描述——哪一份有、哪一份沒有，或兩邊讀到的內容/數字有何不同"},
                    },
                    "required": ["location_hint", "issue"],
                },
            },
        },
        "required": ["discrepancies"],
    },
}


def compare_scenario_text(our_text: str, alt_text: str) -> list[dict[str, Any]]:
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None or not our_text.strip() or not alt_text.strip():
        return []

    # Same cap app/pdf_loader.py applies to a single extraction — bounds cost/
    # context on a very long scenario; this is a one-off GM action, not
    # something worth chunking/paginating for in this first pass.
    our_text = our_text[:MAX_SCENARIO_CHARS]
    alt_text = alt_text[:MAX_SCENARIO_CHARS]

    result = provider.analyze_text(
        f"【本系統擷取】\n{our_text}\n\n【另一套工具擷取】\n{alt_text}",
        _REPORT_TOOL,
        "以下是同一份劇本 PDF 的兩種文字擷取結果，請用 report_discrepancies 工具回報實質內容落差。",
    )
    return (result or {}).get("discrepancies", []) or []
