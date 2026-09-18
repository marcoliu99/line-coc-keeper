from __future__ import annotations

import asyncio
import logging

from app import keeper
from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.domain.models import AgentMessage
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.services import prompt_config

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}


async def run_assistant(message: AgentMessage) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """OOC Assistant Path（Phase 10）：KP 助手的場外討論走這一條，完全繞開
    Executor／State Reducer／Narrator／Rule Validator／Guard 那條「機制判定與
    故事生成」流水線。

    這個階段刻意薄——app/keeper.py 已經有一整套經過驗證的 KP Assistant OOC 機制
    （_KP_ASSISTANT_PROMPT 主持規則、_KP_ASSISTANT_ALLOWED_TOOL_NAMES 工具白名單、
    kp_ooc_log 獨立歷史、_commit_kp_ooc_turn_result 落庫方式），這裡直接複用，不
    重新刻一份會漸漸跟原版不同步的複製品：
    - keeper._build_dynamic_prompt(..., speaker_role="kp_assistant") 本身就會
      在回傳的動態 prompt 裡插入 KP 助手專屬的主持規則區塊跟最近的 kp_ooc_log
      歷史，不需要在這裡另外組一次。
    - keeper._format_turn_message(...) 把這句話包成「[KP ASSISTANT / OOC HOST
      INSTRUCTION]」的樣子，跟舊架構完全一樣。
    - keeper._tools_for_speaker_role("kp_assistant") 把可用工具限制在唯讀查詢
      跟少數「幫指定調查員／NPC 建立正式檢定流程」的工具；keeper._execute_tool
      內部也有第二層同樣的白名單檢查（防禦性、不只靠這裡的工具列表過濾）。
    - keeper._commit_kp_ooc_turn_result(...) 把這輪對話存進 state.kp_ooc_log，
      不會寫進 state.log（正式劇情歷史），也不會動到
      state.openai_previous_response_id（避免污染玩家那條正式對話鏈）。

    資料隔離的另一半——不觸發 post-turn maintenance（記憶壓縮等背景任務，這輪
    OOC 討論不是真的劇情回合，沒有東西需要壓縮）——由呼叫端
    app/commands/router.py 已經有的 `run_maintenance=not is_kp_assistant`
    負責，這裡不用重複處理。
    """
    provider = _PROVIDERS[LLM_PROVIDER]

    state = message.payload["state"]
    user_id = message.payload["user_id"]
    display_name = message.payload["display_name"]
    text = message.payload["text"]
    resolved_location = message.payload.get("resolved_location")
    rag_context = message.payload.get("rag_context", "")
    memory_context = message.payload.get("memory_context", "")

    static_system = keeper._build_static_prompt(state)
    dynamic_system = prompt_config.build_dynamic_prompt_with_context(
        keeper._build_dynamic_prompt(state, user_id, resolved_location, "kp_assistant"), rag_context, memory_context
    )
    turn_message = keeper._format_turn_message(display_name, text, "kp_assistant")
    tools = keeper._tools_for_speaker_role("kp_assistant")

    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []

    def execute_tool(name: str, tool_input: dict) -> dict:
        return keeper._execute_tool(state, name, tool_input, private_messages, image_requests, "kp_assistant")

    try:
        # run_conversation is synchronous (see every app/providers/*.py) —
        # dispatched via asyncio.to_thread like every other call site in
        # this codebase, not awaited directly.
        final_text = await asyncio.to_thread(
            provider.run_conversation,
            static_system,
            dynamic_system,
            tools,
            state.log,  # full public game history for context — same as keeper.run_turn passed for every speaker_role
            turn_message,
            execute_tool,
            MAX_TOOL_ITERATIONS,
        )
    except Exception:
        _logger.exception("Assistant (OOC) LLM call failed")
        final_text = "（KP 助手一時語塞，請再說一次剛才的問題或指令）"

    # Persist to kp_ooc_log, never state.log — see this function's docstring.
    keeper._commit_kp_ooc_turn_result(state, text, final_text)

    return final_text, private_messages, image_requests
