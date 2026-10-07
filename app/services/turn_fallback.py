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

__all__ = ["FALLBACK_REASONS", "FallbackRecord", "classify", "guidance", "record", "recoverable", "recovery_query", "scene_hints"]

# What the player is told when the turn has no more specific wording; every reason has one.
_GUIDANCE: dict[str, str] = {
    "no_scenario_evidence": "目前查不到足以裁決這個行動的劇本依據，系統已暫停相關操作；請換個說法，或說明你想對哪個地點、人物或物件做什麼。",
    "executor_no_action": (
        "劇本裡沒有足夠的內容可以據以裁決這個行動，守密人沒有替它編造。"
        "可以改問劇本中已經出現的人物、物件或地點；如果行動很籠統，也可以補上對象與方式再試一次。"
    ),
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


# Reasons whose message asks the player to try something else, so naming what they can try is the useful part.
_HINTED: frozenset[str] = frozenset({"no_scenario_evidence", "executor_no_action", "unsupported_action"})
_HINT_LIMIT = 5
_CLUE_HINT_CHARS = 24
_RECENT_NARRATION = 60


def guidance(reason: str | None, hints: str = "") -> str:
    """What the player is told; ``hints`` (see ``scene_hints``) is added for reasons that ask them to try something else."""
    text = _GUIDANCE.get(reason or "unknown", _GUIDANCE["unknown"])
    return f"{text}\n{hints}" if hints and reason in _HINTED else text


# Reasons whose generic wording talks about the scenario; in a battle the table needs to know whose turn it is instead.
_COMBAT_REASONS: frozenset[str] = frozenset({"no_scenario_evidence", "executor_no_action", "unsupported_action"})


def combat_guidance(state: GroupState, reason: str | None) -> str:
    """What to tell the table when a turn in a running battle could not be played; empty outside one."""
    battle = state.combat
    if reason not in _COMBAT_REASONS or not battle.active or not battle.order:
        return ""
    if battle.phase == "SETTLEMENT":
        return "戰鬥已經結束，正在等守密人結算；結算後就能繼續探索。"
    enemies = [c for c in battle.order if c.side == "enemy"]
    if enemies and all(c.defeated for c in enemies):
        return "敵方已全數倒下，戰鬥等著結算；結算後就能繼續探索。"
    if battle.phase == "NEEDS_RULING":
        return "戰鬥暫停中，要等守密人裁定後才能繼續。"
    if battle.phase == "LUCK_DECISION":
        return "戰鬥暫停中，有人正在做 Luck 決定；請用 Luck 按鈕或輸入 /coc luck 完成，才能繼續。"
    if (battle.interaction or battle.phase not in {"READY", "RESOLVE"}
            or any(not action.get("completed") for action in battle.actions.values())):
        return "戰鬥暫停中，還有尚未完成的檢定或選擇；請先完成它（按檢定按鈕或輸入 /coc check），才能繼續。"
    current = battle.order[min(battle.current_index, len(battle.order) - 1)]
    if current.defeated:
        return f"「{current.display_name}」已經倒下，等守密人推進到下一位。"
    if any(action.get("actor_id") == current.combatant_id and action.get("completed")
           and action.get("round") == battle.round_number for action in battle.actions.values()):
        return f"「{current.display_name}」這一輪已經行動完畢，等守密人推進到下一位。"
    return (f"戰鬥進行中，現在輪到「{current.display_name}」行動。輪到你時，請說明要對哪個目標、用什麼方式攻擊或行動；"
            "還沒輪到你時，請稍候。")


def _public_narration(state: GroupState) -> list[str]:
    """What players have actually been told in this timeline, newest first.

    Player lines and anything not public are left out. A new scenario starts a new timeline but keeps the log, so
    narration and clues stamped with another timeline (or with none: unverified clues are stored unstamped, and a new scenario keeps them) are left out too: it was
    about a different scenario and must not make a same-named entry of this one look disclosed. Only narration that
    still stands counts: text an approved correction replaced (``superseded_by``) was withdrawn, and a correction's own
    wording ("narrative_correction") may name the very thing it denies.
    """
    return [str(entry.get("content", "")) for entry in reversed(state.log[-_RECENT_NARRATION:])
            if entry.get("role") == "assistant" and entry.get("audience", "public") == "public"
            and entry.get("timeline_id", "") == (state.timeline_id or "")
            and entry.get("record_kind") == "narrative" and not entry.get("superseded_by")]


def _names(entry: dict[str, Any]) -> list[str]:
    """An index entry's canonical name and aliases, the ones long enough to match on."""
    return [n for n in (str(entry.get("name") or "").strip(), *(str(a).strip() for a in entry.get("aliases") or []))
            if len(n) >= 2]


_ASCII_WORD = "A-Za-z0-9"


def _occurs(name: str, text: str, longer: list[str]) -> bool:
    """Whether ``name`` is used in ``text`` rather than only as part of a longer name the scenario also has.

    A name is not looked for inside a longer index name that contains it (a "房東" must not be found in the
    narration's "房東太太" when she is an entry of her own). Names written in ASCII also must not touch other
    ASCII letters or digits. A longer phrase that no index lists cannot be told apart, so a short name can still
    match inside it; the line then names a character the narration did mention in some form, never one it did not.
    """
    for other in longer:
        text = text.replace(other, " ")
    if name.isascii():
        return re.search(rf"(?<![{_ASCII_WORD}]){re.escape(name)}(?![{_ASCII_WORD}])", text) is not None
    return name in text


def _already_shown(index: list[dict[str, Any]], narration: list[str], every_name: list[str]) -> list[str]:
    """Names from a scenario index that the narration has already used, the most recently mentioned first.

    Each entry appears once, written the way the narration wrote it (the most recently narrated spelling if it used several): an entry whose alias was narrated is listed under that alias, never
    under its canonical name, which the narration may not have said and which may itself give something away.
    """
    found: list[tuple[int, int, str]] = []
    for entry in index:
        spellings = []
        for name in _names(entry):
            longer = [other for other in every_name if len(other) > len(name) and name in other]
            position = next((i for i, text in enumerate(narration) if _occurs(name, text, longer)), None)
            if position is not None:
                spellings.append((position, -len(name), name))
        if spellings:
            found.append(min(spellings))  # one spelling per entry: the one narrated most recently
    return list(dict.fromkeys(name for _, _, name in sorted(found)))[:_HINT_LIMIT]


def scene_hints(state: GroupState) -> str:
    """The places, people and clues the players have already been shown, as one line; empty when there are none.

    Built only from what the narration has already said to the table (a scenario index entry counts once its name or an
    alias has appeared in public narration, and it is listed under the spelling that appeared) and from public clues, so
    it can only repeat what players know and never names an unvisited location or an undisclosed character. A name the
    narration translated differently from the index will not match, in which case the line is simply shorter.
    """
    narration = _public_narration(state)
    every_name = [n for entry in (*state.scenario_location_index, *state.scenario_npc_index) for n in _names(entry)]
    parts: list[str] = []
    places = _already_shown(state.scenario_location_index, narration, every_name)
    if places:
        parts.append("地點：" + "、".join(places))
    people = _already_shown(state.scenario_npc_index, narration, every_name)
    if people:
        parts.append("人物：" + "、".join(people))
    clues = [str(c.get("text", "")).strip() for c in reversed(state.known_clues)
             if c.get("visibility", "public") == "public" and str(c.get("text", "")).strip()
             and c.get("timeline_id", "") == (state.timeline_id or "")]
    clues = [c if len(c) <= _CLUE_HINT_CHARS else c[:_CLUE_HINT_CHARS] + "…" for c in clues[:3]]
    if clues:
        parts.append("已記錄的線索：" + "；".join(clues))
    return ("目前已在劇情中出現、可以接著問或查看的有——" + "　".join(parts) + "。") if parts else ""


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


def _failed_before_any_tool(reason: str | None, result: MechanicResult) -> bool:
    """The model request itself failed (timeout, CLI error) before a single tool ran, so there is nothing to repeat.

    Real runs lost turns this way after 52-120 s with no tool call at all. ``execution_health == "failed"`` is only
    set when the game state is also unchanged; a failure after any tool call stays a fallback.
    """
    return (reason == "internal_error" and result.execution_health == "failed"
            and not result.tool_calls and not result.observed_outcomes)


def recoverable(reason: str | None, result: MechanicResult, *, before_pending: dict, before_luck: dict, state: GroupState) -> bool:
    """True only when running the Executor again cannot apply anything twice.

    The first attempt must have changed no game state, rolled no dice, and left every pending check and
    Luck decision as it found it; its tools may only have looked things up. Looking something up is itself a tool
    call with a recorded outcome (``search_scenario`` leaves an internal one), so an outcome only counts against a
    retry when it came from a tool that is not read-only. Counting every outcome made any turn that searched the
    scenario unrecoverable: in a 200-turn run, one of 18 searching fallback turns was retried and four of four that
    made no tool call were.
    """
    from app.keeper_tools import (
        registry,  # imported here: the tool registry imports modules that import this one
    )

    status = result.check_status
    return bool(
        (reason in RECOVERABLE or _failed_before_any_tool(reason, result))
        and not status.get("state_changed") and not status.get("dice_rolled") and not status.get("resolved")
        and state.pending_checks == before_pending and state.pending_luck_decisions == before_luck
        and not result.events
        and all(outcome.tool_name in registry.READ_ONLY_TOOL_NAMES for outcome in result.observed_outcomes)
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
