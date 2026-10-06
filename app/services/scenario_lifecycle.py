"""Own scenario submission decisions, transitions, and commit-before-image order.

Source parsers produce library entries; this module owns what a group does with
one. The persisted ``pending_pdf_upload`` name is retained for old saves,
including Markdown submissions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from app import (
    db,
    locks,
    observability,
    scenario_activation,
    scenario_index,
    scenario_library,
    scenario_templates,
    scene_map,
)
from app.keeper_tools import resource_bridge
from app.models import GroupState
from app.repositories import manual_pregens, state_transaction
from app.repositories.group_state import load_state

_logger = logging.getLogger(__name__)


def _discard_unreferenced_staged_source(key: str) -> None:
    """Keep old content-hash files while any persisted submission refers to them."""
    for group_id in db.list_keys("group_states"):
        try:
            state = load_state(group_id)
        except Exception:  # noqa: BLE001 - fail closed if any persisted state cannot be inspected
            # An unreadable state cannot prove the source is safe to delete.
            return
        if (state.pending_scenario_upload or {}).get("key") == key:
            return
        if any(part.get("key") == key for part in state.staged_pdf_parts):
            return
    scenario_library.discard_staged_upload(key)


@dataclass(frozen=True)
class LifecycleResult:
    outcome: Literal["accepted", "activated", "pending", "raced", "stale", "missing", "rejected", "reparsed", "cancelled"]
    scenario_id: str = ""
    title: str = ""
    previous_title: str = ""
    text: str = ""
    low_text_pages: tuple[int, ...] = ()
    truncated: bool = False
    page_maps: dict[str, Any] | None = None
    indexes: dict[str, Any] | None = None
    pregen_count: int = 0
    active_chapters: tuple[str, ...] = ()
    image_refreshed: bool = True
    stale_cards: bool = False
    variant_notice: str = ""
    artifact_notice: str = ""
    reason: str = ""
    detail: str = ""


async def admit_submission(
    conversation_id: str, *, source_format: Literal["pdf", "markdown"],
    skip_similarity: bool = False, expected_revision: int | None = None,
) -> LifecycleResult:
    """Early UX admission; the authoritative transition repeats admission under lock."""
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if expected_revision is not None and state.state_revision != expected_revision:
            return LifecycleResult("stale")
        if state.pending_pregen_luck:
            return LifecycleResult("rejected", reason="pending_pregen_luck")
        if state.pending_pdf_upload is not None:
            return LifecycleResult(
                "rejected", reason="pending_choice",
                title=state.pending_pdf_upload["title"],
            )
        if state.pending_scenario_upload is not None and (source_format == "markdown" or not skip_similarity):
            return LifecycleResult("rejected", reason="similar_pending")
    return LifecycleResult("accepted")


def _new_upload(state: GroupState, context: dict[str, Any]) -> None:
    replacement_block = resource_bridge.guard_replacement(state)
    if replacement_block:
        raise ValueError(replacement_block)
    state.scenario_text = context["text"]
    state.scenario_title = context["manifest"]["title"]
    state.active = True
    old_timeline_id = state.timeline_id or f"legacy-{state.group_id}"
    new_timeline_id = f"timeline-{uuid4().hex[:8]}"
    observability.event(
        "provider.chain.reset", reason="scenario_upload",
        old_timeline_id=old_timeline_id, requested_timeline_id=new_timeline_id,
        provider="openai",
    )
    state.openai_previous_response_id = ""
    state.openai_previous_response_timeline_id = ""
    state.timeline_id = new_timeline_id
    state.resolved_check_events.clear()
    state.check_consequence_origins.clear()
    state.check_consequence_receipts.clear()
    state.pending_checks.clear()
    state.pending_luck_decisions.clear()
    state.deterministic_check_results.clear()
    state.game_started = False
    state.kp_ooc_log = []
    state.scenario_npc_index = context["indexes"]["npcs"]
    state.scenario_location_index = context["indexes"]["locations"]
    state.scene_maps = {str(k): v for k, v in context["scene_maps"].items()}
    state.current_map_page = {}
    state.current_room_id = {}
    state.party_facing = {}
    state.pregens = list(context["pregens"])


def _repair(state: GroupState, context: dict[str, Any]) -> None:
    state.scenario_title = context["manifest"]["title"]
    state.scenario_text = context["text"]
    state.scenario_npc_index = context["indexes"]["npcs"]
    state.scenario_location_index = context["indexes"]["locations"]
    scenario_index.report_location_index(
        state.scenario_location_index, source="correction", scenario_title=state.scenario_title,
    )
    from app import pregen_extractor
    for pregen in context["pregens"]:
        state.pregens, _ = pregen_extractor.reconcile_pregen_into_pool(state.pregens, pregen)


def _keep_valid_map_locations(state: GroupState, new_maps: dict[str, Any]) -> None:
    previous_locations = {
        owner_id: (state.current_map_page.get(owner_id, ""), state.current_room_id.get(owner_id, ""))
        for owner_id in set(state.current_map_page) | set(state.current_room_id)
    }
    state.scene_maps = dict(new_maps)
    state.current_map_page = {}
    state.current_room_id = {}
    for owner_id, (map_key, room_id) in previous_locations.items():
        new_map = state.scene_maps.get(map_key)
        if new_map is not None and scene_map.get_room(new_map, room_id) is not None:
            state.current_map_page[owner_id] = map_key
            state.current_room_id[owner_id] = room_id
        else:
            state.party_facing.pop(owner_id, None)


def _name_kept_map_locations(state: GroupState) -> None:
    """The library's index replaces the running one while the custom maps stay, so put their locations back."""
    state.scenario_location_index = scenario_index.merge_scene_map_locations(
        state.scenario_location_index, state.scene_maps)


