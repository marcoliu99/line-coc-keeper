from __future__ import annotations

import logging
import json

from app.domain.models import AgentMessage
from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}


NARRATOR_SYSTEM_PROMPT = """你是一個 TRPG 守密人（Narrator Agent）。
你的任務是「將已經發生的客觀事實，轉化為沉浸、懸疑且冷酷的敘事文學」。
你沒有權力決定判定成功或失敗、也不能扣除玩家的血量或理智，這些機制已經在前一個階段由系統完成。
請嚴格根據輸入的「機制結果 (Mechanic Result)」與「狀態變更 (State Delta)」來描述場景。

語氣要求：
- 冷酷、嚴肅、帶有壓迫感與克蘇魯神話的未知恐懼。
- 絕對不要使用客服語氣，也不要因為判定成功就過度恭喜玩家。
- 如果沒有提供明確的檢定結果，只是一般對話，請維持 KP 的角色與玩家互動。
"""

async def run_narrator(message: AgentMessage) -> tuple[str, list[dict], list[dict]]:
    """
    Runs the LLM loop for the Narrator Agent.
    This Agent has NO access to tools. It only generates narrative text
    based on the state and facts provided to it.
    
    Returns:
        (reply_text, private_messages, image_requests)
    """
    provider = _PROVIDERS[LLM_PROVIDER]
    
    text = message.payload.get("text", "")
    display_name = message.payload.get("display_name", "玩家")
    intent = message.payload.get("intent", "PURE_ROLEPLAY")
    mechanic_result = message.payload.get("mechanic_result")
    
    # Construct the facts-only prompt
    prompt = f"玩家 {display_name} 說：\n{text}\n\n"
    
    if intent == "GAMEPLAY_ACTION" and mechanic_result:
        prompt += "【系統判定結果 (Facts)】\n"
        prompt += f"成功與否: {'成功' if mechanic_result.success else '失敗'}\n"
        prompt += "發生的敘事事實:\n"
        for fact in mechanic_result.narrative_facts:
            prompt += f"- {fact}\n"
            
        delta = mechanic_result.state_delta
        prompt += "狀態變更:\n"
        if delta.hp_change != 0:
            prompt += f"- 生命值(HP)變更: {delta.hp_change}\n"
        if delta.sanity_change != 0:
            prompt += f"- 理智(SAN)變更: {delta.sanity_change}\n"
        if delta.inventory_add:
            prompt += f"- 獲得物品: {', '.join(delta.inventory_add)}\n"
            
        prompt += "\n請根據上述事實，撰寫生動的 KP 敘事回覆："
    else:
        prompt += "【純角色扮演 (無機制判定)】\n請以 KP 的身分自然地回應玩家的行動或對話："

    messages = [{"role": "user", "content": prompt}]
    
    # Narrator Agent runs with NO tools
    response = await provider.run_conversation(
        messages=messages,
        system_prompt=NARRATOR_SYSTEM_PROMPT,
        tools=[] 
    )
    
    # In the real provider logic, the reply text is typically in the last message's content
    reply_text = messages[-1]["content"] if messages else "（敘事生成失敗）"
    
    # The new pipeline handles returning the final tuple
    return reply_text, [], []
