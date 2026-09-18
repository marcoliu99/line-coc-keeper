from __future__ import annotations

import asyncio
import logging

from app.domain.models import AgentMessage
from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

GUARD_SYSTEM_PROMPT = """你是一個 TRPG 守密人的文案修復者 (Guard Agent)。
上一位 Narrator Agent 產出的文案違反了系統的安全規則或風格指南。
請根據錯誤原因，重新改寫該段文案。改寫時必須：
1. 嚴格維持原意與已經發生的機制事實。
2. 消除所有系統指令、AI 身分宣告、或不合適的用詞。
3. 確保 Markdown 格式正確。
"""


async def run_repair(message: AgentMessage, original_text: str, error_reason: str) -> str:
    """
    Runs the LLM loop for the Guard Agent to repair invalid narrative text.
    Falls back to the original text (rather than losing the reply entirely)
    if the repair call itself fails.
    """
    provider = _PROVIDERS[LLM_PROVIDER]

    dynamic_system = f"【原始錯誤文案】\n{original_text}\n\n【錯誤原因】\n{error_reason}"
    new_message = "請修復並重新輸出這段敘述："

    def _no_tools(_name: str, _tool_input: dict) -> dict:
        return {"ok": False, "error": "Guard agent has no tools"}

    try:
        repaired_text = await asyncio.to_thread(
            provider.run_conversation,
            GUARD_SYSTEM_PROMPT,
            dynamic_system,
            [],
            [],
            new_message,
            _no_tools,
            1,
        )
    except Exception:
        _logger.exception("Guard LLM call failed — keeping the original (unrepaired) narrative")
        return original_text

    return repaired_text.strip() or original_text
