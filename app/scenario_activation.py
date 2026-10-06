"""Commit scenario transitions before publishing their derived page images."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from app import db, scenario_library, scenario_templates
from app.models import GroupState
from app.repositories import group_state, page_repairs

_logger = logging.getLogger(__name__)
_T = TypeVar("_T")


def install_context_fields(
    state: GroupState, scenario_id: str, context: dict[str, Any], *,
    variant_id: str | None = None, preserve_maps: bool = False,
    preserve_pregens: bool = False,
) -> None:
    """Install a library chapter while leaving transition policy to the caller."""
    state.scenario_library_id = scenario_id
    state.scenario_variant_id = (
        variant_id if variant_id is not None
        else scenario_templates.preferred_variant(state.group_id, scenario_id)
    )
    state.scenario_title = context["manifest"]["title"]
    source_hash = context["manifest"].get("content_hash", "")
    state.scenario_text = page_repairs.apply_saved(state.group_id, scenario_id, source_hash, context["text"])
    state.active_scenario_source_hash = source_hash
    state.active_chapter_id = context["active_chapter_id"]
    state.context_chapter_ids = context["context_chapter_ids"]
    state.scenario_npc_index = context["indexes"].get("npcs", [])
    state.scenario_location_index = context["indexes"].get("locations", [])
    if not preserve_pregens:
        state.pregens = list(context.get("pregens", []))
    if not preserve_maps:
        state.scene_maps = context["scene_maps"]


def refresh_context_images(
    group_id: str, scenario_id: str, context: dict[str, Any], *,
    expected_revision: int | None = None,
) -> None:
    """Publish only while the committed scenario still owns the image cache."""
    committed = group_state.load_state(group_id)
    if committed.scenario_library_id != scenario_id:
        return
    revision = committed.state_revision if expected_revision is None else expected_revision
    timeline = committed.timeline_id
    chapter = context.get("active_chapter_id")

    def still_current(conn: Any, images: dict[int, bytes], *, allow_revision_drift: bool) -> bool:
        row = conn.execute("SELECT data FROM group_states WHERE key = ?", (group_id,)).fetchone()
        if row is None:
            return False
        current = json.loads(row[0])
        if not (
            current.get("scenario_library_id") == scenario_id
            and current.get("timeline_id", "") == timeline
            and (chapter is None or current.get("active_chapter_id") == chapter)
        ):
            return False
        if current.get("state_revision", 0) == revision:
            return True
        # A persona/check write may advance the revision while image copying
        # runs. Publish after it only if the active text and source image bytes
        # are still identical; this also rules out an in-place library reparse
        # with the same scenario ID and timeline.
        if not allow_revision_drift or current.get("scenario_text") != context.get("text"):
            return False
        latest_images: dict[int, bytes] = {}
        try:
            scenario_library.copy_context_images(
                scenario_id, context["page_numbers"],
                lambda page, image: latest_images.__setitem__(page, image),
            )
        except (FileNotFoundError, OSError):
            return False
        return latest_images == images

    def publish(images: dict[int, bytes], *, allow_revision_drift: bool = True) -> None:
        # SQLite serializes this cache publication with every authoritative
        # state commit, including commits made by other gateway processes.
        with db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not still_current(conn, images, allow_revision_drift=allow_revision_drift):
                return
            group_state.clear_page_images(group_id)
            for page, image in images.items():
                group_state.save_page_image(group_id, page, image)

    images: dict[int, bytes] = {}
    try:
        scenario_library.copy_context_images(
            scenario_id, context["page_numbers"],
            lambda page, image: images.__setitem__(page, image),
        )
    except Exception:
        # Preserve the existing clear-on-copy-failure behavior only if this
        # is still the active scenario. Never clear a newer scenario's cache.
        publish({}, allow_revision_drift=False)
        raise
    publish(images)


def commit_and_refresh(
    commit: Callable[[], _T], group_id: str, scenario_id: str,
    context: dict[str, Any],
) -> tuple[_T, bool]:
    """Never publish images before the caller's authoritative SQLite commit."""
    value = commit()
    revision = getattr(value, "revision", None)
    return value, refresh_after_commit(group_id, scenario_id, context, expected_revision=revision)


def refresh_after_commit(
    group_id: str, scenario_id: str, context: dict[str, Any], *,
    expected_revision: int | None = None,
) -> bool:
    """Publish after a commit that the caller has already completed."""
    try:
        refresh_context_images(group_id, scenario_id, context, expected_revision=expected_revision)
    except Exception:
        _logger.exception("scenario_image_refresh_failed group_id=%s scenario_id=%s", group_id, scenario_id)
        return False
    return True


def refresh_restored_images(state: GroupState) -> bool:
    """Rebuild a rollback's image cache after its SQLite transaction commits."""
    if not state.scenario_library_id:
        _clear_restored_images_if_current(state)
        return True
    try:
        context = scenario_library.load_context(state.scenario_library_id, state.active_chapter_id)
    except FileNotFoundError:
        _clear_restored_images_if_current(state)
        _logger.warning("rollback_image_restore_skipped group_id=%s scenario_id=%s reason=library_missing",
                        state.group_id, state.scenario_library_id)
        return True
    return refresh_after_commit(
        state.group_id, state.scenario_library_id, context,
        expected_revision=state.state_revision,
    )


def _clear_restored_images_if_current(state: GroupState) -> None:
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM group_states WHERE key = ?", (state.group_id,)).fetchone()
        if row is None:
            return
        current = json.loads(row[0])
        if (
            current.get("state_revision", 0) == state.state_revision
            and current.get("timeline_id", "") == state.timeline_id
            and current.get("scenario_library_id", "") == state.scenario_library_id
        ):
            group_state.clear_page_images(state.group_id)
