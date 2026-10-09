from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app import (
    config,
    locks,
    observability,
    opening_identity,
    spoiler_policy,
    turn_commit,
)
from app.agents import (
    assistant,
    context_builder,
    executor,
    intent_router,
    laya_shadow,
    narrator,
    obligation_gate,
    reply_pipeline,
    state_reducer,
    tool_gateway,
)
from app.domain.models import (
    AgentMessage,
    FallbackReason,
    MechanicResult,
    PlayerTurnKind,
    SpeakerRole,
)
from app.models import GroupState
from app.providers import turn_budget
from app.providers.codex_provider import with_codex_turn
from app.providers.turn_budget import with_turn_deadline
from app.repositories import state_transaction
from app.services import (
    mutation_admission,
    prompt_config,
    turn_delivery,
    turn_fallback,
    turn_handoff,
    turn_phases,
    unconscious_wake,
)

_logger = logging.getLogger(__name__)


async def prefetch_retrieval(
    state: GroupState, user_id: str, text: str, speaker_role: SpeakerRole, conversation_id: str,
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


# A retrieval that came back without usable evidence. "disabled" is not one of these: the full scenario (or the
# combat state) is already in the prompt, so searching again would only bypass the setting that turned it off.
_DEGRADED_RAG = frozenset({"empty", "fallback", "timeout", "error"})


def _evidence_status(message: AgentMessage) -> str:
    """The retrieval status as the Executor saw it: a successful recovery search counts as evidence."""
    return "success" if message.payload.get("recovery_context") else message.payload.get("rag_status", "")


def _evidence_text(message: AgentMessage) -> str:
    """The scenario text the turn had, including what the one recovery search found."""
    return "\n\n".join(part for part in (message.payload.get("rag_context", ""),
                                          message.payload.get("recovery_context", "")) if part)


def _time_for_retry() -> bool:
    """Whether the turn deadline leaves room for another Executor request (no deadline: always)."""
    try:
        left = turn_budget.remaining()
    except TimeoutError:  # TurnDeadlineExceeded
        return False
    return left is None or left >= config.TURN_RETRY_MIN_REMAINING_SECONDS


async def _recover_blocked_turn(
    message: AgentMessage, result: MechanicResult, *, state: GroupState, user_id: str, text: str,
    speaker_role: SpeakerRole, before_pending: dict, before_luck: dict,
) -> tuple[MechanicResult, FallbackReason | None, str]:
    """Give a fallback turn one more chance, when that cannot apply anything twice.

    Returns the result to use, the reason the first attempt fell back (None when it did not) and the
    outcome of the recovery: ``not_attempted``, ``recovered`` or ``unresolved``. The Executor's first
    attempt must have left the game untouched; the retry gets at most one extra scenario search, and is
    never repeated.
    """
    rag_status = message.payload.get("rag_status", "")
    reason = turn_fallback.classify(result, state, user_id, rag_status=rag_status)
    if reason is None:
        return result, None, "not_attempted"
    if (not config.TURN_FALLBACK_RECOVERY_ENABLED
            or (reason == "internal_error" and not _time_for_retry())
            or not turn_fallback.recoverable(reason, result, before_pending=before_pending,
                                             before_luck=before_luck, state=state)
            or message.payload.get("private_messages") or message.payload.get("image_requests")):
        return result, reason, "not_attempted"
    if rag_status in _DEGRADED_RAG:
        tool_gateway.note_scenario_search()  # the recovery search spends the same per-turn allowance as the tool
        query = turn_fallback.recovery_query(state, text, message.payload.get("resolved_location"))
        try:
            context, _status = await asyncio.to_thread(
                context_builder.search_scenario_context, state, user_id, speaker_role, query,
                label="supervisor.recovery_retrieval", accept_lexical=True, phase_name="recovery_retrieval",
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # the retry still runs; it simply has no new evidence
            _logger.exception("Recovery retrieval failed")
            context = ""
        if context:
            message.payload["recovery_context"] = context
    if reason == "internal_error" and not _time_for_retry():  # the recovery search above may have spent the margin
        return result, reason, "not_attempted"
    retry = await executor.run_executor(message)
    if turn_fallback.classify(retry, state, user_id, rag_status=_evidence_status(message)) is None:
        return retry, reason, "recovered"
    return retry, reason, "unresolved"


TurnReply = tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]


@dataclass
class _Turn:
    """One run of a turn: what it was asked, and what each stage learned for the next.

    The stages below are the turn in the order it happens. A stage that can end the turn early returns the reply;
    otherwise it returns None and the next stage runs.
    """

    state: GroupState
    user_id: str
    display_name: str
    text: str
    resolved_location: dict[str, Any] | None
    speaker_role: SpeakerRole
    conversation_id: str
    turn_kind: PlayerTurnKind
    resolved_check_context: dict[str, Any] | None
    prefetched_retrieval: context_builder.RetrievalPrefetch | None
    handoff: locks.TurnHandoff | None
    expected_opening_source_hash: str | None
    expected_opening_context: opening_identity.OpeningContext | None
    expected_opening_participants: opening_identity.OpeningParticipants | None
    turn_timeline_id: str
    turn_id: str
    message: AgentMessage = field(init=False)
    intent: str = ""
    mechanic_result: MechanicResult | None = None
    pending_reply: str = ""
    autoroll_followups: list[dict[str, Any]] = field(default_factory=list)
    pending_before: tuple[dict, dict] | None = None  # pending checks and Luck decisions as the Executor found them
    obligation_evidence: list[str] = field(default_factory=list)
    obligation_candidates: bool = False
    handoff_before: tuple[dict, dict] = field(default_factory=lambda: ({}, {}))
    narration: tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]] = ("", [], [])


