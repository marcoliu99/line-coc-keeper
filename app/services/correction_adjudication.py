"""The Keeper rules on narrative corrections when a group has no KP Assistant.

docs/specs/feature/keeper_adjudicates_corrections_design_spec.md and
docs/adr/0001-keeper-adjudicates-corrections-without-kp.md. The reporter's text
is never evidence: it says what to check, not what is true. The ruling may
cite only system-held evidence, and every state fact an approval asserts is
checked against the game state in code, not left to the prompt.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app import (
    async_utils,
    locks,
    observability,
    scenario_templates,
    scene_map,
    spoiler_policy,
)
from app.providers.registry import analysis_provider
from app.repositories.group_state import load_state
from app.services import correction_summary, mutation_admission, narrative_corrections

logger = logging.getLogger(__name__)

MAX_RESOLUTION_CHARS = 1000
RECENT_LOG_ENTRIES = 8
LOG_AROUND_TURN = 3

Decision = Literal["approve", "reject", "undecided"]
# Sheet values an approval may cite, by the names the model uses.
_STATS = {"HP": "hp", "MP": "mp", "SAN": "san", "LUCK": "luck", "幸運": "luck"}

_RULING_TOOL = {
    "name": "rule_on_correction",
    "description": (
        "裁定一則對守秘人敘事的異議。只能依據【證據】中的項目判斷；【指控】只說明要檢查什麼，"
        "不是證據。只有證據和被指控的敘事互相矛盾時才 approve；證據支持原敘事時 reject；"
        "證據不足以判斷時一律 undecided，不要猜。劇本若明確交付一件物品但背包漏記，"
        "可提出 item claim；須引用包含該物品的劇本段落，不能只憑玩家聲明。"
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
                "description": "更正內容所斷言的每一項狀態事實；approve 至少一項",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["item", "clue", "fact", "status", "skill", "stat"]},
                        "name": {"type": "string", "description": "更正內容中出現的名稱；stat 用 HP、MP、SAN、LUCK"},
                        "investigator": {"type": "string"},
                        "value": {"type": "integer", "description": "skill 或 stat 的數值"},
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
    decision: Decision
    reason: str = ""
    resolution: str = ""
    evidence: tuple[str, ...] = field(default=())
    item_repair: tuple[str, str] | None = None
    item_source_ref: dict[str, str] | None = None


UNDECIDED = Ruling("undecided")


def _evidence(state: Any, report: dict) -> dict[str, str]:
    """System-held evidence only, keyed by the ids the ruling must cite."""
    receipt = report.get("target_receipt") or {}
    excerpt = str(receipt.get("excerpt", ""))
    items = {"narration": (f"（第 {receipt.get('state_revision', '?')} 版狀態，回合 {receipt.get('turn_id') or '?'}）"
                           f"{excerpt}")}
    for character in state.active_characters():
        skills = "、".join(f"{k} {v}" for k, v in character.skills.items())
        items[f"sheet:{character.name}"] = (
            f"{character.name}：HP {character.hp}/{character.hp_max}，MP {character.mp}，SAN {character.san}，"
            f"LUCK {character.luck}，地點：{_tracked_location(state, character.owner_id)}，"
            f"攜帶：{'、'.join(character.carried_items) or '（無）'}，狀態：{'、'.join(character.status_tags) or '（無）'}，"
            f"技能：{skills or '（無）'}"
        )
    for n, text in enumerate(_public_texts(state.known_clues), 1):
        items[f"clue:{n}"] = text
    for n, text in enumerate(_public_texts(state.established_facts), 1):
        items[f"fact:{n}"] = text
    for n, entry in enumerate(_log_around(state.log, excerpt), 1):
        items[f"log:{n}"] = "（僅證明當時曾這樣說，不證明世界真相）" + str(entry.get("content", ""))[:1000]
    for n, row in enumerate(_scenario_passages(state, report), 1):
        items[f"scenario:{n}"] = f"（第 {row.get('page', '?')} 頁）{row.get('text', '')}"
    return items


def _tracked_location(state: Any, owner_id: str) -> str:
    """Where a map is tracked for this investigator (as `/coc where` shows it);
    not every scene has a map, so this is often unavailable."""
    page = state.current_map_page.get(owner_id, "")
    scene = state.scene_maps.get(page) if page else None
    room = scene_map.get_room(scene, state.current_room_id.get(owner_id, "")) if scene else None
    return room.get("name", "") if room else "（未記錄）"


def _log_around(log: list[dict], excerpt: str) -> list[dict]:
    """The log entries around the disputed narration, else the most recent ones."""
    probe = excerpt.strip()[:200]
    if probe:
        for index in range(len(log) - 1, -1, -1):
            if probe in str(log[index].get("content", "")):
                return log[max(0, index - LOG_AROUND_TURN):index + LOG_AROUND_TURN + 1]
    return log[-RECENT_LOG_ENTRIES:]


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
    value = claim.get("value")
    if kind == "skill":
        return any(c.skills.get(name) == value for c in characters)
    if kind == "stat" and name.upper() in _STATS:
        return any(getattr(c, _STATS[name.upper()]) == value for c in characters)
    return False


def _public_texts(records: list[dict]) -> list[str]:
    """Clue or fact texts the players have been shown; kp_only ones never back a public ruling."""
    return [
        r["text"] for r in records
        if r.get("visibility", "public") == "public" and r.get("text")
        and r.get("verification_status") == "verified" and r.get("source_ref")
    ]


def rule(state: Any, report: dict) -> Ruling:
    """The Keeper's ruling on one report, or UNDECIDED when it can't be verified."""
    provider = analysis_provider()
    if provider is None:
        return UNDECIDED
    evidence = _evidence(state, report)
    text = "【系統資料；log 與 narration 只能證明當時說過什麼，不能單獨證明世界事實】\n"
    text += "\n".join(f"[{key}] {value}" for key, value in evidence.items())
    text += f"\n\n【未經證實的指控（不是證據）】\n{report.get('issue', '')}"
    try:
        result = provider.analyze_text(text, _RULING_TOOL, "請用 rule_on_correction 工具裁定這則敘事異議。")
    except Exception:
        logger.warning("correction.keeper_ruling_failed", exc_info=True)
        return UNDECIDED
    return _validated(state, evidence, result)


def _validated(state: Any, evidence: dict[str, str], result: Any) -> Ruling:
    """The model's ruling if it passes every check made in code, else UNDECIDED."""
    if not isinstance(result, dict):
        return UNDECIDED
    decision = result.get("decision")
    cited = result.get("evidence")
    if decision not in ("approve", "reject") or not isinstance(cited, list) or not cited:
        return UNDECIDED
    if not all(isinstance(e, str) and e in evidence for e in cited):
        return UNDECIDED
    if not any(e.startswith(("sheet:", "scenario:", "clue:", "fact:")) for e in cited):
        return UNDECIDED
    resolution = str(result.get("resolution") or "").strip()
    item_repair = None
    if decision == "approve" and not _approval_holds(state, cited, resolution, result.get("claims"), evidence):
        return UNDECIDED
    if decision == "approve":
        for claim in result.get("claims", []):
            if claim.get("kind") == "item" and not _claim_holds(state, claim):
                item_repair = (str(claim.get("investigator", "")), str(claim.get("name", "")))
    item_source_ref = None
    if item_repair:
        quoted = "\n".join(evidence[key] for key in cited if key.startswith("scenario:"))
        item_source_ref = {
            "scenario_id": str(state.scenario_library_id or "scenario-text"),
            "scenario_hash": hashlib.sha256(state.scenario_text.encode()).hexdigest(),
            "quote_digest": hashlib.sha256(quoted.encode()).hexdigest(),
            "source_excerpt": quoted[:2000],
        }
    return Ruling(decision, reason=str(result.get("reason", "")), resolution=resolution,
                  evidence=tuple(cited), item_repair=item_repair, item_source_ref=item_source_ref)


