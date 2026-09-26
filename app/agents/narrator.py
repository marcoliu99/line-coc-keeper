from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app import keeper, observability
from app.agents.tool_gateway import make_tool_executor, tools_for_speaker_role
from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.domain.models import AgentMessage, MechanicResult
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.services import prompt_config

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}
_OPENING_TOOL_NAMES = keeper.READ_ONLY_TOOL_NAMES - {
    "roll_dice", "roll_impaling_damage", "roll_weapon_damage",
} | {"send_private_info", "show_scenario_image"}


async def run_narrator(message: AgentMessage) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """
    Runs the LLM loop for the Narrator Agent.
    Ordinary turns have no tools. A resolved check or opening fallback may
    use a restricted set within this same narrative stage, so those entries
    do not need a second Keeper conversation or a fixed extra LLM call.

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
    turn_kind = message.payload.get("turn_kind", "player_action")
    tool_enabled = turn_kind in {"resolved_check_followup", "opening_fallback"}

    static_system = (
        prompt_config.build_tool_enabled_narrator_static_prompt(
            keeper._build_static_prompt(state), turn_kind
        ) if tool_enabled else prompt_config.build_narrator_static_prompt(keeper._build_static_prompt(state))
    )
    dynamic_system = prompt_config.build_dynamic_prompt_with_context(
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

    if turn_kind == "resolved_check_followup":
        dynamic_system += "\n\n" + prompt_config.build_resolved_check_outcome_block(
            message.payload["resolved_check_context"]
        )
    elif turn_kind == "opening_fallback":
        dynamic_system += "\n\n" + prompt_config.OPENING_FALLBACK_BLOCK
    elif intent == "GAMEPLAY_ACTION" and mechanic_result:
        dynamic_system += "\n\n" + prompt_config.build_mechanic_facts_block(mechanic_result)
    else:
        dynamic_system += "\n\n" + prompt_config.PURE_ROLEPLAY_BLOCK

    new_message = f"{display_name}：{text}"
    history = state.log

    async def _no_tools(_name: str, _tool_input: dict) -> dict:
        # Narrator has no tools per the design spec — this is never actually
        # invoked (tools=[] below means the model has nothing to call), it's
        # only here because run_conversation's signature requires a callback.
        return {"ok": False, "error": "Narrator agent has no tools"}

    private_messages = message.payload.get("private_messages", [])
    image_requests = message.payload.get("image_requests", [])
    tools: list[dict] = []
    execute_tool: Callable[[str, dict], Awaitable[dict]] = _no_tools
    provider_options: dict = {"response_stage": "narrator"} if LLM_PROVIDER == "openai" else {}
    if tool_enabled:
        allowed = (
            keeper.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES
            if turn_kind == "resolved_check_followup" else _OPENING_TOOL_NAMES
        )
        tools = [tool for tool in tools_for_speaker_role("player")
                 if tool.get("name") in allowed]
        offered_names = {tool["name"] for tool in tools}
        facts: list[str] = []
        gateway = make_tool_executor(
            state, private_messages, image_requests, "player", facts
        )
        combat_status_gate = (
            keeper._CombatStatusToolGate(state) if LLM_PROVIDER == "openai" else None
        )

        async def execute_restricted_tool(name: str, tool_input: dict) -> dict:
            # The provider should only call offered tools, but enforce the
            # boundary at execution too. A resolved roll must never reroll.
            if name not in offered_names:
                return {"ok": False, "error": "tool_not_allowed_for_turn"}
            result = await gateway(name, tool_input)
            if combat_status_gate is not None:
                combat_status_gate.observe_tool_result(name, result)
            return result

        execute_tool = execute_restricted_tool
        if combat_status_gate is not None:
            provider_options["tools_for_request"] = (
                lambda: combat_status_gate.tools_for_request(tools)
            )

    try:
        turn_metrics: dict[str, int] = {}
        with observability.metrics_context(turn_metrics), observability.span(
            "llm.turn", provider=LLM_PROVIDER,
            model=getattr(provider, f"{LLM_PROVIDER.upper()}_MODEL", None),
            agent="narrator",
            reasoning_effort=observability.llm_reasoning_effort(LLM_PROVIDER),
            metrics=turn_metrics,
        ):
            reply_text = await provider.run_conversation(
                static_system, dynamic_system, tools, history, new_message,
                execute_tool, MAX_TOOL_ITERATIONS if tool_enabled else 1,
                **provider_options,
            )
            if tool_enabled and not reply_text.strip():
                raise ValueError("tool-enabled narrator returned an empty reply")
    except Exception:
        observability.event("llm.failed", level=logging.ERROR, agent="narrator", status="error")
        _logger.exception("Narrator LLM call failed")
        message.payload["narration_failed"] = True
        if turn_kind == "resolved_check_followup":
            result = message.payload["resolved_check_context"]
            reply_text = (
                f"{result.get('investigator', '調查員')} 的檢定已結算（擲出 {result.get('roll', '未知')}，"
                f"結果：{result.get('outcome', '未知')}）。守密人暫時無法完成後續敘述；"
                "請先查看目前狀態，不要重新擲骰或重做這次行動。"
            )
        elif turn_kind == "opening_fallback":
            reply_text = "（開場生成暫時失敗，遊戲尚未開始；請稍後再輸入 /coc start。）"
        else:
            reply_text = "守密人暫時無法完成敘事。已提交的變更會保留；請查看目前狀態，不要重擲或重做剛才的行動。"

    return reply_text, private_messages, image_requests
