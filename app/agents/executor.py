from __future__ import annotations

import asyncio
import logging
from collections import Counter
from copy import deepcopy
from typing import Any

from app import config, keeper, observability, scenario_retrieval
from app.agents.tool_gateway import make_tool_executor, tools_for_speaker_role
from app.config import MAX_TOOL_ITERATIONS
from app.domain.models import (
    AgentMessage,
    GameEvent,
    MechanicResult,
    ObservedOutcome,
    StateDelta,
    TurnResolution,
)
from app.providers.conversation_session import ConversationSession
from app.services import (
    canonical_facts,
    mutation_admission,
    prompt_config,
    turn_context,
    turn_resolution,
)

_logger = logging.getLogger(__name__)


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
    session = ConversationSession.current()
    assert session is not None
    provider = session.provider

    state = message.payload["state"]
    mutation_admission.assert_admitted(state.group_id)
    text = message.payload["text"]
    user_id = message.payload["user_id"]
    display_name = message.payload["display_name"]
    speaker_role = message.payload["speaker_role"]
    resolved_location = message.payload.get("resolved_location")
    rag_context = message.payload.get("rag_context", "")
    memory_context = message.payload.get("memory_context", "")

    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []
    # Attach the same queues before awaiting anything: prior successful outputs
    # survive a failed continuation, without replaying their tools.
    message.payload["private_messages"] = private_messages
    message.payload["image_requests"] = image_requests
    facts: list[str] = []
    observed: list[ObservedOutcome] = []
    message.payload["observed_outcomes"] = observed
    inventory_events: list[GameEvent] = []
    check_status: dict[str, Any] = {
        "tool_called": False,
        "pending": None,
        "pending_luck": None,
        "resolved": None,
    }
    execute_tool = make_tool_executor(
        state, private_messages, image_requests, speaker_role, facts, check_status,
        evidence_incomplete=bool(scenario_retrieval.incomplete_roots(rag_context)),
        required_evidence_ids=scenario_retrieval.incomplete_roots(rag_context),
        observed_outcomes=observed,
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
    authority_block = canonical_facts.prompt_block(
        canonical_facts.requirements(state, recipient_id=user_id, speaker_role=speaker_role)
    )
    if authority_block:
        dynamic_system += "\n\n" + authority_block
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

    new_message = f"{display_name}：{text}" + message.payload.get("correction_context", keeper._correction_context_message(state))
    before_pending = deepcopy(state.pending_checks)
    before_luck = deepcopy(state.pending_luck_decisions)
    before_actor = turn_resolution.actor_snapshot(state, user_id)
    before_gameplay = turn_resolution.gameplay_snapshot(state)
    tool_events: list[dict[str, Any]] = []
    # Only wire-visible receipts belong in the model's retrieval input budget.
    tool_context: list[dict[str, Any]] = []
    delivered_fragments = scenario_retrieval.delivered_fragments(rag_context)
    source_binding = scenario_retrieval.source_binding(state)
    completion = ""
    scenario_search_count = 0
    turn_status = "success"

    try:
        # Providers expose one native async contract.  Tool execution remains
        # sequential inside the provider and offloads only synchronous state
        # mutation at the tool gateway boundary.
        turn_metrics: dict[str, int] = {}
        with observability.metrics_context(turn_metrics), observability.span(
            "llm.turn", provider=config.LLM_PROVIDER,
            model=session.model,
            agent="executor",
            reasoning_effort=observability.llm_reasoning_effort(config.LLM_PROVIDER),
            metrics=turn_metrics,
        ):
            async def execute_turn_tool(name: str, tool_input: dict) -> dict:
                nonlocal scenario_search_count, source_binding
                if name == "search_scenario":
                    scenario_search_count += 1
                    tool_input = {**tool_input, "_retrieval_principal": user_id}
                inventory_before = {c.name: list(c.carried_items) for c in state.active_characters()}
                combat_active_before = state.combat.active
                actor_before_tool = turn_resolution.actor_snapshot(state, user_id)
                gameplay_before_tool = turn_resolution.gameplay_snapshot(state)
                if name in {'skill_check', 'offer_check_choice', 'offer_npc_attack_defense_choice', 'sanity_check'}:
                    tool_input = {**tool_input, '_player_action': text}
                model = session.model or "unknown"
                remaining = (await asyncio.to_thread(scenario_retrieval.request_budget,
                        [static_system, dynamic_system, tools, new_message, tool_context, {"name": name, "arguments": tool_input}], session.history(state.log), model, config.LLM_PROVIDER)
                    if name == "search_scenario" else scenario_retrieval.BUDGET.get())
                current_binding = scenario_retrieval.source_binding(state)
                if current_binding != source_binding:
                    delivered_fragments.clear()
                    source_binding = current_binding
                fragments_token = scenario_retrieval.DELIVERED_FRAGMENTS.set(frozenset(delivered_fragments))
                budget_token = scenario_retrieval.BUDGET.set(remaining)
                model_token = scenario_retrieval.MODEL.set(model)
                try:
                    result = await execute_tool(name, tool_input)
                finally:
                    scenario_retrieval.DELIVERED_FRAGMENTS.reset(fragments_token)
                    scenario_retrieval.MODEL.reset(model_token)
                    scenario_retrieval.BUDGET.reset(budget_token)
                if result.get("ok") and name in {"add_carried_item", "remove_carried_item"}:
                    owner = result.get("investigator")
                    before_items = inventory_before.get(owner, [])
                    after_items = result.get("carried_items", [])
                    inventory_events.append(GameEvent("inventory_change", {
                        "investigator": owner, "operation": name,
                        "added": list((Counter(after_items) - Counter(before_items)).elements()),
                        "removed": list((Counter(before_items) - Counter(after_items)).elements()),
                        "evidence_ref": f"tool:{len(tool_events) + 1}",
                    }))
                if session.dynamic_tools:
                    combat_status_gate.observe_tool_result(name, result)
                tool_events.append({"name": name, "arguments": deepcopy(tool_input), "result": deepcopy(result),
                                    "inventory_before": inventory_before,
                                    "combat_active_before": combat_active_before,
                                    "gameplay_before": gameplay_before_tool,
                                    "gameplay_after": turn_resolution.gameplay_snapshot(state),
                                    "actor_changed": actor_before_tool != turn_resolution.actor_snapshot(state, user_id)})
                receipt = {**result, "evidence_ref": f"tool:{len(tool_events)}",
                           "current_turn_state": turn_context.current_state(state)}
                tool_context.append({"name": name, "arguments": deepcopy(tool_input), "result": deepcopy(receipt)})
                if name == "search_scenario" and result.get("ok"):
                    delivered_fragments.update(scenario_retrieval.delivered_fragments(result.get("results", "")))
                return receipt

            provider_options = session.stage_options(
                'executor', tools_for_request=lambda: combat_status_gate.tools_for_request(tools))
            if session.decision_context:
                provider_options['tools_for_request'] = lambda: turn_context.check_creation_tools(
                    state, combat_status_gate.tools_for_request(tools))
                provider_options['decision_context'] = lambda: turn_context.executor_decision_context(state, user_id)
            if session.final_feedback:
                def final_feedback(candidate: str) -> dict | None:
                    verified = turn_resolution.validate_resolution(
                        candidate, state=state, user_id=user_id, before_pending=before_pending,
                        before_luck=before_luck, tool_events=tool_events,
                        has_scenario=bool(rag_context or (not keeper.SCENARIO_RAG_ENABLED and state.scenario_text)),
                        before_actor=before_actor, before_gameplay=before_gameplay,
                    )
                    if verified.disposition != 'incomplete' or verified.validation_code == 'model_incomplete':
                        return None
                    return {
                        'validation_code': verified.validation_code, 'reason': verified.reason,
                        'instruction': 'Python 尚未接受這份裁決。核對本次玩家要求、當前 state 與工具收據。'
                            '已成功執行的操作不可重做；若獨立拾取尚未執行，須先呼叫背包工具。'
                            '原檢定仍待擲時，不宣稱整回合 resolved；完成獨立操作後交回正確的 await_check '
                            '及 check_id，並引用本次工具證據。不能用等待原檢定掩蓋本次未執行的操作。'
                            '若真正缺依據或工具失敗，保留 incomplete 並解釋原因，不猜值或繞過驗證。',
                    }
                provider_options['final_feedback'] = final_feedback
            completion = await provider.run_conversation(
                static_system, dynamic_system, tools, session.history(state.log), new_message,
                execute_turn_tool, MAX_TOOL_ITERATIONS,
                # Reuse the existing completion; never force an extra wrap-up.
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
    finally:
        observability.event(
            "executor.scenario_search.summary", count=scenario_search_count, status=turn_status,
        )

    if turn_status == "error":
        resolution = TurnResolution(reason="機制流程中斷；保留已確認結果，不重播工具")
    else:
        resolution = turn_resolution.validate_resolution(
            completion, state=state, user_id=user_id, before_pending=before_pending,
            before_luck=before_luck, tool_events=tool_events,
            has_scenario=bool(rag_context or (not keeper.SCENARIO_RAG_ENABLED and state.scenario_text)),
            before_actor=before_actor, before_gameplay=before_gameplay,
        )
    observability.event("executor.resolution", disposition=resolution.disposition,
                        evidence_count=len(resolution.evidence_refs),
                        validation_code=resolution.validation_code, tool_event_count=len(tool_events))
    state_changed = turn_resolution.gameplay_snapshot(state) != before_gameplay
    execution_health = "completed"
    if turn_status == "error":
        execution_health = "partial" if any(o.success for o in observed) else "recovery_required" if state_changed else "failed"
    return MechanicResult(
        success=turn_status != "error",
        execution_health=execution_health,
        observed_outcomes=observed,
        action_type="tool_calls" if facts else "none",
        narrative_facts=facts or ["本回合沒有工具操作；是否完成行動以裁決狀態為準"],
        # Real state changes already happened above via execute_tool's calls
        # into keeper._execute_tool — this StateDelta is intentionally left
        # empty (see state_reducer.apply_mechanic_result's docstring for why
        # it must not try to re-apply anything on top of that).
        state_delta=StateDelta(),
        check_status={**check_status, "tool_event_count": len(tool_events),
                      "state_changed": state_changed,
                      "dice_rolled": any(e["result"].get("ok") and (e["name"] in {"roll_dice", "roll_weapon_damage", "roll_impaling_damage"} or e["result"].get("resolved")) for e in tool_events)},
        events=inventory_events,
        turn_resolution=resolution,
    )