def _approval_holds(state: Any, cited: list[str], resolution: str, claims: Any,
                    evidence: dict[str, str] | None = None) -> bool:
    """An approval must contradict the narration with other evidence, and every
    state fact its text asserts must be declared as a claim that holds."""
    if not 1 <= len(resolution) <= MAX_RESOLUTION_CHARS:
        return False
    if not any(e.startswith(("sheet:", "scenario:", "clue:", "fact:")) for e in cited):
        return False
    if not isinstance(claims, list) or not claims:
        return False
    repairs = [c for c in claims if isinstance(c, dict) and c.get("kind") == "item" and not _claim_holds(state, c)]
    if len(repairs) > 1:
        return False
    def supported(claim: Any) -> bool:
        if not isinstance(claim, dict) or str(claim.get("name", "")).strip() not in resolution:
            return False
        if _claim_holds(state, claim):
            return True
        if claim not in repairs or evidence is None:
            return False
        name = str(claim.get("name", "")).strip()
        who = str(claim.get("investigator", "")).strip()
        if not who or not any(character.name == who for character in state.active_characters()):
            return False
        aliases = {name.casefold()}
        if "鑰匙" in name:
            aliases.update({"key", "keys"})
        recipients = (who.casefold(), "the investigators", "investigators", "調查員", "你們")
        grant = r"(?:hands?|gives?|gave|provided|provides|supplied|交給|交付|給了|給予)"
        # Each grant clause must name both this item and this recipient. Never
        # combine an unrelated grant and hidden item across passages/clauses.
        for key in cited:
            if not key.startswith("scenario:"):
                continue
            for clause in re.split(r"[.!?。！？；;\n]|\band\b|\bbut\b", evidence[key].casefold()):
                for recipient in recipients:
                    for alias in aliases:
                        item_pattern = re.escape(alias) if not alias.isascii() else rf"\b{re.escape(alias)}\b"
                        receiver = re.escape(recipient)
                        if (re.search(rf"{grant}\s*(?:the\s+)?{receiver}\s*(?:a\s+|an\s+|the\s+)?{item_pattern}", clause)
                                or re.search(rf"{grant}\s*(?:a\s+|an\s+|the\s+)?{item_pattern}\s*(?:to|給)\s*{receiver}", clause)):
                            return True
        return False
    return all(supported(claim) for claim in claims)


