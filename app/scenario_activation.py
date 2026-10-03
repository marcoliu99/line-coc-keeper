"""Commit scenario transitions before publishing their derived page images."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from app import scenario_library, scenario_templates
from app.models import GroupState
from app.repositories import group_state

_logger = logging.getLogger(__name__)
_T = TypeVar("_T")


def install_context_fields(
    state: GroupState, scenario_id: str, context: dict[str, Any], *,
    variant_id: str | None = None, preserve_maps: bool = False,
    preserve_pregens: bool = False,
) -> None:
    """Install a library chapter while leaving transition policy to the caller."""
    state.scenario_library_id = scenario_id
    state.scenario_library_revision = context.get("library_revision", "")
    state.scenario_variant_id = (
        variant_id if variant_id is not None
        else scenario_templates.preferred_variant(state.group_id, scenario_id)
    )
    state.scenario_title = context["manifest"]["title"]
    state.scenario_text = context["text"]
    state.active_chapter_id = context["active_chapter_id"]
    state.context_chapter_ids = context["context_chapter_ids"]
    state.scenario_npc_index = context["indexes"].get("npcs", [])
    state.scenario_location_index = context["indexes"].get("locations", [])
    if not preserve_pregens:
        state.pregens = list(context.get("pregens", []))
    if not preserve_maps:
        state.scene_maps = context["scene_maps"]
    else:
        # Only an explicitly source-incompatible reparse artifact is withdrawn;
        # compatible maps and positions outside this chapter remain unchanged.
        disabled = set(context.get('quarantined_map_pages', []))
        for page in disabled:
            state.scene_maps.pop(page, None)
        for owner, page in list(state.current_map_page.items()):
            if page in disabled:
                state.current_map_page.pop(owner, None)
                state.current_room_id.pop(owner, None)
                state.party_facing.pop(owner, None)


def refresh_context_images(group_id: str, scenario_id: str, context: dict[str, Any]) -> None:
    """Invalidate old pages before reading images for the committed scenario."""
    group_state.clear_page_images(group_id)
    images: dict[int, bytes] = {}
    options = {"revision": context["library_revision"]} if context.get("library_revision") else {}
    scenario_library.copy_context_images(
        scenario_id, context["page_numbers"],
        lambda page, image: images.__setitem__(page, image), **options,
    )
    for page, image in images.items():
        group_state.save_page_image(group_id, page, image)


def commit_and_refresh(
    commit: Callable[[], _T], group_id: str, scenario_id: str,
    context: dict[str, Any],
) -> tuple[_T, bool]:
    """Never publish images before the caller's authoritative SQLite commit."""
    value = commit()
    return value, refresh_after_commit(group_id, scenario_id, context)


def refresh_after_commit(group_id: str, scenario_id: str, context: dict[str, Any]) -> bool:
    """Publish after a commit that the caller has already completed."""
    try:
        refresh_context_images(group_id, scenario_id, context)
    except Exception:
        _logger.exception("scenario_image_refresh_failed group_id=%s scenario_id=%s", group_id, scenario_id)
        return False
    return True


def refresh_restored_images(state: GroupState) -> bool:
    """Rebuild a rollback's image cache after its SQLite transaction commits."""
    if not state.scenario_library_id:
        group_state.clear_page_images(state.group_id)
        return True
    try:
        context = load_state_context(state)
    except FileNotFoundError:
        group_state.clear_page_images(state.group_id)
        _logger.warning("rollback_image_restore_skipped group_id=%s scenario_id=%s reason=library_missing",
                        state.group_id, state.scenario_library_id)
        return True
    return refresh_after_commit(state.group_id, state.scenario_library_id, context)


def load_state_context(state: GroupState, chapter_id: str | None = None) -> dict[str, Any]:
    """Read the group's pinned source; only explicit activation selects latest."""
    revision = revision_for_state(state)
    options = {"revision": revision} if revision else {}
    return scenario_library.load_context(state.scenario_library_id,
        state.active_chapter_id if chapter_id is None else chapter_id, **options)


def revision_for_state(state: GroupState) -> str:
    """Keep legacy snapshots readable and bind exact retained source when available."""
    revision = getattr(state, 'scenario_library_revision', '')
    if not revision and state.scenario_library_id and getattr(state, 'scenario_text', '').strip():
        revision = scenario_library.matching_revision(state.scenario_library_id,
            getattr(state, 'active_chapter_id', ''), state.scenario_text, getattr(state, 'scene_maps', {}))
    return revision