async def _prepare(turn: _Turn) -> TurnReply | None:
    """Answer from state where a model call would add nothing, otherwise build the context and route the intent."""
    from app.services.narrative_corrections import blocking_reply
    correction_block = blocking_reply(turn.state, [turn.text, turn.resolved_location])
    if correction_block:
        turn_phases.note(route="correction_block")
        return correction_block, [], []

    # Preserve the later state-only Luck shortcut without S2's route model.
    held_luck = turn.state.pending_luck_decisions.get(turn.user_id)
    others_waiting = any(
        owner != turn.user_id
        for owner in (*turn.state.pending_checks, *turn.state.pending_luck_decisions)
    )
    if (turn.turn_kind == "player_action" and held_luck and not others_waiting
            and intent_router.classify_intent(
                AgentMessage({"text": turn.text, "speaker_role": turn.speaker_role})) == "GAMEPLAY_ACTION"):
        actor = turn.state.get_active_character(turn.user_id)
        turn_phases.note(route="gameplay_action", short_circuit="pending_luck")
        observability.event("turn.short_circuit", reason="pending_luck",
                            model_requests_avoided=True)
        _logger.info("Supervisor answered %s from state: Luck decision outstanding", turn.display_name)
        return prompt_config.pending_luck_reply(
            held_luck, actor.name if actor else ""), [], []
    # An investigator knocked out outside combat: wait for a standing companion, or wake after a time skip when
    # nobody is left to help, rather than refusing every line forever.
    knocked_out = (unconscious_wake.decide(turn.state, turn.user_id)
                   if turn.turn_kind == "player_action" and turn.speaker_role == "player" else None)
    if knocked_out is not None:
        actor = turn.state.get_active_character(turn.user_id)
        assert actor is not None
        observability.event("turn.short_circuit" if knocked_out == "wait" else "turn.time_skip_wake",
                            reason="knocked_out", model_requests_avoided=knocked_out == "wait")
        if knocked_out == "wait":
            turn_phases.note(route="gameplay_action", short_circuit="knocked_out")
            return unconscious_wake.wait_reply(actor.name), [], []
        await asyncio.to_thread(unconscious_wake.wake, turn.conversation_id, actor.character_id,
                                timeline_id=turn.turn_timeline_id)
        turn.state = await asyncio.to_thread(state_transaction.refresh_snapshot, turn.state)
        turn.text = unconscious_wake.wake_note(actor.name) + turn.text
    # 1. Build Context. The continuation of a roll reuses the evidence its action turn gathered when nothing it
    # depended on has moved, instead of searching the same scene again.
    prefetched_retrieval = turn.prefetched_retrieval
    if (prefetched_retrieval is None and turn.turn_kind == "resolved_check_followup"
            and config.RETRIEVAL_REUSE_FOR_FOLLOWUPS):
        reused_grounding = context_builder.reusable_grounding(turn.state, turn.user_id)
        observability.event("rag.followup_grounding", reused=reused_grounding is not None)
        prefetched_retrieval = reused_grounding
    turn.message = await context_builder.build_context(
        state=turn.state,
        user_id=turn.user_id,
        display_name=turn.display_name,
        text=turn.text,
        resolved_location=turn.resolved_location,
        speaker_role=turn.speaker_role,
        conversation_id=turn.conversation_id,
        prefetched=prefetched_retrieval,
    )
    message = turn.message
    message.payload["turn_kind"] = turn.turn_kind
    if turn.turn_kind == "player_action":
        context_builder.remember_grounding(turn.state, turn.user_id, message)
    if turn.turn_kind == "resolved_check_followup":
        if not turn.resolved_check_context:
            raise ValueError("resolved_check_followup requires an authoritative result")
        message.payload["resolved_check_context"] = turn.resolved_check_context

    # 2. Intent Routing (Fast Path vs Slow Path)
    turn.intent = (
        intent_router.classify_intent(message)
        if turn.turn_kind == "player_action" else turn.turn_kind.upper()
    )
    message.payload["intent"] = turn.intent
    observability.event("turn.route", route=turn.intent.lower(), turn_kind=turn.turn_kind)
    turn_phases.note(route=turn.intent.lower())

    _logger.info(f"Intent classified as: {turn.intent}")
    if turn.turn_kind == "player_action" and turn.intent == "GAMEPLAY_ACTION":  # the lines the Executor would take
        actor = turn.state.get_active_character(turn.user_id)
        laya_shadow.start(turn.text, character=actor.name if actor else turn.display_name,
                          in_combat=turn.state.combat.active)

    # KP Assistant uses its own provider/tool/Guard/commit path. Its OOC
    # replies enter kp_ooc_log; explicit or tool-created canon enters log.
    # Neither case goes through the player Executor/Narrator pipeline.
    if turn.intent == "OOC_ASSISTANT":
        _logger.info("Routing to AssistantAgent (OOC Path)")
        return await assistant.run_assistant(message)
    return None


