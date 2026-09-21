from __future__ import annotations

import re
from typing import Literal

from app.domain.models import AgentMessage

IntentType = Literal["PURE_ROLEPLAY", "GAMEPLAY_ACTION", "OOC_ASSISTANT"]

_PURE_ROLEPLAY_EXACT = {"好", "ok", "嗯", "知道", "了解", "收到", "沒問題", "是的", "對"}
_OOC_RE = re.compile(r"^\(.*\)$|^（.*）$")


def classify_intent(message: AgentMessage) -> IntentType:
    """
    Classifies the user's input into OOC_ASSISTANT（KP 助手場外討論，Phase 10）、
    PURE_ROLEPLAY (Fast Path) 或 GAMEPLAY_ACTION (Slow Path)。

    Rule-based approach to start, to save LLM calls on trivial inputs.
    """
    # speaker_role 是 app/commands/router.py 已經判斷好的身分（見
    # _handle_ordinary_text_message_locked：state.kp_assistant_user_id ==
    # user_id 才會是 "kp_assistant"），這裡直接信任它、強制走 OOC 快車道——
    # KP 助手的發言本來就不是角色扮演也不是遊戲內行動，不應該經過 Executor／
    # Narrator 那套機制判定與故事生成流程，見 app/agents/assistant.py。
    if message.payload.get("speaker_role") == "kp_assistant":
        return "OOC_ASSISTANT"

    text = message.payload.get("text", "").strip()
    if not text:
        return "PURE_ROLEPLAY"

    # Simple confirmations
    if text.lower() in _PURE_ROLEPLAY_EXACT:
        return "PURE_ROLEPLAY"

    # Pure OOC (Out of Character) messages wrapped in parentheses
    if _OOC_RE.match(text):
        return "PURE_ROLEPLAY"

    # Default to gameplay action to be safe (will trigger Executor)
    return "GAMEPLAY_ACTION"