def _use_existing(state: GroupState, context: dict[str, Any], scenario_id: str, variant_id: str) -> None:
    scenario_activation.install_context_fields(
        state, scenario_id, context, variant_id=variant_id, preserve_maps=True,
    )
    _name_kept_map_locations(state)
    old_timeline_id = state.timeline_id or f"legacy-{state.group_id}"
    state.timeline_id = f"timeline-{uuid4().hex[:8]}"
    state.pending_checks.clear()
    state.pending_luck_decisions.clear()
    state.deterministic_check_results.clear()
    state.resolved_check_events.clear()
    observability.event(
        "provider.chain.reset", reason="scenario_use",
        old_timeline_id=old_timeline_id, requested_timeline_id=state.timeline_id,
        provider="openai",
    )
    _keep_valid_map_locations(state, context["scene_maps"])
    state.openai_previous_response_id = ""
    state.openai_previous_response_timeline_id = ""
    state.active = True


def _commit_activation(
    state: GroupState, scenario_id: str, context: dict[str, Any], *,
    old_scenario_id: str | None, old_pool: list[dict[str, Any]], old_hash: str = "",
    claimed: list[dict[str, Any]] | None = None,
) -> tuple[bool, bool]:
    install_result: dict[str, bool] = {}

    def install_cards(conn) -> None:
        manual_pregens.capture_legacy(conn, state.group_id, old_scenario_id, old_pool, old_hash)
        state.pregens, install_result["stale"] = manual_pregens.install_pool(
            conn, state.group_id, scenario_id, context,
            bind_unassigned=(old_scenario_id is None), claimed=claimed,
        )

    _, image_refreshed = scenario_activation.commit_and_refresh(
        lambda: state_transaction.commit_snapshot(state, mutate_tx=install_cards),
        state.group_id, scenario_id, context,
    )
    return image_refreshed, install_result.get("stale", False)


def _activation_result(
    state: GroupState, scenario_id: str, context: dict[str, Any], *,
    low_text_pages: tuple[int, ...] = (), truncated: bool = False,
    image_refreshed: bool = True, stale_cards: bool = False, source: str = "pdf_upload",
    variant_notice: str = "",
) -> LifecycleResult:
    return LifecycleResult(
        "activated", scenario_id=scenario_id, title=context["manifest"]["title"],
        text=context["text"], low_text_pages=low_text_pages, truncated=truncated,
        page_maps=context["scene_maps"], indexes=context["indexes"],
        pregen_count=len(state.pregens), active_chapters=tuple(state.context_chapter_ids),
        image_refreshed=image_refreshed, stale_cards=stale_cards,
        variant_notice=variant_notice,
        artifact_notice=scenario_index.report_location_index(
            state.scenario_location_index, source=source,
            scenario_title=state.scenario_title, scene_maps=state.scene_maps,
        ),
    )


