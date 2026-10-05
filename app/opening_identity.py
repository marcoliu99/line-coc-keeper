"""Stable opening context and participant projections for stale-work guards."""

from __future__ import annotations

import hashlib

from app.models import GroupState

OpeningContext = tuple[str, str, str, tuple[str, ...], str]
OpeningParticipants = tuple[tuple[str, str, str, bool, str], ...]


def context_identity(state: GroupState) -> OpeningContext:
    """Bind the exact extraction input as well as its active chapter metadata."""
    return (
        state.scenario_library_id,
        state.scenario_variant_id,
        state.active_chapter_id,
        tuple(state.context_chapter_ids),
        hashlib.sha256(state.scenario_text.encode("utf-8")).hexdigest(),
    )


def participant_identity(state: GroupState) -> OpeningParticipants:
    """Track group-check membership/bindings, excluding unrelated character values."""
    return tuple(sorted(
        (owner, char.character_id or f"legacy-user:{owner}", char.owner_id,
         char.active, state.active_character_id_by_user.get(owner, ""))
        for owner, char in state.characters.items()
    ))
