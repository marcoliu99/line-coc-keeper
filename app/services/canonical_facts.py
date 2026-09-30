"""Audience-scoped facts from durable sources, never from Keeper prose."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app import scenario_templates
from app.models import GroupState

SourceKind = Literal["scenario", "check_event", "kp_canon"]


@dataclass(frozen=True)
class CanonicalFactRef:
    fact_id: str
    text: str
    source_kind: SourceKind
    source_ref: str | dict[str, str]
    visibility: str
    timeline_id: str
    subject_ids: tuple[str, ...] = ()
    recipient_ids: tuple[str, ...] = ()
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NarrationRequirements:
    authoritative_facts: tuple[CanonicalFactRef, ...] = ()
    committed_events: tuple[dict[str, Any], ...] = ()
    pending: tuple[dict[str, Any], ...] = ()
    allowed_evidence_refs: tuple[str, ...] = ()
    hard_constraints: tuple[dict[str, Any], ...] = ()


def _visible(record: dict[str, Any], recipient_id: str, speaker_role: str) -> bool:
    visibility = record.get("visibility", "public")
    if visibility == "public":
        return True
    if speaker_role == "kp_assistant":
        return True
    recipients = record.get("recipient_ids", [])
    return visibility == "private" and recipient_id in recipients


def _scenario_source_valid(state: GroupState, source_ref: Any, receipt: Any = None) -> bool:
    if not isinstance(source_ref, dict):
        return False
    record_id = source_ref.get("record_id", "")
    quote = source_ref.get("source_quote", "")
    current = scenario_templates.match_fact_source(state, record_id, quote, historical=True)
    if current is None or not all(current.get(key) == source_ref.get(key)
                                  for key in ("scenario_id", "source_hash", "variant_id",
                                              "record_id", "content_digest", "source_quote")):
        return False
    trigger_event_id = source_ref.get("trigger_event_id")
    return not trigger_event_id or any(
        event.get("event_id") == trigger_event_id and event.get("timeline_id") == state.timeline_id
        and "成功" in str(event.get("outcome", ""))
        for event in [*state.resolved_check_events, *([receipt] if isinstance(receipt, dict) else [])]
    )


def project(state: GroupState, *, recipient_id: str = "", speaker_role: str = "player") -> list[CanonicalFactRef]:
    """Revalidate source identity and timeline before projecting stored facts."""
    facts: list[CanonicalFactRef] = []
    for collection in (state.established_facts, state.known_clues):
        for record in collection:
            if (record.get("verification_status") != "verified"
                    or record.get("timeline_id", state.timeline_id) != state.timeline_id
                    or not _visible(record, recipient_id, speaker_role)):
                continue
            source_kind = record.get("source_kind")
            source_ref = record.get("source_ref")
            if source_kind == "scenario":
                if not _scenario_source_valid(state, source_ref, record.get("discovery_receipt")):
                    continue
            elif source_kind == "check_event":
                if not isinstance(source_ref, str) or not any(
                    event.get("event_id") == source_ref and event.get("timeline_id") == state.timeline_id
                    for event in state.resolved_check_events
                ):
                    continue
            else:
                continue
            fact_id = record.get("fact_id")
            if (not isinstance(fact_id, str) or not fact_id
                    or not isinstance(source_ref, (str, dict))):
                continue
            facts.append(CanonicalFactRef(
                fact_id=fact_id, text=str(record["text"]),
                source_kind=source_kind, source_ref=source_ref,
                visibility=str(record.get("visibility", "public")), timeline_id=state.timeline_id,
                subject_ids=tuple(record.get("subject_ids", [])),
                recipient_ids=tuple(record.get("recipient_ids", [])),
                constraints=dict(record.get("constraints", {})),
            ))
    for entry in state.log:
        if (entry.get("record_kind") != "kp_canon" or entry.get("authority") != "authoritative"
                or entry.get("timeline_id") != state.timeline_id
                or not _visible(entry, recipient_id, speaker_role)):
            continue
        text = str(entry.get("content", ""))
        if text.startswith("[KP Assistant] "):
            text = text[len("[KP Assistant] "):].split("\n\n[DETERMINISTIC GAME WORKFLOW]", 1)[0]
        turn_id = str(entry.get("turn_id", ""))
        if not turn_id or not text:
            continue
        digest = hashlib.sha256(f"{state.timeline_id}:{turn_id}:{text}".encode()).hexdigest()[:16]
        facts.append(CanonicalFactRef(
            fact_id=f"kp:{digest}", text=text, source_kind="kp_canon",
            source_ref=f"kp:{state.timeline_id}:{turn_id}",
            visibility=str(entry.get("audience", "public")), timeline_id=state.timeline_id,
        ))
    for report in state.narrative_corrections:
        if (report.get("status") != "approved" or report.get("adjudicated_by") != "kp_assistant"
                or report.get("timeline_id") != state.timeline_id):
            continue
        resolution = str(report.get("resolution", "")).strip()
        if not resolution or any(term in resolution.upper() for term in ("HP", "SAN", "MP", "LUCK", "彈藥", "骰值", "先攻")):
            continue
        correction_id = str(report.get("id", ""))
        if correction_id:
            facts.append(CanonicalFactRef(
                fact_id=f"correction:{correction_id}", text=resolution,
                source_kind="kp_canon", source_ref=f"correction:{state.timeline_id}:{correction_id}",
                visibility="public", timeline_id=state.timeline_id,
            ))
    return facts


def requirements(state: GroupState, *, recipient_id: str = "", speaker_role: str = "player", public_only: bool = False) -> NarrationRequirements:
    facts = project(state, recipient_id=recipient_id, speaker_role=speaker_role)
    if public_only:
        facts = [fact for fact in facts if fact.visibility == "public"]
    pending = tuple({"owner_id": owner_id, "check_id": row.get("check_id", "")}
                    for owner_id, row in state.pending_checks.items() if isinstance(row, dict))
    events = tuple(event for event in state.resolved_check_events[-5:]
                   if event.get("timeline_id") == state.timeline_id)
    return NarrationRequirements(
        authoritative_facts=tuple(facts), committed_events=events, pending=pending,
        allowed_evidence_refs=tuple(f.fact_id for f in facts),
        hard_constraints=tuple({"fact_id": f.fact_id, **f.constraints} for f in facts if f.constraints),
    )


def prompt_block(requirements_value: NarrationRequirements, *, max_facts: int = 12) -> str:
    """Bound prompt cost while retaining typed refs for later validation."""
    facts = requirements_value.authoritative_facts[-max_facts:]
    if not facts:
        return ""
    content = [{"fact_id": f.fact_id, "text": f.text, "source_kind": f.source_kind,
                "visibility": f.visibility, "constraints": f.constraints} for f in facts]
    omitted = len(requirements_value.authoritative_facts) - len(facts)
    return (
        "【已核對的世界事實；依收件者篩選】\n"
        "這些來自劇本來源／持久事件／真人 KP 明確更正；舊敘事、摘要與玩家聲明不能覆蓋。"
        + (f"較早的 {omitted} 筆未列出；需要時查來源，不能猜測。" if omitted else "")
        + "\n" + json.dumps(content, ensure_ascii=False)
    )


def as_payload(requirements_value: NarrationRequirements) -> dict[str, Any]:
    return asdict(requirements_value)


def validated_constraints(raw: Any, quote: str) -> dict[str, Any]:
    """Accept only an explicit adjacent source quantity; no prose inference."""
    if not isinstance(raw, dict) or set(raw) != {"entity", "unit", "quantity"}:
        return {}
    entity, unit, quantity = raw["entity"], raw["unit"], raw["quantity"]
    if not isinstance(entity, str) or not entity or not isinstance(unit, str) or not unit or type(quantity) is not int or quantity < 0:
        return {}
    counts = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    pattern = rf"(?<![\d一二兩三四五六七八九十])(?P<count>\d+|[一二兩三四五六七八九]){re.escape(unit)}\s*{re.escape(entity)}"
    for match in re.finditer(pattern, quote):
        value = match.group("count")
        if (int(value) if value.isdigit() else counts[value]) == quantity:
            return dict(raw)
    return {}