async def stage_similar_pdf(
    conversation_id: str, pdf_bytes: bytes, filename: str, title: str,
    matches: list[dict[str, Any]], *, expected_revision: int | None = None,
) -> LifecycleResult:
    key = await asyncio.to_thread(scenario_library.stage_upload, pdf_bytes)
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if expected_revision is not None and state.state_revision != expected_revision:
            _discard_unreferenced_staged_source(key)
            return LifecycleResult("stale")
        if state.pending_pregen_luck:
            _discard_unreferenced_staged_source(key)
            return LifecycleResult("rejected", reason="pending_pregen_luck")
        if state.pending_pdf_upload is not None:
            _discard_unreferenced_staged_source(key)
            return LifecycleResult("rejected", reason="pending_choice", title=state.pending_pdf_upload["title"])
        if state.pending_scenario_upload is not None:
            _discard_unreferenced_staged_source(key)
            return LifecycleResult("rejected", reason="similar_pending")
        state.pending_scenario_upload = {
            "key": key, "file_name": filename, "title": title, "matches": matches,
        }
        state_transaction.commit_snapshot(state)
    return LifecycleResult("pending", title=title)


async def submit_published_scenario(
    conversation_id: str, scenario_id: str, *, source_format: Literal["pdf", "markdown"],
    low_text_pages: list[int] | None = None,
    truncated: bool = False, expected_revision: int | None = None,
    expected_timeline: str | None = None,
) -> LifecycleResult:
    context = await asyncio.to_thread(scenario_library.load_context, scenario_id)
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if expected_timeline is not None and state.timeline_id != expected_timeline:
            return LifecycleResult("stale", reason="timeline_changed")
        if expected_revision is not None and state.state_revision != expected_revision:
            return LifecycleResult("stale")
        if state.pending_pregen_luck:
            return LifecycleResult("rejected", reason="pending_pregen_luck")
        if state.pending_pdf_upload is not None:
            return LifecycleResult("raced", title=context["manifest"]["title"])
        if state.pending_scenario_upload is not None:
            return LifecycleResult("raced", title=context["manifest"]["title"])
        if state.scenario_text.strip():
            previous_content_hash = ""
            if state.scenario_library_id:
                try:
                    previous_content_hash = scenario_library.load_context(
                        state.scenario_library_id
                    )["manifest"].get("content_hash", "")
                except (FileNotFoundError, ValueError):
                    pass
            state.pending_pdf_upload = {
                "scenario_id": scenario_id,
                "previous_content_hash": previous_content_hash,
                **({"source_format": "markdown"} if source_format == "markdown" else {}),
                "text": context["text"], "title": context["manifest"]["title"],
                "low_text_pages": low_text_pages or [], "truncated": truncated,
                "npcs": context["indexes"]["npcs"],
                "locations": context["indexes"]["locations"],
                "page_maps": ({str(k): v for k, v in context["scene_maps"].items()}
                              if source_format == "pdf" else {}),
                "pregens": context["pregens"],
                "active_chapter_id": context["active_chapter_id"],
                "context_chapter_ids": context["context_chapter_ids"],
            }
            state_transaction.commit_snapshot(state)
            return LifecycleResult(
                "pending", scenario_id=scenario_id,
                title=context["manifest"]["title"], previous_title=state.scenario_title,
            )

        old_pool = list(state.pregens)
        _new_upload(state, context)
        scenario_activation.install_context_fields(state, scenario_id, context)
        image_refreshed, stale_cards = _commit_activation(
            state, scenario_id, context, old_scenario_id=None, old_pool=old_pool,
        )
    variant_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
    return _activation_result(
        state, scenario_id, context, low_text_pages=tuple(low_text_pages or []),
        truncated=truncated, image_refreshed=image_refreshed, stale_cards=stale_cards,
        source="markdown_upload" if source_format == "markdown" else "pdf_upload",
        variant_notice=variant_notice,
    )