async def _mechanics(turn: _Turn) -> None:
    """Run the Executor (with its one recovery) and the reducer, then hand the mutation lock on when narration may not mutate."""
    state, user_id, message = turn.state, turn.user_id, turn.message
    # 3. Route to Executor (Slow Path) or Skip to Narrator (Fast Path)
    mechanic_result: MechanicResult | None = None
    if turn.intent == "GAMEPLAY_ACTION":
        _logger.info("Routing to ExecutorAgent (Slow Path)")
        pending_checks_before = deepcopy(state.pending_checks)
        pending_luck_before = deepcopy(state.pending_luck_decisions)
        turn.pending_before = (pending_checks_before, pending_luck_before)
        origins_before = set(state.check_consequence_origins)
        mechanic_result = await executor.run_executor(message)
        mechanic_result, first_reason, recovery = await _recover_blocked_turn(
            message, mechanic_result, state=state, user_id=user_id, text=turn.text,
            speaker_role=turn.speaker_role,
            before_pending=pending_checks_before, before_luck=pending_luck_before,
        )
        fallback_reason = turn_fallback.classify(
            mechanic_result, state, user_id, rag_status=_evidence_status(message))
        mechanic_result.fallback_reason = fallback_reason
        if fallback_reason or recovery == "recovered":
            turn_fallback.record(
                fallback_reason or first_reason or "unknown", state=state, user_id=user_id, turn_id=turn.turn_id,
                rag_context=_evidence_text(message), result=mechanic_result,
                resolved_location=turn.resolved_location, recovery_attempted=recovery != "not_attempted",
                recovery_result=recovery, initial_reason=first_reason if first_reason != fallback_reason else None,
            )
        turn.autoroll_followups = [event for event in state.resolved_check_events
            if event.get("event_id") not in origins_before
            and event.get("event_id") in state.check_consequence_origins
            and event.get("owner_id") == user_id
            and event.get("timeline_id") == state.timeline_id]
        if turn.autoroll_followups:
            turn.turn_kind = "resolved_check_followup"
            message.payload["turn_kind"] = turn.turn_kind
            turn.resolved_check_context = turn.autoroll_followups[0]
            message.payload["resolved_check_context"] = turn.resolved_check_context
        turn.pending_reply = turn_handoff.prepare_narrator_handoff(
            state, user_id, mechanic_result, pending_checks_before, pending_luck_before,
            message.payload,
        )
        message.payload["mechanic_result"] = mechanic_result

        # 4. State Reducer (Pure Python)
        state_reducer.apply_mechanic_result(message, mechanic_result)
    else:
        _logger.info("Routing directly to NarratorAgent (Fast Path)")
    turn.mechanic_result = mechanic_result

    # Every mutation this turn will make is committed by now: the Executor's
    # tools persist through their own locked path and the reducer is pure. An
    # ordinary turn's Narrator runs with tools=[], so from here the turn needs
    # ordering, not exclusion — hand the mutation lock to the next player and
    # queue for narration instead.
    #
    # Not for a tool-enabled Narrator. narrator.py gives resolved_check_followup
    # and opening_fallback a restricted tool set, and #99 commits arrivals
    # inside that loop, so those turns keep the mutation lock to the end.
    #
    # Nor when the evidence states a mechanic the narration may make due: that gate changes state, so it needs the
    # mutation phase.
    turn.obligation_evidence = [
        _evidence_text(message), *(mechanic_result.scenario_evidence if mechanic_result else ())]
    turn.obligation_candidates = (
        turn.turn_kind in {"player_action", "resolved_check_followup"}
        and not (turn.pending_reply and not turn.autoroll_followups)
        and obligation_gate.possible(turn.obligation_evidence, mechanic_result)
    )
    if (turn.handoff is not None and config.NARRATION_OUTSIDE_MUTATION_LOCK
            and turn.turn_kind == "player_action" and not turn.obligation_candidates):
        await turn.handoff.to_narration()
        observability.event("turn.handoff", phase="narration")


