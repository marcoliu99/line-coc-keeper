from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app import keeper, observability
from app.agents.tool_gateway import make_tool_executor, tools_for_speaker_role
from app.config import LLM_PROVIDER, MAX_TOOL_ITERATIONS
from app.domain.models import AgentMessage, MechanicResult
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.services import mutation_admission, prompt_config, reply_segments

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
    mutation_admission.assert_admitted(state.group_id)
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

    if message.payload.get("movement_resume_result") is not None:
        import json
        dynamic_system += "\n移動接續結果（未抵達時不得給予室內物品或效果）：" + json.dumps(message.payload["movement_resume_result"], ensure_ascii=False)
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

    new_message = f"{display_name}：{text}" + message.payload.get("correction_context", keeper._correction_context_message(state))
    history = state.log

    route = message.payload.get("route_decision")
    if route and route.message_mode in {"OOC", "mixed"}:
        # Visibility filtering happens before generation, not by trusting JSON
        # recipient labels after the model has already read secrets.
        static_system = ("你是 COC 遊戲的敘事與規則說明者。場外陳述不是遊戲事實。"
                         "不得發明劇情、洩漏秘密或聲稱未提交的行動已完成。只用提供的公開依據。")
        dynamic_system = reply_segments.public_context(state)
        history = []
        tool_enabled = False
        if route.message_mode == "mixed":
            dynamic_system += "\n" + reply_segments.prompt(route)
            if mechanic_result:
                dynamic_system += "\n已驗證的公開結果：\n" + "\n".join(
                    o.public_text for o in mechanic_result.observed_outcomes if o.success and o.audience == "public")
                if mechanic_result.turn_resolution:
                    dynamic_system += "\n裁決狀態：" + mechanic_result.turn_resolution.disposition
            new_message = "請依輸入片段與已驗證機制結果回覆。"
        elif route.audience == "player_private":
            dynamic_system += "\n自己的角色資料（只回覆本人）：\n" + reply_segments.self_context(state, user_id)
            new_message = text
        else:
            new_message = text

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
        if message.payload.get("movement_resume_result", {}).get("arrival"):
            # Only after a verified entry result: finish the originally requested
            # location effects in this existing restricted loop, never reroll entry.
            allowed = (allowed - {"roll_dice"}) | {"add_carried_item", "remove_carried_item", "record_clue", "record_established_fact",
                                 "send_private_info", "show_scenario_image", "sanity_check", "skill_check"}
            dynamic_system += "\n原玩家 IC 行動（後續尚待處理部分須依實際工具結果裁定）：" + message.payload.get("movement_request_text", "")
            dynamic_system += ("\n抵達已提交。可以完成原行動中抵達後的物品／線索，或要求新場景的獨立檢定。"
                               "不可重做原進入檢定；新檢定必須另列不同 action_context，不能把原已結算骰當作未骰。")
        tools = [tool for tool in tools_for_speaker_role("player")
                 if tool.get("name") in allowed]
        offered_names = {tool["name"] for tool in tools}
        facts: list[str] = []
        gateway = make_tool_executor(
            state, private_messages, image_requests, "player", facts,
            observed_outcomes=message.payload.setdefault("observed_outcomes", []),
        )
        combat_status_gate = (
            keeper._CombatStatusToolGate(state) if LLM_PROVIDER == "openai" else None
        )

        async def execute_restricted_tool(name: str, tool_input: dict) -> dict:
            # The provider should only call offered tools, but enforce the
            # boundary at execution too. A resolved roll must never reroll.
            if name not in offered_names:
                return {"ok": False, "error": "tool_not_allowed_for_turn"}
            from app.services import movement
            move_token = movement.CURRENT.set(message.payload.get("movement_session"))
            try:
                result = await gateway(name, tool_input)
            finally:
                movement.CURRENT.reset(move_token)
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