async def resolve_pending_submission(
    conversation_id: str, choice: Literal["new", "fix"], *,
    authorized: Callable[[GroupState], bool] | None = None,
) -> LifecycleResult:
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if authorized is not None and not authorized(state):
            return LifecycleResult("rejected", reason="unauthorized")
        pending = state.pending_pdf_upload
        if pending is None:
            return LifecycleResult("missing", reason="pending_absent")
        scenario_id = pending.get("scenario_id")
        if not scenario_id:
            return LifecycleResult("missing", reason="library_id_absent")
        try:
            context = scenario_library.load_context(scenario_id, pending.get("active_chapter_id", ""))
        except (FileNotFoundError, ValueError):
            state.pending_pdf_upload = None
            state_transaction.commit_snapshot(state)
            return LifecycleResult("missing", reason="library_missing")
        old_pool = list(state.pregens)
        old_scenario_id = state.scenario_library_id or None
        old_hash = ""
        if old_scenario_id == scenario_id:
            old_hash = pending.get("previous_content_hash", "")
        elif old_scenario_id:
            try:
                old_hash = scenario_library.load_context(old_scenario_id)["manifest"].get("content_hash", "")
            except (FileNotFoundError, ValueError):
                pass
        if choice == "new":
            _new_upload(state, context)
        else:
            _repair(state, context)
        scenario_activation.install_context_fields(
            state, scenario_id, context, preserve_maps=(choice != "new"),
            preserve_pregens=(choice != "new"),
        )
        if choice != "new":
            _name_kept_map_locations(state)
        state.pending_pdf_upload = None
        claimed = [p for p in old_pool if p.get("claimed_by")] if choice != "new" else []
        image_refreshed, stale_cards = _commit_activation(
            state, scenario_id, context, old_scenario_id=old_scenario_id,
            old_pool=old_pool, old_hash=old_hash, claimed=claimed,
        )
    variant_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
    scenario_templates.schedule_index_prewarm(state)
    return _activation_result(
        state, scenario_id, context,
        low_text_pages=tuple(pending["low_text_pages"]), truncated=pending["truncated"],
        image_refreshed=image_refreshed, stale_cards=stale_cards,
        source="pdf_upload", variant_notice=variant_notice,
    )


async def reparse_pending_scenario(
    conversation_id: str, submit_pdf: Callable[..., Awaitable[bool]], reply: Callable[[str], Awaitable[None]],
    push: Callable[[str], Awaitable[None]], *,
    authorized: Callable[[GroupState], bool], expected_revision: int | None = None,
) -> LifecycleResult:
    """Claim under lock, parse unlocked, then conditionally consume or restore.

    ``submit_pdf`` is the PDF source adapter. It performs the expensive parse
    and calls ``submit_published_scenario`` for the authoritative transition.
    The lifecycle retains ownership of the staged reference across its wait.
    """
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if expected_revision is not None and state.state_revision != expected_revision:
            return LifecycleResult("stale", reason="revision_changed")
        if not authorized(state):
            return LifecycleResult("rejected", reason="unauthorized")
        if state.pending_pregen_luck:
            return LifecycleResult("rejected", reason="pending_pregen_luck")
        replacement_block = resource_bridge.guard_replacement(state)
        if replacement_block:
            return LifecycleResult("rejected", reason="combat_unsettled", title=replacement_block)
        pending = state.pending_scenario_upload
        if pending is None:
            return LifecycleResult("missing", reason="pending_absent")
        try:
            pdf_bytes = scenario_library.read_staged_upload(pending["key"])
        except FileNotFoundError:
            state.pending_scenario_upload = None
            state_transaction.commit_snapshot(state)
            return LifecycleResult("missing", reason="staged_missing")
        state.pending_scenario_upload = None
        state_transaction.commit_snapshot(state)
        claimed_revision = state.state_revision
        claimed_timeline = state.timeline_id
    matches = pending.get("matches") or []
    candidate_id = matches[0]["id"] if matches else None
    accepted = False
    try:
        accepted = await submit_pdf(
            conversation_id, reply, push, pdf_bytes, pending["file_name"],
            skip_similarity=True, reparse_candidate_id=candidate_id,
            expected_revision=claimed_revision,
            expected_timeline=claimed_timeline,
        )
    finally:
        if accepted:
            try:
                _discard_unreferenced_staged_source(pending["key"])
            except Exception:
                _logger.exception("scenario_reparse_source_discard_failed group_id=%s", conversation_id)
        else:
            async with locks.get_conversation_lock(conversation_id):
                latest = load_state(conversation_id)
                if (
                    latest.state_revision == claimed_revision
                    and latest.timeline_id == claimed_timeline
                    and latest.pending_scenario_upload is None
                    and latest.pending_pdf_upload is None
                ):
                    latest.pending_scenario_upload = pending
                    state_transaction.commit_snapshot(latest)
    return LifecycleResult("reparsed" if accepted else "rejected", reason="submission_failed" if not accepted else "")


async def cancel_pending_reparse(
    conversation_id: str, *, authorized: Callable[[GroupState], bool],
) -> LifecycleResult:
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if not authorized(state):
            return LifecycleResult("rejected", reason="unauthorized")
        pending = state.pending_scenario_upload
        if pending is None:
            return LifecycleResult("missing", reason="pending_absent")
        state.pending_scenario_upload = None
        state_transaction.commit_snapshot(state)
        try:
            _discard_unreferenced_staged_source(pending.get("key", ""))
        except Exception:
            _logger.exception("scenario_reparse_cancel_discard_failed group_id=%s", conversation_id)
    return LifecycleResult("cancelled")


