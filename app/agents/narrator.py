from __future__ import annotations

import asyncio
import logging

from app.domain.models import AgentMessage, MechanicResult
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


async def run_narrator(message: AgentMessage) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """
    Runs the LLM loop for the Narrator Agent.
    This Agent has NO access to tools. It only generates narrative text
    based on the state and facts provided to it.

    Returns:
        (reply_text, private_messages, image_requests)
    """
    provider = _PROVIDERS[LLM_PROVIDER]

    state = message.payload.get("state")
    text = message.payload.get("text", "")
    display_name = message.payload.get("display_name", "玩家")
    speaker_role = message.payload.get("speaker_role", "player")
    intent = message.payload.get("intent", "PURE_ROLEPLAY")
    mechanic_result: MechanicResult | None = message.payload.get("mechanic_result")
    rag_context = message.payload.get("rag_context", "")
    memory_context = message.payload.get("memory_context", "")

    dynamic_system_parts = [f"目前發話者：{display_name}（{speaker_role}）"]
    if rag_context:
        dynamic_system_parts.append(f"【劇本相關內容】\n{rag_context}")
    if memory_context:
        dynamic_system_parts.append(f"【過去記憶】\n{memory_context}")

    if intent == "GAMEPLAY_ACTION" and mechanic_result:
        facts_block = ["【系統判定結果（事實，禁止重新判定或改變）】"]
        facts_block.append(f"成功與否: {'成功' if mechanic_result.success else '失敗'}")
        facts_block.append("發生的事實：")
        for fact in mechanic_result.narrative_facts:
            facts_block.append(f"- {fact}")
        dynamic_system_parts.append("\n".join(facts_block))
    else:
        dynamic_system_parts.append("【純角色扮演（無機制判定）】請以 KP 的身分自然地回應玩家的行動或對話。")

    dynamic_system = "\n\n".join(dynamic_system_parts)
    new_message = f"{display_name}：{text}"
    history = state.log if state is not None else []

    def _no_tools(_name: str, _tool_input: dict) -> dict:
        # Narrator has no tools per the design spec — this is never actually
        # invoked (tools=[] below means the model has nothing to call), it's
        # only here because run_conversation's signature requires a callback.
        return {"ok": False, "error": "Narrator agent has no tools"}

    try:
        # run_conversation is synchronous (see every app/providers/*.py) —
        # dispatched via asyncio.to_thread like every other call site in
        # this codebase, not awaited directly.
        reply_text = await asyncio.to_thread(
            provider.run_conversation,
            NARRATOR_SYSTEM_PROMPT,
            dynamic_system,
            [],
            history,
            new_message,
            _no_tools,
            1,
        )
    except Exception:
        _logger.exception("Narrator LLM call failed")
        reply_text = "（守密人一時語塞，請再說一次剛才的行動）"

    private_messages = message.payload.get("private_messages", [])
    image_requests = message.payload.get("image_requests", [])
    return reply_text, private_messages, image_requests
