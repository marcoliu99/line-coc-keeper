"""Validate model candidates; only Python assigns audience and canonical policy."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from app.agents.intent_router import RouteDecision

UNRESOLVED = '場外說明尚未完整處理；已結算的行動與待處理選擇仍保留。'


@dataclass(frozen=True)
class SegmentProjection:
    canonical: str
    public_ooc: str
    private: tuple[tuple[str, str], ...]
    valid: bool


def prompt(route: RouteDecision) -> str:
    spans = [asdict(s) for s in route.spans if s.audience == 'public']
    return ('Return JSON only: {"schema_version":1,"segments":[{"text":"...",'
            '"source_request_span_refs":["span:0"],"proposed_mode":"IC",'
            '"proposed_event_refs":[]}]}. Cover every supplied span exactly once. '
            'Keep IC and OOC in separate segments; OOC statements never establish world facts. '
            'No role, audience, recipient or canonical fields. Do not invent event refs. '
            'Narrate only supported actions. Input spans:\n' + json.dumps(spans, ensure_ascii=False))


def project(raw: str, route: RouteDecision, user_id: str, events: set[str]) -> SegmentProjection:
    invalid = SegmentProjection('', UNRESOLVED, (), False)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'schema_version', 'segments'} or data['schema_version'] != 1:
            return invalid
        segments = data['segments']
        if not isinstance(segments, list) or len(segments) > 32:
            return invalid
        spans = {s.ref: s for s in route.spans if s.audience == 'public'}
        seen: set[str] = set()
        canonical: list[str] = []
        ooc: list[str] = []
        for segment in segments:
            if not isinstance(segment, dict) or set(segment) != {
                'text', 'source_request_span_refs', 'proposed_mode', 'proposed_event_refs'
            }:
                return invalid
            refs, erefs = segment['source_request_span_refs'], segment['proposed_event_refs']
            if (not isinstance(segment['text'], str) or not segment['text'].strip()
                    or len(segment['text']) > 12000 or not isinstance(refs, list) or not refs
                    or not all(isinstance(r, str) and r in spans and r not in seen for r in refs)
                    or len(set(refs)) != len(refs) or not isinstance(erefs, list)
                    or not all(isinstance(r, str) and r in events for r in erefs)):
                return invalid
            modes = {spans[r].mode for r in refs}
            if modes != {segment['proposed_mode']}:
                return invalid
            seen.update(refs)
            (canonical if modes == {'IC'} else ooc).append(segment['text'])
        if seen != set(spans):
            return invalid
        return SegmentProjection('\n\n'.join(canonical), '\n\n'.join(ooc), (), True)
    except (ValueError, TypeError, KeyError):
        return invalid


def public_context(state) -> str:
    # Explicit public game records, not raw scenario, private sheets, campaign
    # summary (legacy provenance unknown), or memory. Model never sees secrets.
    facts: list[str] = []
    for collection in (state.established_facts, state.known_clues):
        facts.extend(str(r.get('text', '')) for r in collection
                     if isinstance(r, dict) and r.get('visibility') == 'public')
    return '\n'.join(facts[-30:])


def self_context(state, owner: str) -> str:
    char = state.get_active_character(owner)
    if char is None:
        return '目前沒有有效角色。'
    text = char.sheet_text()
    if char.skills:
        text += "\n全部技能：" + "、".join(f"{name} {value}%" for name, value in char.skills.items())
    if char.secret_goal and char.secret_goal not in text:
        text += "\n你的秘密目標：" + char.secret_goal
    return text


def audit(route: RouteDecision, text: str, user_id: str) -> dict:
    return {"recipient_id": user_id, "input": text[:16000],
            "input_truncated": len(text) > 16000, "mode": route.message_mode,
            "spans": [asdict(s) for s in route.spans if s.end <= 16000]}
