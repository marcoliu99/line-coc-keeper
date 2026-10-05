"""Owe the mechanic a scenario attaches to an event in the turn the event is narrated.

The Narrator can show what the scenario evidence describes (an object a rule says costs SAN to see) whether or not the
Executor called the tool the scenario's rule requires. Before the narration is committed, this gate reads the
explicit obligations in the evidence the turn was given (``services/event_obligations``), and for each one
whose trigger the narration shows and that has not been applied to this investigator, applies it through the
ordinary deterministic tools: ``sanity_check``, ``adjust_character`` with a Python-rolled damage, or
``skill_check``. A ledger entry is reserved before the tool runs, so a retry or replay cannot owe it twice.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app import dice, keeper, observability
from app.agents.tool_gateway import make_tool_executor
from app.domain.models import MechanicResult, ObservedOutcome
from app.models import GroupState
from app.services import event_obligations

_logger = logging.getLogger(__name__)
_CAP = 4  # obligations applied in one turn; a longer list is a retrieval accident, not a scene


@dataclass(frozen=True)
class Applied:
    kind: str
    key: str
    summary: str
    pending: bool


def _reserve(state: GroupState, identity: str, obligation: event_obligations.Obligation) -> bool:
    """Record that this application is under way; False when it already was or is done."""
    taken = False

    def mutate(latest: GroupState) -> Any:
        nonlocal taken
        if identity in latest.check_consequence_receipts:
            return keeper.ToolStateMutation({"ok": False}, should_save=False)
        latest.check_consequence_receipts[identity] = {
            "fingerprint": obligation.key,
            "result": {"status": "reserved", "kind": obligation.kind},
        }
        taken = True
        return keeper.ToolStateMutation({"ok": True}, should_save=True)

    keeper.mutate_tool_state(state, mutate)
    return taken


def _settle(state: GroupState, identity: str, result: dict[str, Any] | None) -> None:
    """Keep the receipt of a success; drop the reservation of a failure so a later turn may try again."""

    def mutate(latest: GroupState) -> Any:
        if result is None:
            latest.check_consequence_receipts.pop(identity, None)
        else:
            entry = latest.check_consequence_receipts.get(identity)
            if entry is not None:
                entry["result"] = {"status": "applied", **result}
        return keeper.ToolStateMutation({"ok": True}, should_save=True)

    keeper.mutate_tool_state(state, mutate)


def _summary(obligation: event_obligations.Obligation, name: str, result: dict[str, Any], extra: dict[str, Any]) -> tuple[str, bool]:
    pending = bool(result.get("pending") or result.get("pending_luck"))
    if obligation.kind == "sanity_check":
        if pending:
            return f"劇本規定 {name} 須進行理智檢定（成功損失 {obligation.arg('loss_success')}、失敗損失 {obligation.arg('loss_failure')}），已建立。", True
        return (f"劇本規定 {name} 的理智檢定已結算：損失 {result.get('loss')} 點，"
                f"SAN {result.get('current_san')} → {result.get('san_after')}。"), False
    if obligation.kind == "damage":
        return (f"劇本規定 {name} 受到 {extra.get('damage')} 點傷害（{obligation.arg('expression')}），"
                f"HP 現為 {result.get('value')}。"), False
    return f"劇本規定 {name} 須進行{obligation.arg('skill')}檢定，已建立。", pending


async def enforce(
    state: GroupState, user_id: str, narration: str, evidence: Sequence[str], mechanic_result: MechanicResult | None,
    *, turn_id: str, speaker_role: str, private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]], observed_outcomes: list[ObservedOutcome],
) -> list[Applied]:
    """Apply the obligations this turn's narration has made due; returns what was applied."""
    actor = state.get_active_character(user_id)
    if actor is None or speaker_role != "player" or not narration.strip() or mechanic_result is None:
        return []
    due = [
        item for item in event_obligations.extract("\n\n".join(text for text in evidence if text))
        if event_obligations.triggered(item, narration)
    ]
    if not due:
        return []
    facts: list[str] = []
    execute = make_tool_executor(
        state, private_messages, image_requests, speaker_role, facts, mechanic_result.check_status,
        observed_outcomes=observed_outcomes, actor_id=user_id,
    )
    applied: list[Applied] = []
    for obligation in due[:_CAP]:
        identity = event_obligations.identity(state.timeline_id, actor.character_id, obligation, turn_id)
        if not await asyncio.to_thread(_reserve, state, identity, obligation):
            observability.event("turn.obligation", status="already_applied", kind=obligation.kind, key=obligation.key)
            continue
        extra: dict[str, Any] = {}
        call: tuple[str, dict[str, Any]]
        try:
            if obligation.kind == "sanity_check":
                call = ("sanity_check", {
                    "investigator": actor.name, "loss_success": obligation.arg("loss_success"),
                    "loss_failure": obligation.arg("loss_failure"), "action_context": obligation.trigger[:200],
                })
            elif obligation.kind == "damage":
                rolled = dice.roll_expression(obligation.arg("expression"))
                extra = {"damage": rolled.total, "rolls": list(rolled.rolls)}
                call = ("adjust_character", {"investigator": actor.name, "field": "hp", "delta": -rolled.total})
            else:
                call = ("skill_check", {
                    "investigator": actor.name, "skill": obligation.arg("skill"),
                    "difficulty": obligation.arg("difficulty", "regular"), "action_context": obligation.trigger[:200],
                })
            result = await execute(*call)
        except Exception:
            _logger.exception("Event obligation %s failed", obligation.key)
            await asyncio.to_thread(_settle, state, identity, None)
            observability.event("turn.obligation", level=logging.WARNING, status="error", kind=obligation.kind, key=obligation.key)
            continue
        if not result.get("ok"):
            await asyncio.to_thread(_settle, state, identity, None)
            observability.event("turn.obligation", level=logging.WARNING, status="blocked",
                                kind=obligation.kind, key=obligation.key, error=str(result.get("error", ""))[:120])
            continue
        await asyncio.to_thread(_settle, state, identity, {"kind": obligation.kind, "key": obligation.key, **extra})
        summary, pending = _summary(obligation, actor.name, deepcopy(result), extra)
        applied.append(Applied(obligation.kind, obligation.key, summary, pending))
        observability.event("turn.obligation", status="applied", kind=obligation.kind, key=obligation.key, pending=pending)
    return applied
