from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app import async_utils, keeper, observability
from app.config import (
    LOG_SLOW_OPERATION_MS,
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
    TOOL_EXECUTION_TIMEOUT_SECONDS,
)
from app.domain.models import ObservedOutcome
from app.keeper_tools import registry as tool_registry
from app.models import GroupState
from app.services import mutation_admission, turn_delivery

_logger = logging.getLogger(__name__)

# Query timeouts retain worker ownership too: some queries refresh the shared
# snapshot or derived indexes. Dice never inherit query retry semantics.
BOUNDED_QUERY_TOOLS = tool_registry.BOUNDED_QUERY_TOOLS

# The design spec originally called for a condensed set of ~5 high-level
# tools (mechanic_action/character_action/inventory_action/combat_action/
# information_action) with an `action` sub-field, to save tokens versus
# app/keeper.py's 24 granular tools. Implementing that condensed schema for
# real means re-deriving every one of keeper.py's already-verified behaviors
# (dice rolls, pending_checks registration, ammo/weapon safety checks,
# combat state transitions, the _mutate_and_save_state locking discipline —
# see app/keeper.py's _execute_tool and this project's changelog for the
# bugs that discipline was built to prevent) a second time, with every
# chance of silently reintroducing bugs already found and fixed there. The
# previous version of this file took a shortcut instead: each "high-level"
# tool just returned a description string ("Requested skill check for X on
# Y.") without ever touching real dice/state — no check was ever actually
# rolled, no pending_checks entry was ever registered, no HP/SAN/ammo was
# ever really adjusted.
#
# This version drops the condensed schema and exposes keeper.py's real
# TOOLS/_execute_tool directly instead — the Executor Agent gets the exact
# same tool set and behavior the original single-LLM Keeper had, just called
# from a different orchestration layer. Token-count reduction from
# condensing the tool list is a real, separate optimization that can be
# revisited later without re-touching correctness.
def tools_for_speaker_role(speaker_role: str) -> list[dict[str, Any]]:
    """The tool list for one turn, delegated to keeper._tools_for_speaker_
    role — NOT a bare `keeper.TOOLS` constant, and deliberately computed
    fresh per call rather than cached at import time, because the correct
    list genuinely varies per turn in two ways keeper.py's own callers
    (app/keeper.py:3423, the legacy run_turn path) already account for but
    this module previously didn't:
    1. It appends the scenario-search tool when SCENARIO_RAG_ENABLED — the
       static prompt this Agent sends explicitly requires the model to
       call search_scenario for any scenario detail in that mode; without
       this, the tool was never actually offered even though the prompt
       demanded it.
    2. It's filtered/patched differently when speaker_role == "kp_
       assistant" (a KP Assistant speaking an in-character line still
       reaches this Agent for GAMEPLAY_ACTION-classified turns — only
       OOC_ASSISTANT intent bypasses it, via app/agents/assistant.py).
    A bare module-level constant can't reflect either of these, since
    SCENARIO_RAG_ENABLED can differ per deployment and speaker_role
    genuinely differs per turn."""
    return keeper._tools_for_speaker_role(speaker_role)


