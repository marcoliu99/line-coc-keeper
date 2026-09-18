from __future__ import annotations

import asyncio
import logging

from app import keeper
from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.domain.models import AgentMessage, MechanicResult, StateDelta
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.agents.tool_gateway import TOOLS, make_tool_executor

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

# Deliberately instructs the model NOT to write player-facing narration —
# that's the Narrator agent's job, given only the facts this stage produces
# (see run_narrator). Prepended to keeper._build_static_prompt's output
# rather than replacing it: that function is where every COC7e tool-usage
# rule actually lives (skill_check/sanity_check semantics, difficulty
# levels, pushed rolls, ammo/damage rules, item plausibility, combat
# entry conditions, "never invent a result" — hundreds of lines, actively
# maintained for the old single-LLM Keeper) — re-deriving a second copy of
# those rules here would both duplicate a lot of text and silently drift
# out of sync the next time someone tunes the original. The Executor needs
# those exact rules to decide *which* tool to call and with what
# parameters; it just doesn't need the narrative-style paragraphs also
# baked into that same prompt (敘事節奏紀律/文風) — sending those too is
# harmless (the instruction below overrides them for this stage), just a
# few hundred extra tokens, not a correctness issue.
EXECUTOR_SYSTEM_PROMPT = (
    "你是 TRPG 機制執行者（Executor Agent），下面完整的守密人規則你都要讀，但你的輸出跟"
    "守密人不一樣：你的唯一任務是判斷這句話是否需要呼叫工具（擲骰、技能檢定、理智檢定、"
    "調整角色數值、戰鬥、查詢劇本或記憶等），並實際呼叫對應工具取得真實結果——絕對不要"
    "自己編造擲骰或檢定的數字，一律呼叫工具，工具怎麼選、什麼時候該用哪個難度、哪個規則，"
    "都照下面的完整規則判斷。你的文字輸出只是給下一階段（Narrator Agent）看的內部摘要，"
    "玩家看不到，不需要修飾語氣或寫成故事，也不用管下面規則裡關於敘事風格、防雷、NPC 演出"
    "的部分（那些是 Narrator 的工作），條列說明呼叫了什麼、結果是什麼即可。如果這句話根本"
    "不需要呼叫任何工具（純聊天、純角色扮演、沒有機制動作），就不要呼叫任何工具，直接回覆"
    "「無需機制判定」。\n\n以下是完整的守密人規則（僅供你判斷要不要呼叫工具、呼叫哪個、"
    "怎麼填參數，不是要你自己寫敘事）：\n"
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
    user_id = message.payload["user_id"]
    display_name = message.payload["display_name"]
    speaker_role = message.payload["speaker_role"]
    resolved_location = message.payload.get("resolved_location")
    rag_context = message.payload.get("rag_context", "")
    memory_context = message.payload.get("memory_context", "")

    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []
    facts: list[str] = []
    execute_tool = make_tool_executor(state, private_messages, image_requests, speaker_role, facts)

    # Reuse the same static/dynamic prompt builders the old single-LLM
    # Keeper uses — see EXECUTOR_SYSTEM_PROMPT's docstring for why these
    # aren't re-derived here: character sheets, combat status, NPC/location
    # index, and every tool-usage rule (skill_check difficulty, ammo/damage
    # rules, etc.) all come from these two functions.
    static_system = EXECUTOR_SYSTEM_PROMPT + keeper._build_static_prompt(state)
    dynamic_system = keeper._build_dynamic_prompt(state, user_id, resolved_location, speaker_role)
    if rag_context:
        dynamic_system += f"\n\n【劇本相關內容】\n{rag_context}"
    if memory_context:
        dynamic_system += f"\n\n【過去記憶】\n{memory_context}"

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
            static_system,
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
