from __future__ import annotations

import logging
from typing import Any

from app import keeper, observability, spoiler_policy
from app.agents import guard, tool_gateway
from app.domain.models import AgentMessage

_logger = logging.getLogger(__name__)
_ROLE = "kp_assistant"


async def run_assistant(message: AgentMessage) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """Run the independent KP Assistant conversation and commit its result."""
    state = message.payload["state"]
    user_id = message.payload["user_id"]
    display_name = message.payload["display_name"]
    message_text = message.payload["text"]
    resolved_location = message.payload.get("resolved_location")

    provider = keeper._PROVIDERS.get(keeper.LLM_PROVIDER)
    if provider is None:
        return (
            f'（設定錯誤：LLM_PROVIDER="{keeper.LLM_PROVIDER}" 不是支援的供應商，請在 .env 設成 anthropic、gemini 或 openai）',
            [], [],
        )

    turn_id = observability.current_context().get("turn_id") or observability.new_id("turn")
    turn_metrics: dict[str, int] = {}
    model = getattr(provider, f"{keeper.LLM_PROVIDER.upper()}_MODEL", None)
    with (
        observability.context(turn_id=turn_id),
        observability.metrics_context(turn_metrics),
        observability.span(
            "llm.turn",
            provider=keeper.LLM_PROVIDER,
            model=model,
            agent=_ROLE,
            reasoning_effort=observability.llm_reasoning_effort(keeper.LLM_PROVIDER),
            metrics=turn_metrics,
        ),
    ):
        return await _run_assistant_turn(
            state, user_id, display_name, message_text, resolved_location, provider
        )


async def _run_assistant_turn(
    state: Any,
    user_id: str,
    display_name: str,
    message_text: str,
    resolved_location: dict | None,
    provider: Any,
) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    turn_timeline_id = keeper._ensure_turn_timeline(state)
    static_prompt = keeper._build_static_prompt(state)
    dynamic_prompt = keeper._build_dynamic_prompt(state, user_id, resolved_location, _ROLE)
    manual_canon, effective_text = keeper._parse_kp_manual_canon_trigger(_ROLE, message_text)
    turn_message = keeper._format_turn_message(display_name, effective_text, _ROLE)
    tools = tool_gateway.tools_for_speaker_role(_ROLE)
    allowed_tools = {tool["name"] for tool in tools}
    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []
    canonical_tool_events: list[dict] = []
    mutating_tools_ran: list[str] = []
    facts: list[str] = []
    execute_tool = tool_gateway.make_tool_executor(
        state, private_messages, image_requests, _ROLE, facts
    )
    combat_status_gate = keeper._CombatStatusToolGate(state)

    async def execute_assistant_tool(name: str, tool_input: dict) -> dict:
        if name not in allowed_tools:
            return {"ok": False, "error": f"KP Assistant 不允許使用工具：{name}"}
        if name not in keeper.READ_ONLY_TOOL_NAMES:
            mutating_tools_ran.append(name)
        result = await execute_tool(name, tool_input)
        if keeper._kp_tool_result_creates_canon(name, tool_input, result):
            canonical_tool_events.append({
                "tool_name": name,
                "tool_input": dict(tool_input),
                "result": dict(result),
            })
        combat_status_gate.observe_tool_result(name, result)
        return result

    openai_response_id: str | None = None
    if keeper.LLM_PROVIDER == "openai":
        previous_response_id: str | None = state.openai_previous_response_id
        chain_timeline_id = state.openai_previous_response_timeline_id
        if previous_response_id and chain_timeline_id != turn_timeline_id:
            observability.event(
                "provider.chain.reset",
                level=logging.WARNING,
                provider="openai",
                reason="missing_timeline_metadata" if not chain_timeline_id else "timeline_mismatch",
                old_timeline_id=chain_timeline_id or "",
                requested_timeline_id=turn_timeline_id,
                chain_timeline_id=chain_timeline_id,
            )
            previous_response_id = None

        def remember_openai_response_id(response_id: str) -> None:
            nonlocal openai_response_id
            openai_response_id = response_id

        try:
            final_text = await provider.run_conversation(
                static_prompt, dynamic_prompt, tools, state.log, turn_message,
                execute_assistant_tool, keeper.MAX_TOOL_ITERATIONS,
                previous_response_id=previous_response_id,
                on_response_id=remember_openai_response_id,
                tools_for_request=lambda: combat_status_gate.tools_for_request(tools),
            )
        except Exception:
            _logger.exception("KP Assistant provider call failed")
            final_text = keeper._provider_failure_fallback_text(mutating_tools_ran)
    else:
        try:
            final_text = await provider.run_conversation(
                static_prompt, dynamic_prompt, tools, state.log, turn_message,
                execute_assistant_tool, keeper.MAX_TOOL_ITERATIONS,
            )
        except Exception:
            _logger.exception("KP Assistant provider call failed")
            final_text = keeper._provider_failure_fallback_text(mutating_tools_ran)

    provider_text = final_text
    final_text = await guard.enforce_narrative_safety(AgentMessage(payload={}), final_text)
    creates_canon = manual_canon or bool(canonical_tool_events)
    if creates_canon:
        spoiler_check = spoiler_policy.sanitize_public_text(
            final_text, spoiler_policy.collect_protected_terms(state)
        )
        if not spoiler_check.is_safe:
            final_text = spoiler_check.fallback_text or final_text
        canonical_message = keeper._format_kp_canonical_history_message(
            effective_text, canonical_tool_events
        )
        committed = keeper._commit_turn_result(
            state,
            [
                {"role": "user", "content": canonical_message},
                {"role": "assistant", "content": final_text},
            ],
            openai_response_id=openai_response_id,
            timeline_id=turn_timeline_id,
            invalidate_openai_response_chain=final_text != provider_text,
        )
        if not committed:
            return "（這次回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）", [], []
    else:
        committed = keeper._commit_kp_ooc_turn_result(
            state, effective_text, final_text, timeline_id=turn_timeline_id
        )
        if not committed:
            return "（這次 KP Assistant 回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）", [], []
    return final_text, private_messages, image_requests
