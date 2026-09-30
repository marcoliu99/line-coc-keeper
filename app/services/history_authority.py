"""Provenance for conversation history; prose is never a world-state receipt."""
from __future__ import annotations

from typing import Any, Literal, cast

RecordKind = Literal["player_claim", "player_correction", "correction_request", "kp_canon", "kp_workflow", "narrative", "narrative_correction", "opening_instruction", "legacy_mixed"]
AuthorityLevel = Literal["authoritative", "presentation", "claim", "mixed"]
_RECORD_KINDS = frozenset({"player_claim", "player_correction", "correction_request", "kp_canon", "kp_workflow", "narrative", "narrative_correction", "opening_instruction", "legacy_mixed"})
_AUTHORITY_LEVELS = frozenset({"authoritative", "presentation", "claim", "mixed"})


def annotate_entry(
    entry: dict[str, Any], *, turn_id: str, timeline_id: str,
    record_kind: RecordKind | None = None, authority: AuthorityLevel | None = None,
) -> dict[str, Any]:
    """Stamp a new log entry without promoting its content to world authority."""
    role = entry.get("role")
    kind = record_kind or entry.get("record_kind") or ("player_claim" if role == "user" else "narrative")
    level = authority or entry.get("authority") or ("claim" if role == "user" else "presentation")
    if kind not in _RECORD_KINDS or level not in _AUTHORITY_LEVELS:
        raise ValueError("Unknown history provenance")
    return {
        **entry,
        "record_kind": kind,
        "authority": level,
        "turn_id": turn_id,
        "timeline_id": timeline_id,
        "audience": entry.get("audience", "public"),
    }


def _kind(entry: dict[str, Any]) -> RecordKind:
    kind = entry.get("record_kind")
    return cast(RecordKind, kind) if isinstance(kind, str) and kind in _RECORD_KINDS else "legacy_mixed"


def provider_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Only send SDK-valid role/content pairs, with compact authority labels."""
    labels = {
        "player_claim": "claim",
        "player_correction": "correction request",
        "correction_request": "correction request; not world evidence",
        "kp_canon": "explicit human KP canon",
        "kp_workflow": "KP request; only committed tool results are authoritative",
        "narrative": "prior narration, not independent world evidence",
        "narrative_correction": "corrected presentation",
        "opening_instruction": "system opening request",
        "legacy_mixed": "unverified earlier conversation",
    }
    return [
        {
            "role": str(entry.get("role", "user")),
            "content": f"[{labels.get(_kind(entry), 'unverified conversation')}] {entry.get('content', '')}",
        }
        for entry in history
    ]


def summary_input(history: list[dict[str, Any]]) -> str:
    """Separate explicit KP input from presentation and player claims."""
    sections: dict[str, list[str]] = {
        "Explicit human KP canon (verify timeline and scope)": [],
        "Delivered narration and earlier conversation (presentation only)": [],
        "Player claims and correction requests (not established facts)": [],
    }
    for entry in history:
        content = str(entry.get("content", ""))
        kind = _kind(entry)
        if kind == "kp_canon":
            name = "Explicit human KP canon (verify timeline and scope)"
        elif kind in {"player_claim", "player_correction", "correction_request"}:
            name = "Player claims and correction requests (not established facts)"
        else:
            name = "Delivered narration and earlier conversation (presentation only)"
        superseded = entry.get("superseded_by")
        marker = f" [superseded by {', '.join(superseded)}]" if isinstance(superseded, list) and superseded else ""
        sections[name].append(f"{entry.get('role', 'unknown')}{marker}: {content}")
    return "\n\n".join(f"[{name}]\n" + "\n".join(items or ["(none)"]) for name, items in sections.items())


def memory_source_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Compact metadata kept with a trimmed conversation chunk."""
    return [
        {
            "record_kind": _kind(entry),
            "authority": str(entry.get("authority", "mixed")),
            "turn_id": str(entry.get("turn_id", "")),
            "timeline_id": str(entry.get("timeline_id", "")),
        }
        for entry in history
    ]
