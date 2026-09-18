from __future__ import annotations

import asyncio
import logging

from app import keeper
from app.domain.models import AgentMessage, MechanicResult
from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}


# Prepended to keeper._build_static_prompt's output — see
# app/agents/executor.py's EXECUTOR_SYSTEM_PROMPT docstring for why these
# agents reuse that function rather than re-deriving character sheets/
# combat status/persona rules a second time. Narrator doesn't need the
# tool-calling rules also baked into that same prompt (which tool to call,
# what difficulty, ammo bookkeeping, etc.) since it has no tools at all —
# the instruction below tells it to ignore that part — but it does need
# everything else in there: persona/tone (敘事節奏紀律/文風), anti-spoiler
# rules, NPC-teammate acting notes, and each character's static sheet.
NARRATOR_SYSTEM_PROMPT = """你是一個 TRPG 守密人（Narrator Agent）。
你的任務是「將已經發生的客觀事實，轉化為沉浸、懸疑且冷酷的敘事文學」。
你沒有權力決定判定成功或失敗、也不能扣除玩家的血量或理智，這些機制已經在前一個階段由系統完成，
你完全沒有工具可以呼叫，也不需要呼叫——下面規則裡提到「呼叫 XX 工具」的部分不適用於你，
純粹當作「這件事在機制上已經處理過了」來理解即可。請嚴格根據輸入的「機制結果 (Mechanic
Result)」來描述場景，不要重新判定或改變這些既定事實。

語氣要求：
- 冷酷、嚴肅、帶有壓迫感與克蘇魯神話的未知恐懼。
- 絕對不要使用客服語氣，也不要因為判定成功就過度恭喜玩家。
- 如果沒有提供明確的檢定結果，只是一般對話，請維持 KP 的角色與玩家互動。

事實來源優先順序（來自 app/services/prompt_config.py 原本設計的守則，這裡沿用）：
- 劇本內容與下面列出的角色資料是世界事實來源，不得隨意發明劇本沒寫的關鍵線索、NPC、地點或幕後真相。
- 【過去記憶】區塊只是玩家過去經歷的參考，不能拿它推翻或覆蓋這一回合的機制結果——機制結果永遠以
  最新的【系統判定結果】為準。

以下是完整的守密人規則（人設、敘事風格、防雷、NPC 演出規範，以及每位角色的資料）：
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

    state = message.payload["state"]
    user_id = message.payload.get("user_id", "")
    text = message.payload.get("text", "")
    display_name = message.payload.get("display_name", "玩家")
    speaker_role = message.payload.get("speaker_role", "player")
    resolved_location = message.payload.get("resolved_location")
    intent = message.payload.get("intent", "PURE_ROLEPLAY")
    mechanic_result: MechanicResult | None = message.payload.get("mechanic_result")
    rag_context = message.payload.get("rag_context", "")
    memory_context = message.payload.get("memory_context", "")

    static_system = NARRATOR_SYSTEM_PROMPT + keeper._build_static_prompt(state)
    dynamic_system = keeper._build_dynamic_prompt(state, user_id, resolved_location, speaker_role)
    if rag_context:
        dynamic_system += f"\n\n【劇本相關內容】\n{rag_context}"
    if memory_context:
        dynamic_system += f"\n\n【過去記憶】\n{memory_context}"

    if intent == "GAMEPLAY_ACTION" and mechanic_result:
        facts_block = ["【系統判定結果（事實，禁止重新判定或改變）】"]
        facts_block.append(f"成功與否: {'成功' if mechanic_result.success else '失敗'}")
        facts_block.append("發生的事實：")
        for fact in mechanic_result.narrative_facts:
            facts_block.append(f"- {fact}")
        dynamic_system += "\n\n" + "\n".join(facts_block)
    else:
        dynamic_system += "\n\n【純角色扮演（無機制判定）】請以 KP 的身分自然地回應玩家的行動或對話。"

    new_message = f"{display_name}：{text}"
    history = state.log

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
            static_system,
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
