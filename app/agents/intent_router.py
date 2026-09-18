from __future__ import annotations

import re
from typing import Literal

from app.domain.models import AgentMessage


IntentType = Literal["PURE_ROLEPLAY", "GAMEPLAY_ACTION"]

_PURE_ROLEPLAY_EXACT = {"好", "ok", "嗯", "知道", "了解", "收到", "沒問題", "是的", "對"}
_OOC_RE = re.compile(r"^\(.*\)$|^（.*）$")


def classify_intent(message: AgentMessage) -> IntentType:
    """
    Classifies the user's input into PURE_ROLEPLAY (Fast Path) or
    GAMEPLAY_ACTION (Slow Path).
    
    Rule-based approach to start, to save LLM calls on trivial inputs.
    """
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