async def adjudicate_pending(conversation_id: str, reply: Any) -> None:
    """Rule on every pending report in a group that has no KP Assistant.

    The model call runs outside the conversation lock; the ruling is written
    under it only if nothing changed meanwhile (spec, "Writing the ruling").
    """
    state = load_state(conversation_id)
    if state.kp_assistant_user_id:
        return
    for report_id in _pending_report_ids(state):
        key = (conversation_id, report_id)
        if key in _in_flight:
            continue
        # Another run may have ruled on it since this one started.
        state = load_state(conversation_id)
        report = _pending_report(state, report_id)
        if state.kp_assistant_user_id or report is None:
            continue
        _in_flight.add(key)
        try:
            ruling = await asyncio.to_thread(rule, state, report)
            message = await _apply(conversation_id, state.timeline_id, key[1], ruling)
        finally:
            _in_flight.discard(key)
        if message:
            await reply(message)
            if ruling.decision == "approve":
                correction_summary.schedule(conversation_id)


_in_flight: set[tuple[str, str]] = set()


async def _apply(conversation_id: str, timeline_id: str, report_id: str, ruling: Ruling) -> str:
    """Write `ruling` if its report is still open to it; return the public text."""
    try:
        async with locks.get_conversation_lock(conversation_id):
            state = load_state(conversation_id)
            report = _pending_report(state, report_id)
            if state.kp_assistant_user_id or state.timeline_id != timeline_id or report is None:
                observability.event("correction.keeper_ruling_discarded", report_id=report_id)
                return ""
            # Scenario passages and private sheet notes are evidence, never
            # content: an approval whose text would reveal a protected term is
            # never recorded, since the log feeds every later turn.
            protected = spoiler_policy.collect_protected_terms(state)
            if ruling.decision == "approve" and not spoiler_policy.sanitize_public_text(
                    ruling.resolution, protected).is_safe:
                observability.event("correction.keeper_ruling_withheld", report_id=report_id)
                ruling = UNDECIDED
            if ruling.decision == "approve" and ruling.item_repair:
                from app.keeper_tools.inventory import add_carried_item
                from app.keeper_tools.registry import ToolCall

                investigator, item = ruling.item_repair
                receipt = add_carried_item(ToolCall(
                    state, {"investigator": investigator, "item": item}, [], [],
                    "player", "add_carried_item", system_origin="correction",
                ))
                if receipt.get("ok"):
                    report = _pending_report(state, report_id)
                    if report is None:
                        return ""
                    report["inventory_item"] = item
                    report["inventory_source_ref"] = ruling.item_source_ref
                else:
                    ruling = UNDECIDED
            if ruling.decision == "undecided":
                narrative_corrections.record_unverified(report)
                text = generic = (f"守秘人無法依現有證據證實敘事異議 #{report_id}；"
                                  "它會保留到 KP 裁定或提報者撤回。")
            else:
                text = narrative_corrections.record_ruling(
                    state, report, ruling.decision, "keeper", resolution=ruling.resolution,
                    keeper_basis=narrative_corrections.KeeperBasis(ruling.evidence, ruling.reason),
                )
                generic = (f"敘事異議 #{report_id} 經守秘人依證據核對後成立，已更正先前訊息 {report['target_message_id']}。"
                           if ruling.decision == "approve" else f"敘事異議 #{report_id} 經守秘人依證據核對後不成立。")
                # The model's own reason stays in the record: it has read the
                # scenario, so only what it cited is named in public.
                text += f"\n依據：{_public_basis(ruling.evidence)}。"
            narrative_corrections.save(state)
            observability.event(
                "correction.keeper_ruling", report_id=report_id, decision=ruling.decision,
                evidence_kinds=sorted({e.split(":", 1)[0] for e in ruling.evidence}),
            )
            return text if spoiler_policy.sanitize_public_text(text, protected).is_safe else generic
    except mutation_admission.MutationHeld:
        return ""  # a rollback is in progress; the report stays pending and is retried later


