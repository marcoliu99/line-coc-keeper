from __future__ import annotations

import asyncio
import logging

from app import keeper
from app import observability
from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.domain.models import AgentMessage, MechanicResult, StateDelta
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.agents.tool_gateway import TOOLS, make_tool_executor
from app.services import prompt_config

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}


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

    # Prompt text lives in app/services/prompt_config.py — see that module's
    # header for why it reuses keeper._build_static_prompt/_build_dynamic_
    # prompt (character sheets, combat status, NPC/location index, and every
    # tool-usage rule) rather than re-deriving a second copy.
    static_system = prompt_config.build_executor_static_prompt(keeper._build_static_prompt(state))
    dynamic_system = prompt_config.build_dynamic_prompt_with_context(
        keeper._build_dynamic_prompt(state, user_id, resolved_location, speaker_role), rag_context, memory_context
    )

    new_message = f"{display_name}：{text}"

    try:
        # run_conversation is a plain synchronous function in every
        # provider (see app/providers/*.py) — never awaited directly
        # elsewhere in this codebase, always dispatched via asyncio.to_thread
        # from async call sites (see app/legacy_commands.py's handle_text_
        # message). Calling it with `await` directly, as the previous
        # version of this file did, raises before the call even completes.
        turn_metrics: dict[str, int] = {}
        with observability.metrics_context(turn_metrics):
            with observability.span(
                "llm.turn", provider=LLM_PROVIDER,
                model=getattr(provider, f"{LLM_PROVIDER.upper()}_MODEL", None),
                agent="executor",
                reasoning_effort=observability.llm_reasoning_effort(LLM_PROVIDER),
                metrics=turn_metrics,
            ):
                await asyncio.to_thread(
                    provider.run_conversation, static_system, dynamic_system, TOOLS,
                    state.log, new_message, execute_tool, MAX_TOOL_ITERATIONS,
                )
    except Exception:
        observability.event("llm.failed", level=logging.ERROR, agent="executor", status="error")
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
