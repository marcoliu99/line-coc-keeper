"""Why a gameplay turn ended in a generic reply, and one bounded way to recover first.

A generic "cannot continue" is not a result. Each fallback has a stable ``FallbackReason`` that is
logged with the turn's evidence (``turn.fallback``), and a turn whose Executor left no mark on the
game may search once more and decide once more before the player sees the blocker. Nothing here
changes state, replays a tool, or turns a failure into prose.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app import observability, scenario_retrieval
from app.domain.models import FALLBACK_REASONS, FallbackReason, MechanicResult
from app.models import GroupState
from app.services import turn_phases

__all__ = ["FALLBACK_REASONS", "FallbackRecord", "classify", "guidance", "record", "recoverable", "recovery_query"]

# What the player is told when the turn has no more specific wording; every reason has one.
_GUIDANCE: dict[str, str] = {
    "no_scenario_evidence": "目前查不到足以裁決這個行動的劇本依據，系統已暫停相關操作；請換個說法，或說明你想對哪個地點、人物或物件做什麼。",
    "executor_no_action": "守密人沒有為這個行動找到可以執行的處理；請把行動說得更具體（對象、方式）後再試。",
    "unresolved_pending_state": "還有尚未完成的檢定、Luck 決定或他人的行動；請先完成它，再宣告新的行動。",
    "invalid_tool_plan": "守密人的裁決沒有通過核對，這次行動沒有執行；請再說一次你的行動。",
    "tool_failure": "處理這個行動的工具失敗了；已完成的變更會保留，請稍後重試或換個做法。",
    "tool_result_rejected": "工具的結果沒有通過核對，這次行動沒有被接受；請再說一次你的行動。",
    "narration_failure": "守密人暫時無法完成敘事。已提交的變更會保留；請查看目前狀態，不要重擲或重做剛才的行動。",
    "state_conflict": "目前的狀態和這次行動不一致（例如時間線已更新）；請依目前劇情重新操作。",
    "unsupported_action": "依目前的劇本與場景狀態，這個行動無法進行；請改試別的做法。",
    "safety_block": "這段回覆沒有通過安全檢查，已改為中性的說明；請換個說法再試。",
    "internal_error": "系統發生內部錯誤；已提交的變更會保留，請稍後再試。",
    "unknown": "這次行動沒有被接受，原因不明；請先確認目前狀態或更正原本的行動。",
}
assert set(_GUIDANCE) == set(FALLBACK_REASONS)

# The Executor's own answer was unusable (malformed, unsourced or about another actor).
_INVALID_DECISION = {
    "invalid_json", "invalid_object", "completion_too_long", "invalid_fields",
    "invalid_actor_or_disposition", "invalid_evidence_format", "invalid_evidence_reference", "missing_actor",
}
_STATE_CONFLICT = {"pending_identity_mismatch", "cancellation_not_verified"}
_PENDING = {"luck_takes_precedence", "unfinished_check_or_luck", "deferral_not_verified"}
_REJECTED = {"inventory_or_combat_not_verified", "missing_resolved_effect", "no_mechanics_has_effects"}
# Only a turn that failed for lack of grounding or of a decision is worth one more try.
RECOVERABLE: frozenset[str] = frozenset({"no_scenario_evidence", "executor_no_action", "invalid_tool_plan", "unsupported_action"})

_HEADER = re.compile(r"^--- (?:原稿補查 · )?第 (\d+) 頁 ---$", re.MULTILINE)


def guidance(reason: str | None) -> str:
    return _GUIDANCE.get(reason or "unknown", _GUIDANCE["unknown"])


def _has_scenario_evidence(result: MechanicResult, rag_status: str, state: GroupState) -> bool:
    # "disabled" means retrieval was off or skipped, not that nothing was found: the whole scenario is in the prompt.
    return (rag_status == "success" or (rag_status == "disabled" and bool(state.scenario_text))
            or any(name == "search_scenario" and ok for name, ok in result.tool_calls))


def _waiting(state: GroupState, user_id: str) -> bool:
    return any(owner == user_id for owner in (*state.pending_checks, *state.pending_luck_decisions))


def classify(result: MechanicResult | None, state: GroupState, user_id: str, *, rag_status: str = "") -> FallbackReason | None:
    """The reason for a generic reply to this turn's mechanics, or None when the turn is not a fallback."""
    resolution = None if result is None else result.turn_resolution
    if result is None or resolution is None:
        return None
    if resolution.disposition == "deferred":
        return "unresolved_pending_state"
    if resolution.disposition not in {"incomplete", "blocked"}:
        return None
    code = resolution.validation_code
    failed_tool = any(not ok for _, ok in result.tool_calls)
    if result.execution_health != "completed" or not result.success:
        return "tool_failure" if failed_tool else "internal_error"
    if result.check_status.get("scenario_evidence_blocked") or code == "missing_scenario_or_mutation_evidence":
        return "no_scenario_evidence"
    if code in _INVALID_DECISION:
        return "invalid_tool_plan"
    if code in _STATE_CONFLICT:
        return "state_conflict"
    if code in _PENDING:
        return "unresolved_pending_state"
    if code in _REJECTED:
        return "tool_result_rejected"
    if code == "model_incomplete":
        return "tool_failure" if failed_tool else "executor_no_action"
    if code == "validated" and resolution.disposition == "blocked":
        if _waiting(state, user_id):
            return "unresolved_pending_state"
        return "unsupported_action" if _has_scenario_evidence(result, rag_status, state) else "no_scenario_evidence"
    return "unknown"


