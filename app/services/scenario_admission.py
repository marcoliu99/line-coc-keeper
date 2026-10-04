"""Admitting a library scenario into a conversation, and the state transition that goes with it.

A scenario reaches a game by four doors: a PDF upload, a Markdown upload, the
"new scenario / correction" choice that follows an upload while a game is running, and
``/coc scenario use``. They all end the same way: install the library chapter into the
state, retire the previous scenario's cards, install the new pool, commit, and only then
publish page images. This module owns that sequence so a door supplies the prepared
library entry and its own wording and nothing else.

``scenario_activation`` stays the small layer underneath (copy fields, publish images
after a commit); ``scenario_ingestion`` and the ``/coc scenario`` handler are the doors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from app import (
    locks,
    observability,
    pregen_extractor,
    scenario_activation,
    scenario_index,
    scenario_library,
    scene_map,
)
from app.keeper_tools import resource_bridge
from app.models import GroupState
from app.repositories import manual_pregens, state_transaction
from app.repositories.group_state import load_state

IMAGE_REFRESH_FAILED = "\n頁面圖片快取刷新失敗；劇本已啟用，請聯絡 KP 檢查圖片。"
CARDS_RESOURCED = "\n舊版合併角色卡的劇本來源已變更；請重新匯入原始 role_ 卡。"

PendingBlock = Literal["pregen_luck", "upload_choice", "similar_upload"]
Choice = Literal["new", "fix"]


@dataclass(frozen=True)
class Activation:
    """What committing a scenario left for the caller to tell the players."""

    image_refreshed: bool
    cards_stale: bool

    @property
    def image_note(self) -> str:
        return "" if self.image_refreshed else IMAGE_REFRESH_FAILED

    @property
    def card_note(self) -> str:
        return CARDS_RESOURCED if self.cards_stale else ""


@dataclass(frozen=True)
class Admission:
    """The outcome of admitting an uploaded scenario under the conversation lock."""

    status: Literal["stale_revision", "raced", "needs_choice", "activated"]
    state: GroupState
    activation: Activation | None = None


def content_hash(scenario_id: str | None) -> str:
    """The stored content hash of a library entry, or ``""`` when there is none to read."""
    if not scenario_id:
        return ""
    try:
        return scenario_library.load_context(scenario_id)["manifest"].get("content_hash", "")
    except (FileNotFoundError, ValueError):
        return ""


def pending_block(state: GroupState, *, include_similar: bool = True) -> PendingBlock | None:
    """Why a scenario cannot be admitted right now, in the order the doors check it."""
    if state.pending_pregen_luck:
        return "pregen_luck"
    if state.pending_pdf_upload is not None:
        return "upload_choice"
    if include_similar and state.pending_scenario_upload is not None:
        return "similar_upload"
    return None


# ---- how a scenario changes the state -----------------------------------------------------


def merge_extracted_pregens(state: GroupState, pregens: list[dict]) -> None:
    """Reconcile a scenario's own cast into ``state.pregens`` without regard to upload order.

    A role card uploaded before or after the scenario ends up merged with, or kept alongside,
    the embedded pregens (never a blind overwrite, never discarded). Only ``/coc newgame``
    wipes the pool.
    """
    for pregen in pregens:
        state.pregens, _ = pregen_extractor.reconcile_pregen_into_pool(state.pregens, pregen)


def apply_new_scenario(
    state: GroupState,
    text: str,
    title: str,
    extracted_index: dict[str, list],
    page_maps: dict,
    pregens: list[dict],
) -> None:
    """"全新劇本": also what a conversation's very first upload does, since there is no
    position to protect yet in that case either way."""
    replacement_block = resource_bridge.guard_replacement(state)
    if replacement_block:
        raise ValueError(replacement_block)
    state.scenario_text = text
    state.scenario_title = title
    state.active = True
    old_timeline_id = state.timeline_id or f"legacy-{state.group_id}"
    new_timeline_id = f"timeline-{uuid4().hex[:8]}"
    observability.event(
        "provider.chain.reset",
        reason="scenario_upload",
        old_timeline_id=old_timeline_id,
        requested_timeline_id=new_timeline_id,
        provider="openai",
    )
    state.openai_previous_response_id = ""
    state.openai_previous_response_timeline_id = ""
    state.timeline_id = new_timeline_id
    state.resolved_check_events.clear()
    state.check_consequence_origins.clear()
    state.check_consequence_receipts.clear()
    # Pending player decisions and deterministic check results are scoped to the old
    # scenario. Invalidate them together with the timeline so stale typed commands or
    # Discord buttons cannot mutate the new scenario.
    state.pending_checks.clear()
    state.pending_luck_decisions.clear()
    state.deterministic_check_results.clear()
    state.game_started = False  # a new scenario has not had its own /coc start opening yet
    state.kp_ooc_log = []  # a new scenario must not inherit the previous scenario's KP OOC memory
    state.scenario_npc_index = extracted_index["npcs"]
    state.scenario_location_index = extracted_index["locations"]
    state.scene_maps = {str(k): v for k, v in page_maps.items()}  # not the old floor plans either
    state.current_map_page = {}
    state.current_room_id = {}
    state.party_facing = {}
    # Replace the scenario-owned candidate pool. Live investigators remain in
    # state.characters, but unclaimed candidates from the previous scenario must not leak
    # into /coc pregens.
    state.pregens = list(pregens)


def apply_scenario_correction(
    state: GroupState, text: str, title: str, extracted_index: dict[str, list], pregens: list[dict],
) -> None:
    """"修正目前劇本": update the scenario's text and index, leave the party's position alone.

    scene_maps, current_map_page, current_room_id, party_facing, the provider chain and
    game_started stay untouched: that is what protecting the party's position and progress
    means when the scenario has not restarted. The cast is reconciled in
    (``merge_extracted_pregens``), not wiped, so a claimed pregen keeps its ``claimed_by``.
    """
    state.scenario_title = title
    state.scenario_text = text
    state.scenario_npc_index = extracted_index["npcs"]
    state.scenario_location_index = extracted_index["locations"]
    scenario_index.report_location_index(
        state.scenario_location_index, source="correction", scenario_title=title)
    merge_extracted_pregens(state, pregens)


def install_library_context(
    state: GroupState, scenario_id: str, context: dict, *,
    preserve_maps: bool = False, preserve_pregens: bool = False,
) -> None:
    """Copy the selected chapter window from an immutable library entry into the state."""
    scenario_activation.install_context_fields(
        state, scenario_id, context, preserve_maps=preserve_maps, preserve_pregens=preserve_pregens,
    )


def replace_scene_maps_preserving_locations(state: GroupState, new_maps: dict) -> None:
    """Swap the floor plans, keeping each investigator where the new map still has their room."""
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


def apply_scenario_use(
    state: GroupState, scenario_id: str, context: dict, *, variant_id: str,
) -> str:
    """``/coc scenario use``: a new campaign context that keeps the live investigators.

    Returns the notice about artifacts the stored library entry lacks (no floor plan, no
    location index), which the upload flow reports elsewhere and this door must report itself.
    """
    scenario_activation.install_context_fields(
        state, scenario_id, context, variant_id=variant_id, preserve_maps=True,
    )
    # Old maintenance, memory and provider results must not bleed into this scenario.
    old_timeline_id = state.timeline_id or f"legacy-{state.group_id}"
    state.timeline_id = f"timeline-{uuid4().hex[:8]}"
    # All player decisions and deterministic-result caches belong to the previous timeline;
    # clear them here so an old Discord button or typed command cannot be consumed by the
    # newly selected scenario.
    state.pending_checks.clear()
    state.pending_luck_decisions.clear()
    state.deterministic_check_results.clear()
    state.resolved_check_events.clear()
    observability.event(
        "provider.chain.reset",
        reason="scenario_use",
        old_timeline_id=old_timeline_id,
        requested_timeline_id=state.timeline_id,
        provider="openai",
    )
    replace_scene_maps_preserving_locations(state, context["scene_maps"])
    artifact_notice = scenario_index.report_location_index(
        state.scenario_location_index, source="scenario_use",
        scenario_title=state.scenario_title, scene_maps=state.scene_maps)
    state.openai_previous_response_id = ""
    state.openai_previous_response_timeline_id = ""
    state.active = True
    return artifact_notice


# ---- committing it ------------------------------------------------------------------------


def commit_activation(
    conversation_id: str, state: GroupState, scenario_id: str, context: dict, *,
    old_pool: list[dict], old_scenario_id: str | None, old_hash: str = "",
    claimed: list[dict] | None = None,
) -> Activation:
    """Retire the old cards, install the new pool, commit, then publish the page images."""
    stale = {"cards": False}

    def install(conn: Any) -> None:
        manual_pregens.capture_legacy(conn, conversation_id, old_scenario_id, old_pool, old_hash)
        state.pregens, stale["cards"] = manual_pregens.install_pool(
            conn, conversation_id, scenario_id, context,
            bind_unassigned=(old_scenario_id is None), claimed=claimed,
        )

    _, image_refreshed = scenario_activation.commit_and_refresh(
        lambda: state_transaction.commit_snapshot(state, mutate_tx=install),
        conversation_id, scenario_id, context,
    )
    return Activation(image_refreshed, stale["cards"])


def activate_selected(
    conversation_id: str, state: GroupState, scenario_id: str, context: dict, *, variant_id: str,
) -> tuple[str, Activation]:
    """``/coc scenario use`` end to end. Returns the artifact notice and the activation."""
    old_pool = list(state.pregens)
    old_scenario_id = state.scenario_library_id or None
    old_hash = content_hash(old_scenario_id)
    artifact_notice = apply_scenario_use(state, scenario_id, context, variant_id=variant_id)
    activation = commit_activation(
        conversation_id, state, scenario_id, context,
        old_pool=old_pool, old_scenario_id=old_scenario_id, old_hash=old_hash,
    )
    return artifact_notice, activation


def activate_pending_choice(
    conversation_id: str, state: GroupState, pending: dict, context: dict, choice: Choice,
) -> Activation:
    """Resolve a stashed upload as a new scenario or as a correction of the running one."""
    scenario_id = pending["scenario_id"]
    old_pool = list(state.pregens)
    old_scenario_id = state.scenario_library_id or None
    if old_scenario_id == scenario_id:
        old_hash = pending.get("previous_content_hash", "")
    else:
        old_hash = content_hash(old_scenario_id)
    extracted_index = context["indexes"]
    if choice == "new":
        apply_new_scenario(
            state, context["text"], context["manifest"]["title"], extracted_index,
            context["scene_maps"], context["pregens"],
        )
    else:
        apply_scenario_correction(
            state, context["text"], context["manifest"]["title"], extracted_index, context["pregens"],
        )
    install_library_context(
        state, scenario_id, context,
        preserve_maps=(choice != "new"), preserve_pregens=(choice != "new"),
    )
    state.pending_pdf_upload = None
    claimed = [p for p in old_pool if p.get("claimed_by")] if choice != "new" else []
    return commit_activation(
        conversation_id, state, scenario_id, context,
        old_pool=old_pool, old_scenario_id=old_scenario_id, old_hash=old_hash, claimed=claimed,
    )


async def admit_upload(
    conversation_id: str, scenario_id: str, context: dict, *,
    previous_content_hash: str, low_text_pages: list, truncated: bool,
    source_format: str | None = None, expected_revision: int | None = None,
) -> Admission:
    """Admit a freshly stored library entry under the conversation lock.

    With no scenario running it activates at once; with one running it stashes the upload
    for the "new scenario or correction" choice, because guessing wrong (a position pointing
    at a room the new map does not have) costs more than one click.
    """
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if expected_revision is not None and state.state_revision != expected_revision:
            return Admission("stale_revision", state)
        # Images of the replacement stay unexposed until the choice is made: the immutable
        # library entry already holds them, and the chapter window is copied on activation.
        if state.pending_pdf_upload is not None:
            return Admission("raced", state)
        indexes = context["indexes"]
        if state.scenario_text.strip():
            pending: dict[str, Any] = {
                "scenario_id": scenario_id,
                "previous_content_hash": previous_content_hash,
            }
            if source_format:
                pending["source_format"] = source_format
            pending.update({
                "text": context["text"],
                "title": context["manifest"]["title"],
                "low_text_pages": low_text_pages,
                "truncated": truncated,
                "npcs": indexes["npcs"],
                "locations": indexes["locations"],
                "page_maps": (
                    {} if source_format == "markdown"
                    else {str(k): v for k, v in context["scene_maps"].items()}
                ),
                "pregens": context["pregens"],
                "active_chapter_id": context["active_chapter_id"],
                "context_chapter_ids": context["context_chapter_ids"],
            })
            state.pending_pdf_upload = pending
            state_transaction.commit_snapshot(state)
            return Admission("needs_choice", state)
        old_pool = list(state.pregens)
        apply_new_scenario(
            state, context["text"], context["manifest"]["title"], indexes,
            context["scene_maps"], context["pregens"],
        )
        install_library_context(state, scenario_id, context)
        activation = commit_activation(
            conversation_id, state, scenario_id, context, old_pool=old_pool, old_scenario_id=None,
        )
        return Admission("activated", state, activation)
