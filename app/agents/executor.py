from __future__ import annotations

import asyncio
import logging

from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.domain.models import AgentMessage, MechanicResult, StateDelta
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.agents.tool_gateway import TOOLS, make_tool_executor

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

# Deliberately instructs the model NOT to write player-facing narration —
# that's the Narrator agent's job, given only the facts this stage produces
# (see run_narrator). Kept separate from app/keeper.py's DEFAULT_PERSONA/
# static prompt, which is written for the old single-call "do everything"
# Keeper, not a tool-calling-only stage.
EXECUTOR_SYSTEM_PROMPT = (
    "你是 TRPG 機制執行者（Executor Agent）。你的唯一任務是判斷這句話是否需要呼叫工具"
    "（擲骰、技能檢定、理智檢定、調整角色數值、戰鬥、查詢劇本或記憶等），並實際呼叫對應"
    "工具取得真實結果——絕對不要自己編造擲骰或檢定的數字，一律呼叫工具。"
    "你的文字輸出只是給下一階段（Narrator Agent）看的內部摘要，玩家看不到，不需要修飾"
    "語氣或寫成故事，條列說明呼叫了什麼、結果是什麼即可。"
    "如果這句話根本不需要呼叫任何工具（純聊天、純角色扮演、沒有機制動作），就不要呼叫"
    "任何工具，直接回覆「無需機制判定」。"
)


async def run_executor(message: AgentMessage) -> MechanicResult:
    """Runs the Executor Agent's tool-calling loop for a GAMEPLAY_ACTION turn.

    Delegates actual tool execution to keeper._execute_tool via
    tool_gateway.make_tool_executor — see that module's docstring for why
    this reuses app/keeper.py's tools directly rather than a separate
    reimplementation. Real state mutation (HP/SAN changes, pending_checks
    registration, combat state, etc.) happens for real here, through that
    already-locked path, by the time this function returns — state_reducer
    no longer needs to (and must not) re-apply anything on top of it.
    """
    provider = _PROVIDERS[LLM_PROVIDER]

    state = message.payload["state"]
    text = message.payload["text"]
    display_name = message.payload["display_name"]
    speaker_role = message.payload["speaker_role"]
    rag_context = message.payload.get("rag_context", "")
    memory_context = message.payload.get("memory_context", "")

    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []
    facts: list[str] = []
    execute_tool = make_tool_executor(state, private_messages, image_requests, speaker_role, facts)

    dynamic_system_parts = [f"目前發話者：{display_name}（{speaker_role}）"]
    if rag_context:
        dynamic_system_parts.append(f"【劇本相關內容】\n{rag_context}")
    if memory_context:
        dynamic_system_parts.append(f"【過去記憶】\n{memory_context}")
    dynamic_system = "\n\n".join(dynamic_system_parts)

    new_message = f"{display_name}：{text}"

    try:
        # run_conversation is a plain synchronous function in every
        # provider (see app/providers/*.py) — never awaited directly
        # elsewhere in this codebase, always dispatched via asyncio.to_thread
        # from async call sites (see app/legacy_commands.py's handle_text_
        # message). Calling it with `await` directly, as the previous
        # version of this file did, raises before the call even completes.
        await asyncio.to_thread(
            provider.run_conversation,
            EXECUTOR_SYSTEM_PROMPT,
            dynamic_system,
            TOOLS,
            state.log,
            new_message,
            execute_tool,
            MAX_TOOL_ITERATIONS,
        )
    except Exception:
        _logger.exception("Executor LLM call failed")
        return MechanicResult(
            success=False,
            action_type="error",
            narrative_facts=["機制執行時發生錯誤，請視為純敘事處理，不要假設任何判定結果"],
            state_delta=StateDelta(),
        )

    message.payload["private_messages"] = private_messages
    message.payload["image_requests"] = image_requests

    return MechanicResult(
        success=True,
        action_type="tool_calls" if facts else "none",
        narrative_facts=facts or ["這句話不需要任何機制判定"],
        # Real state changes already happened above via execute_tool's calls
        # into keeper._execute_tool — this StateDelta is intentionally left
        # empty (see state_reducer.apply_mechanic_result's docstring for why
        # it must not try to re-apply anything on top of that).
        state_delta=StateDelta(),
    )