async def _narrate(turn: _Turn) -> TurnReply | None:
    """Have the Narrator speak (once per auto-rolled consequence), or reuse the pending-state reply."""
    state, user_id, message = turn.state, turn.user_id, turn.message
    # 5. Narrator Agent generates the final text. What the obligation gate compares against is taken before the
    # Narrator runs: a tool-enabled Narrator can move this state.
    turn.handoff_before = (
        turn.pending_before if turn.pending_before is not None
        else (deepcopy(state.pending_checks), deepcopy(state.pending_luck_decisions))
    )
    if turn.pending_reply and not turn.autoroll_followups:
        reply_text = turn.pending_reply
        turn_fallback.record("unresolved_pending_state", state=state, user_id=user_id, turn_id=turn.turn_id,
                             rag_context=_evidence_text(message), result=turn.mechanic_result,
                             resolved_location=turn.resolved_location)
        private_messages: list[tuple[str, str]] = []
        image_requests: list[tuple[str | None, int]] = []
        observability.event('narrator.pending_reused', status='skipped')
    else:
        if turn.autoroll_followups:
            replies = []
            private_messages = []
            image_requests = []
            for result_context in turn.autoroll_followups:
                message.payload["resolved_check_context"] = result_context
                part, private, images = await narrator.run_narrator(message)
                replies.append(part)
                private_messages.extend(private)
                image_requests.extend(images)
            reply_text = "\n\n".join(replies)
            turn.resolved_check_context = turn.autoroll_followups[-1]
        else:
            reply_text, private_messages, image_requests = await narrator.run_narrator(message)
    if message.payload.get("narration_failed"):
        turn_fallback.record("narration_failure", state=state, user_id=user_id, turn_id=turn.turn_id,
                             rag_context=_evidence_text(message), result=turn.mechanic_result,
                             resolved_location=turn.resolved_location)
    if turn.turn_kind == "opening_fallback" and message.payload.get("narration_failed"):
        # A failed opening produced no scene. Leave /coc start retryable.
        return reply_text, [], []
    turn.narration = (reply_text, private_messages, image_requests)
    return None


def _gate(turn: _Turn) -> tuple[reply_pipeline.ReplyContext, reply_pipeline.ReplyDraft]:
    """The reply's context and first draft, for the ordered steps that decide what a player may read."""
    state, message = turn.state, turn.message
    reply_text, private_messages, image_requests = turn.narration
    ctx = reply_pipeline.ReplyContext(
        state=state, message=message, user_id=turn.user_id, speaker_role=turn.speaker_role,
        turn_kind=turn.turn_kind, resolved_check_context=turn.resolved_check_context, turn_id=turn.turn_id,
        obligation_candidates=turn.obligation_candidates, obligation_evidence=turn.obligation_evidence,
        handoff_before=turn.handoff_before,
    )
    draft = reply_pipeline.ReplyDraft(
        text=reply_text, private_messages=private_messages, image_requests=image_requests,
        mechanic_result=turn.mechanic_result,
        public_result=turn_delivery.public_mechanic(turn.mechanic_result, state),
    )
    return ctx, draft


def _record_safety_block(turn: _Turn, draft: reply_pipeline.ReplyDraft) -> None:
    message = turn.message
    safety_blocked = draft.text == spoiler_policy.NEUTRAL_FALLBACK_TEXT or (
        getattr(message.payload.get("delivery_envelope"), "status", "passed") == "blocked"
        or (getattr(message.payload.get("delivery_envelope"), "status", "passed") == "projected_fallback"
            and not message.payload.get("narration_failed")))
    if safety_blocked:
        turn_fallback.record("safety_block", state=turn.state, user_id=turn.user_id, turn_id=turn.turn_id,
                             rag_context=_evidence_text(message), result=draft.mechanic_result,
                             resolved_location=turn.resolved_location)
    draft.private_messages.extend(item for item in draft.private_controls if item not in draft.private_messages)