async def activate_existing_scenario(
    conversation_id: str, scenario_id: str, *,
    authorized: Callable[[GroupState], bool], variant_id: str | None = None,
    expected_revision: int | None = None,
) -> LifecycleResult:
    """Select a library scenario with its distinct USE_EXISTING reset policy."""
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if expected_revision is not None and state.state_revision != expected_revision:
            return LifecycleResult("stale", reason="revision_changed")
        if not authorized(state):
            return LifecycleResult("rejected", reason="unauthorized")
        replacement_block = resource_bridge.guard_replacement(state)
        if replacement_block:
            return LifecycleResult("rejected", reason="combat_unsettled", detail=replacement_block)
        if state.pending_pregen_luck:
            return LifecycleResult("rejected", reason="pending_pregen_luck")
        if state.pending_pdf_upload is not None or state.pending_scenario_upload is not None:
            return LifecycleResult("rejected", reason="pending_submission")
        if not scenario_id:
            return LifecycleResult("missing", reason="identifier_missing")
        try:
            context = scenario_library.load_context(scenario_id)
        except (FileNotFoundError, ValueError):
            return LifecycleResult("missing", reason="library_missing")
        preference_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
        selected_variant = variant_id if variant_id is not None else scenario_templates.preferred_variant(
            conversation_id, scenario_id,
        )
        try:
            if selected_variant != "original":
                scenario_templates.require_approved(scenario_id, selected_variant)
        except (FileNotFoundError, ValueError) as exc:
            return LifecycleResult("rejected", reason="variant_invalid", detail=str(exc))
        old_pool = list(state.pregens)
        old_scenario_id = state.scenario_library_id or None
        old_hash = ""
        if old_scenario_id:
            try:
                old_hash = scenario_library.load_context(old_scenario_id)["manifest"].get("content_hash", "")
            except (FileNotFoundError, ValueError):
                pass
        _use_existing(state, context, scenario_id, selected_variant)
        artifact_notice = scenario_index.report_location_index(
            state.scenario_location_index, source="scenario_use",
            scenario_title=state.scenario_title, scene_maps=state.scene_maps,
        )
        image_refreshed, stale_cards = _commit_activation(
            state, scenario_id, context, old_scenario_id=old_scenario_id,
            old_pool=old_pool, old_hash=old_hash,
        )
        if variant_id is not None:
            scenario_templates.select_variant(conversation_id, scenario_id, selected_variant)
        scenario_templates.schedule_index_prewarm(state)
    return LifecycleResult(
        "activated", scenario_id=scenario_id, title=state.scenario_title,
        active_chapters=tuple(state.context_chapter_ids), image_refreshed=image_refreshed,
        stale_cards=stale_cards, artifact_notice=artifact_notice,
        variant_notice=preference_notice if variant_id is None else "",
    )


async def submit_merged_pdf(
    conversation_id: str, merged_bytes: bytes, filename: str,
    staged_refs: tuple[tuple[str, str], ...],
    submit_pdf: Callable[..., Awaitable[bool]], reply: Callable[[str], Awaitable[None]],
    push: Callable[[str], Awaitable[None]], *, expected_revision: int | None = None,
) -> bool:
    """Consume staged parts only after the merged submission is persisted."""
    accepted = await submit_pdf(
        conversation_id, reply, push, merged_bytes, filename,
        expected_revision=expected_revision,
    )
    if not accepted:
        return False
    try:
        async with locks.get_conversation_lock(conversation_id):
            state = load_state(conversation_id)
            state.staged_pdf_parts = [
                part for part in state.staged_pdf_parts
                if (part["key"], part["file_name"]) not in staged_refs
            ]
            state_transaction.commit_snapshot(state)
    except Exception:
        # The scenario submission has already committed. Keep staging
        # references and bytes retryable, and do not report that activation failed.
        _logger.exception("scenario_multipart_cleanup_commit_failed group_id=%s", conversation_id)
        return True
    for key, _filename in staged_refs:
        try:
            _discard_unreferenced_staged_source(key)
        except Exception:
            # State is committed; a retained unreferenced file is recoverable.
            _logger.exception("scenario_multipart_source_discard_failed group_id=%s", conversation_id)
    return True