def recoverable(reason: str | None, result: MechanicResult, *, before_pending: dict, before_luck: dict, state: GroupState) -> bool:
    """True only when running the Executor again cannot apply anything twice.

    The first attempt must have changed no game state, rolled no dice, and left every pending check and
    Luck decision as it found it; its tools may only have looked things up.
    """
    status = result.check_status
    return bool(
        reason in RECOVERABLE
        and not status.get("state_changed") and not status.get("dice_rolled") and not status.get("resolved")
        and state.pending_checks == before_pending and state.pending_luck_decisions == before_luck
        and not result.events and not result.observed_outcomes
    )


def recovery_query(state: GroupState, text: str, resolved_location: dict[str, Any] | None) -> str:
    """The current scene and the player's action; never a broad "what happens next"."""
    scene = ""
    if isinstance(resolved_location, dict):
        scene = str(resolved_location.get("name") or resolved_location.get("location") or "")
    return " ".join(part for part in (scene.strip(), (state.active_chapter_id or "").strip(), text.strip()) if part)[:300]


@dataclass(frozen=True)
class FallbackRecord:
    reason: FallbackReason
    decision: str
    tool_calls: tuple[str, ...]
    retrieval_count: int
    retrieval_hit_ids: tuple[str, ...]
    recovery_attempted: bool
    recovery_result: str


def _hits(rag_context: str) -> tuple[int, tuple[str, ...]]:
    pages = _HEADER.findall(rag_context or "")
    fragments = sorted(scenario_retrieval.delivered_fragments(rag_context or ""))
    ids = tuple(dict.fromkeys([*(f"page:{page}" for page in pages), *fragments]))[:20]
    return len(pages) + len(fragments), ids


def record(
    reason: FallbackReason, *, state: GroupState, user_id: str, turn_id: str, rag_context: str = "",
    result: MechanicResult | None = None, resolved_location: dict[str, Any] | None = None,
    recovery_attempted: bool = False, recovery_result: str = "not_attempted", initial_reason: str | None = None,
) -> FallbackRecord:
    """Log one ``turn.fallback`` event carrying the turn's evidence, and return what was logged."""
    count, hit_ids = _hits(rag_context)
    resolution = None if result is None else result.turn_resolution
    pending = [kind for kind, owners in (("check", state.pending_checks), ("luck", state.pending_luck_decisions)) if user_id in owners]
    others = sorted({kind for kind, owners in (("check", state.pending_checks), ("luck", state.pending_luck_decisions))
                     if any(owner != user_id for owner in owners)})
    row = FallbackRecord(
        reason=reason, decision=resolution.disposition if resolution else "none",
        tool_calls=tuple(name for name, _ in (result.tool_calls if result else ())),
        retrieval_count=count, retrieval_hit_ids=hit_ids,
        recovery_attempted=recovery_attempted, recovery_result=recovery_result,
    )
    scene = ""
    if isinstance(resolved_location, dict):
        scene = str(resolved_location.get("name") or resolved_location.get("location") or "")
    turn_phases.note(fallback=reason)
    observability.event(
        "turn.fallback", level=logging.WARNING if reason in {"tool_failure", "internal_error", "unknown"} else logging.INFO,
        fallback_reason=reason, campaign_id=observability.safe_identifier(state.group_id),
        timeline_id=state.timeline_id, player_id=observability.safe_identifier(user_id), turn_id=turn_id,
        scene=scene or None, chapter_id=state.active_chapter_id or None,
        pending_own=pending, pending_others=others,
        retrieval_count=row.retrieval_count, retrieval_hit_ids=list(row.retrieval_hit_ids),
        executor_decision=row.decision, tool_calls=list(row.tool_calls),
        failed_tool_calls=[name for name, ok in (result.tool_calls if result else ()) if not ok],
        recovery_attempted=recovery_attempted, recovery_result=recovery_result, initial_reason=initial_reason,
    )
    return row