def _commit(turn: _Turn, draft: reply_pipeline.ReplyDraft) -> TurnReply:
    """Append the turn to the log in one transaction; a stale timeline delivers nothing."""
    state = turn.state
    # Persistence for GAMEPLAY_ACTION's actual game-state changes (HP/SAN/
    # pending_checks/combat/etc.) already happened inside the Executor's
    # tool calls, via tool_dispatch.execute_tool's own locked
    # (mutate_tool_state) path — see state_reducer.py's docstring.
    # What's left here is just committing this turn's log entries: reload
    # the latest state under the state lock (so this can't clobber
    # whatever the tool calls above already saved), append, save, then sync
    # this function's own `state` object so a caller that keeps using it
    # afterward sees the up-to-date snapshot.
    if state.game_started or turn.turn_kind != "player_action":
        committed = turn_commit.commit_turn_result(
            state,
            [
                {"role": "user", "content": f"{turn.speaker_role} {turn.display_name}: {turn.text}"},
                {"role": "assistant", "content": draft.text},
            ],
            timeline_id=turn.turn_timeline_id,
            start_game=(turn.turn_kind == "opening_fallback"),
            expected_source_hash=turn.expected_opening_source_hash,
            expected_opening_context=turn.expected_opening_context,
            expected_opening_participants=turn.expected_opening_participants,
            invalidate_openai_response_chain=True,
            turn_id=turn.turn_id,
        )
        if not committed:
            turn_fallback.record("state_conflict", state=state, user_id=turn.user_id, turn_id=turn.turn_id,
                                 result=draft.mechanic_result, resolved_location=turn.resolved_location)
            return "（這次回覆所屬的劇情時間線已經更新，舊回覆未送出；請依目前劇情重新操作。）", [], []

    return draft.text, draft.private_messages, draft.image_requests


@with_turn_deadline
@with_codex_turn
@turn_phases.timed_turn
async def run_turn(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: SpeakerRole,
    conversation_id: str,
    *,
    turn_kind: PlayerTurnKind = "player_action",
    resolved_check_context: dict[str, Any] | None = None,
    prefetched_retrieval: context_builder.RetrievalPrefetch | None = None,
    handoff: locks.TurnHandoff | None = None,
    expected_opening_source_hash: str | None = None,
    expected_opening_context: opening_identity.OpeningContext | None = None,
    expected_opening_participants: opening_identity.OpeningParticipants | None = None,
) -> TurnReply:
    """
    The main entry point for the Agentic Keeper Supervisor.
    Orchestrates the asynchronous pipeline of Agents to produce a response.
    Returns: (reply_text, private_messages, image_requests)

    prepare -> mechanics -> narrate -> reply steps -> commit. Delivery to Discord, then the background
    maintenance, is the caller's.
    """
    mutation_admission.assert_admitted(state.group_id)
    _logger.info(f"Supervisor starting turn for {display_name} ({user_id})")
    # Capture one authoritative timeline before any agent await.  Executor
    # tools may initialize or persist timeline-bound state; without this
    # early capture, a legacy state with no timeline would later fall back to
    # ``legacy-*`` and the canonical log commit could reject the whole turn.
    turn_timeline_id = turn_commit.ensure_turn_timeline(state)
    # One id for this run of the turn: a retry of its final commit is the same
    # action, a later turn is not.
    turn_id = observability.current_context().get("turn_id") or observability.new_id("turn")

    turn = _Turn(
        state=state, user_id=user_id, display_name=display_name, text=text, resolved_location=resolved_location,
        speaker_role=speaker_role, conversation_id=conversation_id, turn_kind=turn_kind,
        resolved_check_context=resolved_check_context, prefetched_retrieval=prefetched_retrieval, handoff=handoff,
        expected_opening_source_hash=expected_opening_source_hash,
        expected_opening_context=expected_opening_context,
        expected_opening_participants=expected_opening_participants,
        turn_timeline_id=turn_timeline_id, turn_id=turn_id,
    )
    early = await _prepare(turn)
    if early is not None:
        return early
    await _mechanics(turn)
    early = await _narrate(turn)
    if early is not None:
        return early
    ctx, draft = _gate(turn)
    await reply_pipeline.run(ctx, draft)
    _record_safety_block(turn, draft)
    return _commit(turn, draft)
