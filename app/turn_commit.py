"""Committing a turn: the timeline a turn captures, and the one transaction that appends its log entries.

Split out of app/keeper.py unchanged. commit_turn_result is the last write of a player turn; it is
idempotent per turn id (a retry of the same turn is answered from the action ledger) and, for a source-bound
opening, re-checks the scenario, context and participants inside the same transaction.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app import (
    observability,
    opening_identity,
)
from app.config import (
    KP_OOC_LOG_MAX_MESSAGES,
)
from app.keeper_tools import resource_bridge
from app.models import GroupState
from app.repositories import state_transaction
from app.services import (
    history_authority,
)

_logger = logging.getLogger(__name__)


def ensure_turn_timeline(state: GroupState) -> str:
    """Ensure a turn captures one authoritative timeline before any await.

    Older persisted states may have no timeline at all. If a tool creates a
    timeline only after the provider call starts, the turn would capture the
    fallback ``legacy-*`` value and its final log commit could be rejected as
    a false timeline mismatch. Initialize it before prompt construction and
    refresh the caller's snapshot from the committed row.
    """
    if state.timeline_id:
        return state.timeline_id

    def initialize(ctx: state_transaction.TxContext) -> None:
        # The first write assigns the timeline; if another path already did,
        # there is nothing left to save.
        if ctx.state.timeline_id:
            ctx.skip_save()

    state_transaction.run_snapshot(state, initialize, reason="timeline_init")
    return state.timeline_id


class OpeningStartRejected(Exception):
    """A source-bound opening lost its final authoritative start race."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def commit_turn_result(
    state: GroupState,
    log_entries: list[dict[str, Any]],
    openai_response_id: str | None = None,
    *,
    timeline_id: str | None = None,
    invalidate_openai_response_chain: bool = False,
    start_game: bool = False,
    turn_id: str | None = None,
    expected_source_hash: str | None = None,
    expected_opening_context: opening_identity.OpeningContext | None = None,
    expected_opening_participants: opening_identity.OpeningParticipants | None = None,
) -> bool:
    """Append a turn's log entries to the latest committed state.

    The action id is the turn id plus a digest of what is being committed, so a
    delivery or narration retry that reaches this call again with the same
    turn is answered from the action ledger instead of logging it twice, while
    a separate turn (new turn id) or different content is a new action. The
    turn id comes from the caller that owns the turn (``run_turn``), falling
    back to the request context, and is only random when neither exists.
    """
    expected_timeline_id = timeline_id or state.timeline_id or f"legacy-{state.group_id}"
    turn_id = str(turn_id or observability.current_context().get("turn_id") or uuid4().hex)
    fingerprint = state_transaction.request_fingerprint({
        "entries": log_entries, "openai_response_id": openai_response_id,
        "invalidate": invalidate_openai_response_chain, "start_game": start_game,
    })

    def opening_guard(latest_state: GroupState) -> str | None:
        if latest_state.game_started:
            return "already_started"
        if latest_state.active_scenario_source_hash != expected_source_hash:
            return "source_changed"
        if (expected_opening_context is not None
                and opening_identity.context_identity(latest_state) != expected_opening_context):
            return "source_changed"
        if (expected_opening_participants is not None
                and opening_identity.participant_identity(latest_state) != expected_opening_participants):
            return "character_set_changed"
        if not latest_state.active or not latest_state.scenario_text:
            return "no_scenario"
        if not latest_state.characters:
            return "no_characters"
        if latest_state.pending_pregen_luck:
            return "pending_pregen_luck"
        if resource_bridge.guard_replacement(latest_state):
            return "combat_unsettled"
        return None

    def append_entries(ctx: state_transaction.TxContext) -> bool:
        latest_state = ctx.state
        if start_game and latest_state.game_started:
            ctx.skip_save()
            return False
        latest_state.log.extend(
            history_authority.annotate_entry(entry, turn_id=turn_id, timeline_id=ctx.timeline_id)
            for entry in log_entries
        )
        if start_game:
            latest_state.game_started = True
        if invalidate_openai_response_chain:
            latest_state.openai_previous_response_id = ""
            latest_state.openai_previous_response_timeline_id = ""
        elif openai_response_id is not None:
            latest_state.openai_previous_response_id = openai_response_id
            latest_state.openai_previous_response_timeline_id = (
                latest_state.timeline_id or f"legacy-{latest_state.group_id}"
            )
        ctx.stage_event("turn_committed", event_id=f"turn:{turn_id}", entries=len(log_entries))
        return True

    result = state_transaction.commit_for_snapshot(
        state, append_entries, reason="turn", expected_timeline=expected_timeline_id,
        action_id=f"turn:{turn_id}:{fingerprint[:16]}", request_fingerprint=fingerprint,
        latest_state_guard=opening_guard if start_game and expected_source_hash is not None else None,
    )
    if result.outcome is state_transaction.Outcome.STALE_TIMELINE:
        if start_game and expected_source_hash is not None:
            raise OpeningStartRejected("timeline_changed")
        observability.event(
            "state.turn_commit_skipped",
            level=logging.WARNING,
            reason="timeline_mismatch",
            expected_timeline_id=expected_timeline_id,
            current_timeline_id=result.timeline_id,
        )
        return False
    if result.outcome is state_transaction.Outcome.REJECTED and start_game and expected_source_hash is not None:
        raise OpeningStartRejected(result.reason)
    if result.outcome is state_transaction.Outcome.CONFLICT:
        raise state_transaction.StateTransactionFailed(result)
    return result.outcome is state_transaction.Outcome.DUPLICATE or bool(result.value)


def commit_kp_ooc_turn_result(
    state: GroupState, message_text: str, final_text: str, *, timeline_id: str | None = None
) -> bool:
    """Persist KP Assistant OOC working memory without touching public history.

    Appends to the latest committed state so this ephemeral OOC write cannot
    overwrite deterministic tool updates that happened earlier in the same
    Keeper turn.
    """
    expected_timeline_id = timeline_id or state.timeline_id or f"legacy-{state.group_id}"

    def append_ooc(ctx: state_transaction.TxContext) -> None:
        latest_state = ctx.state
        latest_state.kp_ooc_log.extend(
            [
                {"role": "kp_assistant", "content": message_text},
                {"role": "assistant", "content": final_text},
            ]
        )
        latest_state.kp_ooc_log = latest_state.kp_ooc_log[-KP_OOC_LOG_MAX_MESSAGES:]

    result = state_transaction.commit_for_snapshot(
        state, append_ooc, reason="kp_ooc", expected_timeline=expected_timeline_id,
    )
    if result.outcome is state_transaction.Outcome.STALE_TIMELINE:
        observability.event(
            "state.kp_ooc_commit_skipped",
            level=logging.WARNING,
            reason="timeline_mismatch",
            expected_timeline_id=expected_timeline_id,
            current_timeline_id=result.timeline_id,
        )
        return False
    return True