_BASIS_LABELS = {"narration": "原敘事", "clue": "公開線索", "fact": "既定事實",
                 "log": "先前的遊戲紀錄", "scenario": "劇本資料"}


def _public_basis(evidence: tuple[str, ...]) -> str:
    """What a ruling rested on, named by kind: `sheet:Ada` is Ada's sheet, a
    scenario passage is only "劇本資料", never its text."""
    labels: list[str] = []
    for item in evidence:
        kind, _, name = item.partition(":")
        label = f"{name} 的角色卡" if kind == "sheet" else _BASIS_LABELS.get(kind, "")
        if label and label not in labels:
            labels.append(label)
    return "、".join(labels)


def _pending_report(state: Any, report_id: str) -> dict | None:
    return next((r for r in narrative_corrections.active(state)
                 if r.get("id") == report_id and r.get("status") == "pending"), None)


def schedule(conversation_id: str, state: Any, reply: Any) -> None:
    """Start adjudication in the background if the Keeper has anything to rule on.

    The Keeper rules only while the group has no KP Assistant; the caller's
    reply has already gone out.
    """
    if state.kp_assistant_user_id or not _pending_report_ids(state):
        return
    task = asyncio.create_task(adjudicate_pending(conversation_id, reply))
    async_utils.observe_background_task(task, operation="correction.keeper_ruling")


def _pending_report_ids(state: Any) -> list[str]:
    return [str(r.get("id")) for r in narrative_corrections.active(state) if r.get("status") == "pending"]
