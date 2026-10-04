from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any

from app import config, keeper, locks, observability
from app.agents import (
    assistant,
    context_builder,
    executor,
    guard,
    intent_router,
    narrator,
    state_reducer,
)
from app.domain.models import AgentMessage, MechanicResult, PlayerTurnKind
from app.models import GroupState
from app.providers.codex_provider import with_codex_turn
from app.providers.turn_budget import with_turn_deadline
from app.services import mutation_admission, prompt_config, turn_delivery, turn_handoff

_logger = logging.getLogger(__name__)


async def prefetch_retrieval(
    state: GroupState, user_id: str, text: str, speaker_role: str, conversation_id: str,
) -> context_builder.RetrievalPrefetch | None:
    """Run a turn's retrieval before its caller queues for the conversation lock.

    The query derivation lives here rather than in the router so it cannot
    drift from what run_turn feeds build_context: a mixed IC/OOC message
    retrieves on its IC span only.

    Returns None whenever the turn would not retrieve anyway — an OOC route
    answers without the gameplay context, and a speaker holding a Luck
    decision is usually answered from state. A None simply means the search
    happens inside the lock as before.
    """
    intent = intent_router.classify_intent(AgentMessage({"text": text, "speaker_role": speaker_role}))
    if intent != "GAMEPLAY_ACTION":
        return None
    if user_id in state.pending_luck_decisions:
        return None
    try:
        return await context_builder.prefetch_retrieval(
            state=state, user_id=user_id, display_name="", text=text,
            resolved_location=None, speaker_role=speaker_role,
            conversation_id=conversation_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # a missed prefetch costs a second, never a turn
        observability.event("rag.prefetch.failed", level=logging.WARNING)
        _logger.exception("Retrieval prefetch failed; the turn will search under the lock")
        return None


@with_turn_deadline
@with_codex_turn
async def run_turn(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: str,
    conversation_id: str,
    *,
    turn_kind: PlayerTurnKind = "player_action",
    resolved_check_context: dict[str, Any] | None = None,
    prefetched_retrieval: context_builder.RetrievalPrefetch | None = None,
    handoff: locks.TurnHandoff | None = None,
) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """
    The main entry point for the Agentic Keeper Supervisor.
    Orchestrates the asynchronous pipeline of Agents to produce a response.
    Returns: (reply_text, private_messages, image_requests)
    """
    mutation_admission.assert_admitted(state.group_id)
    _logger.info(f"Supervisor starting turn for {display_name} ({user_id})")
    # Capture one authoritative timeline before any agent await.  Executor
    # tools may initialize or persist timeline-bound state; without this
    # early capture, a legacy state with no timeline would later fall back to
    # ``legacy-*`` and the canonical log commit could reject the whole turn.
    turn_timeline_id = keeper._ensure_turn_timeline(state)
    # One id for this run of the turn: a retry of its final commit is the same
    # action, a later turn is not.
    turn_id = observability.current_context().get("turn_id") or observability.new_id("turn")

    from app.services.narrative_corrections import blocking_reply
    correction_block = blocking_reply(state, [text, resolved_location])
    if correction_block:
        return correction_block, [], []

    # Preserve the later state-only Luck shortcut without S2's route model.
    held_luck = state.pending_luck_decisions.get(user_id)
    others_waiting = any(
        owner != user_id
        for owner in (*state.pending_checks, *state.pending_luck_decisions)
    )
    if (turn_kind == "player_action" and held_luck and not others_waiting
            and intent_router.classify_intent(AgentMessage({"text": text, "speaker_role": speaker_role})) == "GAMEPLAY_ACTION"):
        actor = state.get_active_character(user_id)
        observability.event("turn.short_circuit", reason="pending_luck",
                            model_requests_avoided=True)
        _logger.info("Supervisor answered %s from state: Luck decision outstanding", display_name)
        return prompt_config.pending_luck_reply(
            held_luck, actor.name if actor else ""), [], []
    # 1. Build Context
    message = await context_builder.build_context(
        state=state,
        user_id=user_id,
        display_name=display_name,
        text=text,
        resolved_location=resolved_location,
        speaker_role=speaker_role,
        conversation_id=conversation_id,
        prefetched=prefetched_retrieval,
    )
    message.payload["turn_kind"] = turn_kind
    if turn_kind == "resolved_check_followup":
        if not resolved_check_context:
            raise ValueError("resolved_check_followup requires an authoritative result")
        message.payload["resolved_check_context"] = resolved_check_context

    # 2. Intent Routing (Fast Path vs Slow Path)
    intent = (
        intent_router.classify_intent(message)
        if turn_kind == "player_action" else turn_kind.upper()
    )
    message.payload["intent"] = intent
    observability.event("turn.route", route=intent.lower(), turn_kind=turn_kind)
    
    _logger.info(f"Intent classified as: {intent}")

    # KP Assistant uses its own provider/tool/Guard/commit path. Its OOC
    # replies enter kp_ooc_log; explicit or tool-created canon enters log.
    # Neither case goes through the player Executor/Narrator pipeline.
    if intent == "OOC_ASSISTANT":
        _logger.info("Routing to AssistantAgent (OOC Path)")
        return await assistant.run_assistant(message)

    # 3. Route to Executor (Slow Path) or Skip to Narrator (Fast Path)
    mechanic_result: MechanicResult | None = None
    pending_reply = ''
    autoroll_followups: list[dict[str, Any]] = []
    if intent == "GAMEPLAY_ACTION":
        _logger.info("Routing to ExecutorAgent (Slow Path)")
        pending_checks_before = deepcopy(state.pending_checks)
        pending_luck_before = deepcopy(state.pending_luck_decisions)
        origins_before = set(state.check_consequence_origins)
        mechanic_result = await executor.run_executor(message)
        autoroll_followups = [event for event in state.resolved_check_events
            if event.get("event_id") not in origins_before
            and event.get("event_id") in state.check_consequence_origins
            and event.get("owner_id") == user_id
            and event.get("timeline_id") == state.timeline_id]
        if autoroll_followups:
            turn_kind = "resolved_check_followup"
            message.payload["turn_kind"] = turn_kind
            resolved_check_context = autoroll_followups[0]
            message.payload["resolved_check_context"] = resolved_check_context
        pending_reply = turn_handoff.prepare_narrator_handoff(
            state, user_id, mechanic_result, pending_checks_before, pending_luck_before,
            message.payload,
        )
        message.payload["mechanic_result"] = mechanic_result

        # 4. State Reducer (Pure Python)
        state_reducer.apply_mechanic_result(message, mechanic_result)
    else:
        _logger.info("Routing directly to NarratorAgent (Fast Path)")

    # Every mutation this turn will make is committed by now: the Executor's
    # tools persist through their own locked path and the reducer is pure. An
    # ordinary turn's Narrator runs with tools=[], so from here the turn needs
    # ordering, not exclusion — hand the mutation lock to the next player and
    # queue for narration instead.
    #
    # Not for a tool-enabled Narrator. narrator.py gives resolved_check_followup
    # and opening_fallback a restricted tool set, and #99 commits arrivals
    # inside that loop, so those turns keep the mutation lock to the end.
    if (handoff is not None and config.NARRATION_OUTSIDE_MUTATION_LOCK
            and turn_kind == "player_action"):
        await handoff.to_narration()
        observability.event("turn.handoff", phase="narration")

    # 5. Narrator Agent generates the final text
    if pending_reply and not autoroll_followups:
        reply_text = pending_reply
        private_messages: list[tuple[str, str]] = []
        image_requests: list[tuple[str | None, int]] = []
        observability.event('narrator.pending_reused', status='skipped')
    else:
        if autoroll_followups:
            replies = []
            private_messages = []
            image_requests = []
            for result_context in autoroll_followups:
                message.payload["resolved_check_context"] = result_context
                part, private, images = await narrator.run_narrator(message)
                replies.append(part)
                private_messages.extend(private)
                image_requests.extend(images)
            reply_text = "\n\n".join(replies)
            resolved_check_context = autoroll_followups[-1]
        else:
            reply_text, private_messages, image_requests = await narrator.run_narrator(message)
    if turn_kind == "opening_fallback" and message.payload.get("narration_failed"):
        # A failed opening produced no scene. Leave /coc start retryable.
        return reply_text, [], []

    # Consistency precedes Guard; any Guard rewrite is checked again. The
    # deterministic delivery contract is the final writer and safety boundary.
    public_result = turn_delivery.public_mechanic(mechanic_result, state)

    def consistent(candidate: str) -> str:
        if turn_kind == "resolved_check_followup":
            origin_check_id = (resolved_check_context or {}).get("check_id")
            new_pending_check = any(
                isinstance(entry, dict) and entry.get("source_check_id") == origin_check_id
                for entry in state.pending_checks.values()
            )
            candidate = prompt_config.enforce_resolved_check_consistency(
                candidate, resolved_check_context or {}, new_pending_check=new_pending_check,
            )
        if public_result is not None:
            candidate = prompt_config.enforce_mechanic_check_consistency(candidate, public_result)
        return candidate

    reply_text = consistent(reply_text)
    reply_text = await guard.enforce_narrative_safety(message, reply_text)
    reply_text = consistent(reply_text)
    reply_text, private_controls = turn_delivery.finalize(message, reply_text)
    private_messages.extend(item for item in private_controls if item not in private_messages)

    # Persistence for GAMEPLAY_ACTION's actual game-state changes (HP/SAN/
    # pending_checks/combat/etc.) already happened inside the Executor's
    # tool calls, via keeper._execute_tool's own locked
    # (_mutate_and_save_state) path — see state_reducer.py's docstring.
    # What's left here is just committing this turn's log entries: reload
    # the latest state under the state lock (so this can't clobber
    # whatever the tool calls above already saved), append, save, then sync
    # this function's own `state` object so a caller that keeps using it
    # afterward sees the up-to-date snapshot.
    if state.game_started or turn_kind != "player_action":
        committed = keeper._commit_turn_result(
            state,
            [
                {"role": "user", "content": f"{speaker_role} {display_name}: {text}"},
                {"role": "assistant", "content": reply_text},
            ],
            timeline_id=turn_timeline_id,
            start_game=(turn_kind == "opening_fallback"),
            invalidate_openai_response_chain=True,
            turn_id=turn_id,
        )
        if not committed:
            return "（這次回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）", [], []

    return reply_text, private_messages, image_requests
