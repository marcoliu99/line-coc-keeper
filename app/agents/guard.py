from __future__ import annotations

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
    """
    provider = _PROVIDERS[LLM_PROVIDER]
    
    prompt = f"【原始錯誤文案】\n{original_text}\n\n【錯誤原因】\n{error_reason}\n\n請修復並重新輸出這段敘述："
    messages = [{"role": "user", "content": prompt}]
    
    response = await provider.run_conversation(
        messages=messages,
        system_prompt=GUARD_SYSTEM_PROMPT,
        tools=[] 
    )
    
    repaired_text = messages[-1]["content"] if messages else "（修復生成失敗）"
    return repaired_text
