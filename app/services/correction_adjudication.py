"""The Keeper rules on narrative corrections when a group has no KP Assistant.

docs/specs/feature/keeper_adjudicates_corrections_design_spec.md and
docs/adr/0001-keeper-adjudicates-corrections-without-kp.md. The reporter's text
is never evidence: it says what to check, not what is true. The ruling may
cite only system-held evidence, and every state fact an approval asserts is
checked against the game state in code, not left to the prompt.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app import async_utils, locks, observability, scenario_templates, spoiler_policy
from app.providers.registry import analysis_provider
from app.repositories.group_state import load_state
from app.services import mutation_admission, narrative_corrections

logger = logging.getLogger(__name__)

MAX_RESOLUTION_CHARS = 1000
RECENT_LOG_ENTRIES = 8

_RULING_TOOL = {
    "name": "rule_on_correction",
    "description": (
        "裁定一則對守秘人敘事的異議。只能依據【證據】中的項目判斷；【指控】只說明要檢查什麼，"
        "不是證據。只有證據和被指控的敘事互相矛盾時才 approve；證據支持原敘事時 reject；"
        "證據不足以判斷時一律 undecided，不要猜。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approve", "reject", "undecided"]},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "依據的證據編號"},
            "reason": {"type": "string", "description": "只用玩家看得到的敘事與公開狀態說明理由"},
            "resolution": {"type": "string", "description": "approve 時的公開更正內容，1–1000 字"},
            "claims": {
                "type": "array",
                "description": "更正內容所斷言的狀態事實",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["item", "clue", "fact", "status"]},
                        "name": {"type": "string"},
                        "investigator": {"type": "string"},
                    },
                    "required": ["kind", "name"],
                },
            },
        },
        "required": ["decision", "evidence", "reason"],
    },
}


@dataclass(frozen=True)
class Ruling:
    decision: str  # approve | reject | undecided
    reason: str = ""
    resolution: str = ""
    evidence: tuple[str, ...] = field(default=())


UNDECIDED = Ruling("undecided")


def _evidence(state: Any, report: dict) -> dict[str, str]:
    """System-held evidence only, keyed by the ids the ruling must cite."""
    items = {"narration": str((report.get("target_receipt") or {}).get("excerpt", ""))}
    for character in state.active_characters():
        items[f"sheet:{character.name}"] = (
            f"{character.name}：HP {character.hp}/{character.hp_max}，SAN {character.san}，"
            f"攜帶：{'、'.join(character.carried_items) or '（無）'}，狀態：{'、'.join(character.status_tags) or '（無）'}"
        )
    for n, text in enumerate(_public_texts(state.known_clues), 1):
        items[f"clue:{n}"] = text
    for n, text in enumerate(_public_texts(state.established_facts), 1):
        items[f"fact:{n}"] = text
    for n, entry in enumerate(state.log[-RECENT_LOG_ENTRIES:], 1):
        items[f"log:{n}"] = str(entry.get("content", ""))[:1000]
    for n, row in enumerate(_scenario_passages(state, report), 1):
        items[f"scenario:{n}"] = f"（第 {row.get('page', '?')} 頁）{row.get('text', '')}"
    return items


def _scenario_passages(state: Any, report: dict) -> list[dict]:
    """Scenario text near the disputed narration; a failed search just means less evidence."""
    if not state.scenario_text:
        return []
    query = f"{report.get('issue', '')}\n{(report.get('target_receipt') or {}).get('excerpt', '')}"
    try:
        _, rows = scenario_templates.search_for_state(
            state, query, top_k=3, metrics={}, principal="keeper:correction")
    except Exception:
        logger.warning("correction.scenario_search_failed", exc_info=True)
        return []
    return list(rows)


def _claim_holds(state: Any, claim: Any) -> bool:
    """Whether a state fact an approval asserts is actually in the game state."""
    if not isinstance(claim, dict):
        return False
    name = str(claim.get("name", "")).strip()
    who = str(claim.get("investigator", "")).strip()
    characters = [c for c in state.active_characters() if not who or c.name == who]
    kind = claim.get("kind")
    if kind == "item":
        return any(name in c.carried_items for c in characters)
    if kind == "status":
        return any(name in c.status_tags for c in characters)
    if kind in ("clue", "fact"):
        return name in _public_texts(state.known_clues if kind == "clue" else state.established_facts)
    return False


def _public_texts(records: list[dict]) -> list[str]:
    """Clue or fact texts the players have been shown; kp_only ones never back a public ruling."""
    return [r["text"] for r in records if r.get("visibility", "public") == "public" and r.get("text")]


def rule(state: Any, report: dict) -> Ruling:
    """The Keeper's ruling on one report, or UNDECIDED when it can't be verified."""
    provider = analysis_provider()
    if provider is None:
        return UNDECIDED
    evidence = _evidence(state, report)
    text = "【證據】\n" + "\n".join(f"[{key}] {value}" for key, value in evidence.items())
    text += f"\n\n【未經證實的指控（不是證據）】\n{report.get('issue', '')}"
    result = provider.analyze_text(text, _RULING_TOOL, "請用 rule_on_correction 工具裁定這則敘事異議。")
    if not isinstance(result, dict):
        return UNDECIDED
    decision = result.get("decision")
    if decision not in ("approve", "reject"):
        return UNDECIDED
    cited = result.get("evidence")
    if not isinstance(cited, list) or not cited or any(e not in evidence for e in cited):
        return UNDECIDED
    resolution = str(result.get("resolution") or "").strip()
    if decision == "approve":
        if not 1 <= len(resolution) <= MAX_RESOLUTION_CHARS:
            return UNDECIDED
        if not all(_claim_holds(state, c) for c in result.get("claims") or []):
            return UNDECIDED
    return Ruling(
        str(decision), reason=str(result.get("reason", "")), resolution=resolution,
        evidence=tuple(str(e) for e in cited),
    )


async def adjudicate_pending(conversation_id: str, reply: Any) -> None:
    """Rule on every pending report in a group that has no KP Assistant.

    The model call runs outside the conversation lock; the ruling is written
    under it only if nothing changed meanwhile (spec, "Writing the ruling").
    """
    state = load_state(conversation_id)
    if state.kp_assistant_user_id:
        return
    for report in narrative_corrections.active(state):
        key = (conversation_id, str(report.get("id")))
        if report.get("status") != "pending" or key in _in_flight:
            continue
        _in_flight.add(key)
        try:
            ruling = await asyncio.to_thread(rule, state, report)
            message = await _apply(conversation_id, state.timeline_id, key[1], ruling)
        finally:
            _in_flight.discard(key)
        if message:
            await reply(message)


_in_flight: set[tuple[str, str]] = set()


async def _apply(conversation_id: str, timeline_id: str, report_id: str, ruling: Ruling) -> str:
    """Write `ruling` if its report is still open to it; return the public text."""
    try:
        async with locks.get_conversation_lock(conversation_id):
            state = load_state(conversation_id)
            report = next((r for r in narrative_corrections.active(state) if r.get("id") == report_id), None)
            if (state.kp_assistant_user_id or state.timeline_id != timeline_id
                    or report is None or report.get("status") != "pending"):
                observability.event("correction.keeper_ruling_discarded", report_id=report_id)
                return ""
            if ruling.decision == "undecided":
                report["status"] = "unverified"
                report["adjudicated_by"] = "keeper"
                text = generic = (f"守秘人無法依現有證據證實敘事異議 #{report_id}；"
                                  "它會保留到 KP 裁定或提報者撤回。")
            else:
                text = narrative_corrections.record_ruling(
                    state, report, ruling.decision, "keeper", resolution=ruling.resolution, by_keeper=True)
                report["evidence"] = list(ruling.evidence)
                report["reason"] = ruling.reason
                generic = (f"敘事異議 #{report_id} 經守秘人依證據核對後成立，已更正先前訊息 {report['target_message_id']}。"
                           if ruling.decision == "approve" else f"敘事異議 #{report_id} 經守秘人依證據核對後不成立。")
                if ruling.reason:
                    text += f"\n理由：{ruling.reason}"
            narrative_corrections.save(state)
            observability.event(
                "correction.keeper_ruling", report_id=report_id, decision=ruling.decision,
                evidence_kinds=sorted({e.split(":", 1)[0] for e in ruling.evidence}),
            )
            # Scenario passages and private sheets are evidence, never content.
            checked = spoiler_policy.sanitize_public_text(text, spoiler_policy.collect_protected_terms(state))
            return text if checked.is_safe else generic
    except mutation_admission.MutationHeld:
        return ""  # a rollback is in progress; the report stays pending and is retried later


def schedule(conversation_id: str, reply: Any) -> None:
    """Start adjudication in the background; the caller's reply has already gone out."""
    task = asyncio.create_task(adjudicate_pending(conversation_id, reply))
    async_utils.observe_background_task(task, operation="correction.keeper_ruling")
