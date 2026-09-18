from __future__ import annotations

import logging
import json
from typing import Any

from app.models import GroupState
from app.domain.models import AgentMessage, MechanicResult, StateDelta, GameEvent
from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.agents.tool_gateway import HIGH_LEVEL_TOOLS, execute_tool

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

# System prompt for the Executor Agent
EXECUTOR_SYSTEM_PROMPT = """你是一個 TRPG 機制執行者 (Executor Agent)。
你的唯一任務是判斷玩家的發言是否需要進行判定、或是需要呼叫系統機制，並使用對應的工具來解決。
不要直接產出給玩家的最終敘事，你只需要：
1. 呼叫工具來取得擲骰結果、查詢資料、或是計算傷害。
2. 將最終結果統整為結構化的機制結果返回（這將交由下一個階段處理）。
"""

async def run_executor(message: AgentMessage) -> MechanicResult:
    """
    Runs the LLM loop for the Executor Agent.
    """
    provider = _PROVIDERS[LLM_PROVIDER]
    
    state = message.payload["state"]
    text = message.payload["text"]
    user_id = message.payload["user_id"]
    display_name = message.payload["display_name"]
    
    # In a real implementation, we would build a full prompt including RAG and Memory.
    prompt = f"玩家 {display_name} 說：\n{text}\n\n請根據此對話判斷並呼叫機制工具。"
    
    messages = [{"role": "user", "content": prompt}]
    
    # We will loop for tool calls
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await provider.run_conversation(
            messages=messages,
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            tools=HIGH_LEVEL_TOOLS
        )
        
        # Determine if there are tool calls in the response
        # The exact structure depends on the provider adapter.
        # But generally, providers modify the `messages` array or return it.
        # For this prototype, we'll assume we look at the last message.
        last_msg = messages[-1]
        
        # To strictly avoid breaking, we will mock the exit of the loop if no tools called
        # The actual tool execution logic needs to extract tool calls from `response` / `messages`.
        # This is highly dependent on how `anthropic_provider.py` formats tool calls.
        break
        
    # Return a mocked MechanicResult for now, as integrating real LLM responses
    # perfectly requires the StateReducer to be built in Phase 4-8.
    return MechanicResult(
        success=True,
        action_type="unknown",
        narrative_facts=["Executor evaluated the action."],
        state_delta=StateDelta()
    )