def make_tool_executor(
    state: GroupState,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
    speaker_role: str,
    facts: list[str],
    check_status: dict[str, Any] | None = None,
    evidence_incomplete: bool = False,
    required_evidence_ids: set[str] | None = None,
    observed_outcomes: list[ObservedOutcome] | None = None,
    actor_id: str = "",
    resolved_check_followup: bool = False,
) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Returns the async (tool_name, tool_input) -> dict callback that
    provider.run_conversation expects for its execute_tool parameter.

    Delegates every call straight to keeper._execute_tool — the same
    function the Keeper turn uses — so dice rolls,
    pending_checks registration, and all state mutation go through the
    identical, already-locked (_mutate_and_save_state) path. `facts`
    collects one human-readable line per call for MechanicResult.
    narrative_facts, so the Narrator agent has something concrete to
    narrate from without re-deriving what happened itself.
    """

    blocked_evidence = set(required_evidence_ids or ())

    def rejection(tool_name: str, error: str, message: str) -> dict[str, Any]:
        result = {"ok": False, "error": error, "message": message}
        facts.append(_describe_tool_call(tool_name, result))
        observability.event("llm.tool.rejected", tool_name=observability.tool_name(tool_name),
                            status="blocked", error_type=error)
        if check_status is not None and error == "required_scenario_evidence_missing":
            check_status["scenario_evidence_blocked"] = True
        return result

    async def execute(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        nonlocal evidence_incomplete
        mutation_admission.assert_admitted(state.group_id)
        spec = tool_registry.REGISTRY.get(tool_name)
        if spec is not None and spec.followup_only and not resolved_check_followup:
            return rejection(tool_name, "tool_not_allowed_for_turn", "此工具只供已結算檢定後續使用")
        if evidence_incomplete and tool_name not in BOUNDED_QUERY_TOOLS:
            return rejection(tool_name, "required_scenario_evidence_missing",
                             "必要劇本依據未齊；請續取完整依據，或暫緩並聚焦行動。不得以截短摘要執行機制。")
        from app.services.narrative_corrections import blocking_reply
        if tool_name not in keeper.READ_ONLY_TOOL_NAMES:
            blocked = blocking_reply(state, tool_input)
            if blocked:
                return rejection(tool_name, "narrative_correction_hold", blocked)
        observability.increment_metric("tool_call_count")
        with observability.span(
            "llm.tool",
            tool_name=observability.tool_name(tool_name),
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        ):
            # _execute_tool contains synchronous SQLite/state-lock mutation.
            # Keep it off the event loop, but do not abandon the worker thread
            # if the awaiting provider request is cancelled: a mutation must
            # finish before the caller releases the conversation lifecycle.
            owner = mutation_admission.start_worker(state.group_id, state.timeline_id, tool_name)

            def run_owned_tool():
                if not mutation_admission.mark_started(owner):
                    raise mutation_admission.MutationHeld("queued worker was cancelled before starting")
                with mutation_admission.bind(owner):
                    result = None
                    try:
                        if tool_name in {"apply_resolved_check_damage", "create_triggered_check", "declare_combat_action", "submit_combat_choice", "request_stabilization_check"}:
                            result = keeper._execute_tool(
                                state, tool_name, tool_input, private_messages, image_requests,
                                speaker_role, actor_id=actor_id,
                            )
                        else:
                            result = keeper._execute_tool(
                                state, tool_name, tool_input, private_messages, image_requests, speaker_role
                            )
                        observability.event("turn.observed", tool_name=tool_name,
                                            dice_rolled=bool(result.get("ok") and (result.get("resolved") or result.get("pending_luck") or tool_name in {"roll_dice", "roll_weapon_damage", "roll_impaling_damage"})))
                        # Record before settlement, including late/cancelled awaiters.
                        facts.append(_describe_tool_call(tool_name, result))
                        if check_status is not None:
                            _record_check_status(check_status, tool_name, result)
                        if observed_outcomes is not None:
                            observed_outcomes.append(turn_delivery.observe_tool(
                                tool_name, result, len(observed_outcomes) + 1, tool_input
                            ))
                        return result
                    finally:
                        # Reconcile only after the synchronous worker really
                        # stopped. Persist evidence on cancellation, never replay.
                        try:
                            if mutation_admission.is_detached(owner):
                                outcome = turn_delivery.observe_tool(tool_name, result or {}, 1, tool_input)
                                def record_settlement(latest):
                                    latest.tool_recovery_markers.append({
                                        "marker_id": owner.generation,
                                        "tool_name": tool_name,
                                        "timeline_id": owner.timeline_id,
                                        "status": "settled" if result is not None else "stopped_result_unknown",
                                        "public_result": outcome.public_text,
                                    })
                                    del latest.tool_recovery_markers[:-100]
                                keeper._mutate_and_save_state(state, record_settlement)
                        except Exception:
                            _logger.exception("Could not persist stopped worker evidence")
                        finally:
                            mutation_admission.settle(owner)

            task = asyncio.create_task(asyncio.to_thread(run_owned_tool))
            task.add_done_callback(lambda done: mutation_admission.reject_unstarted(owner) if done.cancelled() else None)
            try:
                if tool_name in BOUNDED_QUERY_TOOLS:
                    result = await asyncio.wait_for(
                        asyncio.shield(task), TOOL_EXECUTION_TIMEOUT_SECONDS
                    )
                else:
                    result = await asyncio.shield(task)
            except asyncio.TimeoutError:
                mutation_admission.detach(owner)
                observability.event(
                    "llm.tool.timeout", level=logging.WARNING,
                    tool_name=observability.tool_name(tool_name), status="timeout",
                    timeout_ms=TOOL_EXECUTION_TIMEOUT_SECONDS * 1000,
                )
                async_utils.observe_background_task(task, operation=f"llm.tool:{tool_name}")
                result = {"ok": False, "error": "timeout", "partial": True}
            except asyncio.CancelledError:
                mutation_admission.detach(owner)
                async_utils.observe_background_task(task, operation=f"llm.tool:{tool_name}")
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(task), PROVIDER_SHUTDOWN_GRACE_SECONDS
                    )
                except asyncio.TimeoutError:
                    async_utils.observe_background_task(task, operation=f"llm.tool:{tool_name}")
                    observability.event(
                        "llm.tool.recovery_required",
                        level=logging.ERROR,
                        tool_name=observability.tool_name(tool_name),
                        status="partial",
                    )
                except Exception:
                    _logger.exception("Worker failed while its caller was being cancelled")
                raise
        if tool_name == "search_scenario" and result.get("ok"):
            if result.get("complete_for_action") is False:
                evidence_incomplete = True
                if check_status is not None:
                    check_status["scenario_evidence_blocked"] = True
                blocked_evidence.update(result.get("evidence_record_ids", []))
            elif result.get("complete_for_action") is True:
                blocked_evidence.difference_update(result.get("evidence_record_ids", []))
                evidence_incomplete = bool(blocked_evidence)
                if check_status is not None:
                    check_status["scenario_evidence_blocked"] = evidence_incomplete
        return result

    return execute


_CHECK_REGISTRATION_TOOLS = tool_registry.CHECK_REGISTRATION_TOOLS


def _record_check_status(status: dict[str, Any], tool_name: str, result: dict[str, Any]) -> None:
    """Track player-check state from actual tool results for Narrator policy.

    Keep the pending/resolved distinction structured: a failed tool call does
    not resolve or create a check, and a roll awaiting Luck is not a final
    outcome.
    """
    if tool_name in _CHECK_REGISTRATION_TOOLS:
        status["tool_called"] = True
        interaction = result.get('interaction') or {}
        if result.get('ok') and result.get('phase') in {'PLAYER_CHOICE', 'PLAYER_ROLL', 'INJURY_CHECK', 'LUCK_DECISION'}:
            key = 'pending_luck' if result['phase'] == 'LUCK_DECISION' else 'pending'
            status['pending'] = None
            status['pending_luck'] = None
            status['resolved'] = None
            status[key] = {**interaction, 'combat_id': result.get('combat_id'),
                           'action_id': result.get('action_id'), 'phase': result['phase']}
            return
        if result.get('ok') and result.get('completed'):
            status['pending'] = None
            status['pending_luck'] = None
            status['resolved'] = result.get('result') or {
                'combat_id': result.get('combat_id'), 'action_id': result.get('action_id'),
                'check_id': result.get('check_id'), 'completed': True,
            }
            return
        if result.get("ok") and result.get("pending") is True:
            status["pending"] = {
                key: result[key]
                for key in (
                    "investigator", "skill", "skill_value", "difficulty", "options",
                    "check_id", "timeline_id",
                )
                if key in result
            }
            status["pending_luck"] = None
            status["resolved"] = None
        elif result.get("ok") and result.get("pending_luck") is True:
            status["pending"] = None
            status["pending_luck"] = {
                "investigator": result.get("investigator"),
                "skill_name": result.get("skill_name", result.get("skill")),
                "skill_value": result.get("value", result.get("skill_value")),
                "difficulty": result.get("difficulty"),
                "roll": result.get("roll"),
                "original_tier": result.get("original_tier", result.get("tier")),
                "options": result.get("options", result.get("luck_options", [])),
                **{
                    key: result[key]
                    for key in ("decision_id", "check_id", "timeline_id", "action_context")
                    if key in result
                },
            }
            status["resolved"] = None
        elif result.get("ok") and result.get("resolved") is True:
            status["pending"] = None
            status["pending_luck"] = None
            status["resolved"] = {
                key: result[key]
                for key in (
                    "investigator", "skill", "skill_value", "difficulty", "roll", "tier",
                    "required_tier", "success", "check_id", "timeline_id",
                )
                if key in result
            }
            opposed = result.get('opposed_outcome')
            if isinstance(opposed, dict) and opposed.get('winner') in {'player', 'opponent', 'neither'}:
                status['resolved']['opposed_winner'] = opposed['winner']
    elif tool_name == "clear_pending_check" and result.get("ok") and result.get("cleared"):
        status["tool_called"] = True
        status["pending"] = None
        status["pending_luck"] = None
        status["cleared"] = True


def _describe_tool_call(tool_name: str, result: dict[str, Any]) -> str:
    if not result.get("ok", True):
        return f"{tool_name} 失敗：{result.get('error', '未知錯誤')}"
    # Keep this a plain, factual line (not prose) — the Narrator agent turns
    # facts into narrative text; this just needs to state what happened.
    # The tool result is for the Executor. Narrative facts are a separate
    # public-facing handoff and must never copy a private opposed receipt.
    private_check_fields = {"opposed", "opposed_outcome", "action_basis"} if tool_name == "skill_check" else set()
    details = ", ".join(f"{k}={v}" for k, v in result.items()
                        if k not in {"ok", "note"} | private_check_fields)
    if tool_name == "skill_check" and isinstance(result.get('opposed_outcome'), dict):
        winner = result['opposed_outcome'].get('winner')
        if winner in {'player', 'opponent', 'neither'}:
            details += f", opposed_winner={winner}"
    return f"{tool_name} 成功：{details}" if details else f"{tool_name} 成功。"
