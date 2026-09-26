from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import keeper, observability
from app.agents.tool_gateway import make_tool_executor, tools_for_speaker_role
from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.domain.models import AgentMessage, MechanicResult, StateDelta
from app.providers import anthropic_provider, gemini_provider, openai_provider
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
    check_status: dict[str, Any] = {"tool_called": False, "pending": None}
    execute_tool = make_tool_executor(
        state, private_messages, image_requests, speaker_role, facts, check_status
    )
    combat_status_gate = keeper._CombatStatusToolGate(state)
    # Computed fresh per turn, not a module-level constant — see tool_
    # gateway.tools_for_speaker_role's own docstring for why (RAG-aware
    # search_scenario inclusion, kp_assistant-specific filtering/patching).
    tools = tools_for_speaker_role(speaker_role)

    # Prompt text lives in app/services/prompt_config.py — see that module's
    # header for why it reuses keeper._build_static_prompt/_build_dynamic_
    # prompt (character sheets, combat status, NPC/location index, and every
    # tool-usage rule) rather than re-deriving a second copy.
    static_system = prompt_config.build_executor_static_prompt(keeper._build_static_prompt(state))
    dynamic_system = prompt_config.build_executor_dynamic_prompt_with_context(
        keeper._build_dynamic_prompt(state, user_id, resolved_location, speaker_role), rag_context, memory_context
    )
    character = state.get_active_character(user_id)
    if character:
        dynamic_system += "\n\n" + prompt_config.build_resolved_check_history_block(
            message.payload.get("resolved_check_events", []),
            {
                "HP": f"{character.hp}/{character.hp_max}",
                "SAN": f"{character.san}/{character.san_max}",
                "MP": f"{character.mp}/{character.mp_max}",
                "Luck": character.luck,
            },
        )

    new_message = f"{display_name}：{text}" + keeper._correction_context_message(state)
    scenario_search_count = 0
    turn_status = "success"

    try:
        # Providers expose one native async contract.  Tool execution remains
        # sequential inside the provider and offloads only synchronous state
        # mutation at the tool gateway boundary.
        turn_metrics: dict[str, int] = {}
        with observability.metrics_context(turn_metrics), observability.span(
            "llm.turn", provider=LLM_PROVIDER,
            model=getattr(provider, f"{LLM_PROVIDER.upper()}_MODEL", None),
            agent="executor",
            reasoning_effort=observability.llm_reasoning_effort(LLM_PROVIDER),
            metrics=turn_metrics,
        ):
            async def execute_turn_tool(name: str, tool_input: dict) -> dict:
                nonlocal scenario_search_count
                if name == "search_scenario":
                    scenario_search_count += 1
                result = await execute_tool(name, tool_input)
                if LLM_PROVIDER == "openai":
                    combat_status_gate.observe_tool_result(name, result)
                return result

            provider_options = (
                {"tools_for_request": lambda: combat_status_gate.tools_for_request(tools)}
                if LLM_PROVIDER == "openai" else {}
            )
            await provider.run_conversation(
                static_system, dynamic_system, tools, state.log, new_message,
                execute_turn_tool, MAX_TOOL_ITERATIONS,
                # This call's return value is discarded entirely (only the
                # tool calls' side effects matter to run_executor — see
                # docstring above), and supervisor.py always runs a separate
                # Narrator call afterward regardless of how this turn went.
                # A forced wrap-up here would be a real extra API call whose
                # output the player could never see — see each provider's
                # own comment on the enable_wrapup-gated branch.
                enable_wrapup=False,
                **provider_options,
            )
    except asyncio.CancelledError:
        turn_status = "cancelled"
        raise
    except Exception:
        turn_status = "error"
        observability.event("llm.failed", level=logging.ERROR, agent="executor", status="error")
        _logger.exception("Executor LLM call failed")
        return MechanicResult(
            success=False,
            action_type="error",
            narrative_facts=["機制執行時發生錯誤，請視為純敘事處理，不要假設任何判定結果"],
            state_delta=StateDelta(),
            check_status=check_status,
        )
    finally:
        observability.event(
            "executor.scenario_search.summary", count=scenario_search_count, status=turn_status,
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
        check_status=check_status,
    )
