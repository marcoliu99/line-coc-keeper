"""One module owns how a scenario is admitted into a conversation (``scenario_admission``).

Real SQLite, a fake library entry, and the page-image publisher replaced: the four doors
(PDF upload, Markdown upload, the new/correction choice, ``/coc scenario use``) share this
sequence, and their own wording is covered by the ingestion trace.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app import db, scenario_activation, scenario_library
from app.models import GroupState
from app.repositories import group_state
from app.services import scenario_admission as admission


@pytest.fixture(autouse=True)
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(group_state, "DATA_DIR", tmp_path / "images")
    db._ensure_tables()


def _context(**fields: Any) -> dict[str, Any]:
    return {
        "manifest": {"title": "New Scenario", "content_hash": "hash-new"}, "text": "新劇本內文",
        "active_chapter_id": "chapter-01", "context_chapter_ids": ["chapter-01"],
        "indexes": {"npcs": [{"name": "教徒"}], "locations": []},
        "pregens": [{"name": "新人", "occupation": "醫生", "source": "llm_extracted"}],
        "scene_maps": {1: {"rooms": []}}, "page_numbers": {1}, **fields,
    }


def _running(group: str = "g", **fields: Any) -> GroupState:
    state = GroupState(
        group_id=group, scenario_title="Old", scenario_text="舊劇本", active=True, game_started=True,
        timeline_id="timeline-old", **fields,
    )
    group_state.save_state(state)
    return group_state.load_state(group)


def _admit(group: str = "g", **options: Any) -> admission.Admission:
    values: dict[str, Any] = {
        "previous_content_hash": "", "low_text_pages": [2], "truncated": True, **options,
    }
    with patch.object(scenario_activation, "refresh_after_commit", return_value=True):
        return asyncio.run(admission.admit_upload(group, "new-id", _context(), **values))


def test_blocks_are_reported_in_the_order_the_doors_check_them() -> None:
    state = GroupState(group_id="g")
    assert admission.pending_block(state) is None
    state.pending_scenario_upload = {"key": "k"}
    assert admission.pending_block(state) == "similar_upload"
    assert admission.pending_block(state, include_similar=False) is None
    state.pending_pdf_upload = {"title": "t"}
    assert admission.pending_block(state) == "upload_choice"
    state.pending_pregen_luck = {"u1": "char"}
    assert admission.pending_block(state) == "pregen_luck"


def test_content_hash_is_empty_when_there_is_nothing_to_read(monkeypatch: pytest.MonkeyPatch) -> None:
    assert admission.content_hash(None) == ""
    assert admission.content_hash("") == ""
    monkeypatch.setattr(scenario_library, "load_context", lambda _id: (_ for _ in ()).throw(FileNotFoundError()))
    assert admission.content_hash("missing") == ""
    monkeypatch.setattr(scenario_library, "load_context", lambda _id: {"manifest": {"content_hash": "abc"}})
    assert admission.content_hash("present") == "abc"


def test_the_first_upload_is_activated_at_once_on_a_new_timeline() -> None:
    group_state.save_state(GroupState(group_id="g"))
    result = _admit()

    assert result.status == "activated"
    assert result.activation is not None and result.activation.image_refreshed
    saved = group_state.load_state("g")
    assert (saved.scenario_library_id, saved.scenario_title) == ("new-id", "New Scenario")
    assert saved.active and not saved.game_started
    assert [p["name"] for p in saved.pregens] == ["新人"]
    assert saved.pending_pdf_upload is None


def test_an_upload_while_a_game_runs_is_stashed_for_the_choice_and_nothing_else_changes() -> None:
    _running(pending_checks={"u1": {"type": "skill"}})
    result = _admit(previous_content_hash="hash-old")

    assert result.status == "needs_choice" and result.activation is None
    saved = group_state.load_state("g")
    assert saved.scenario_title == "Old" and saved.timeline_id == "timeline-old" and saved.pending_checks
    pending = saved.pending_pdf_upload
    assert pending is not None
    assert (pending["scenario_id"], pending["previous_content_hash"]) == ("new-id", "hash-old")
    assert (pending["low_text_pages"], pending["truncated"]) == ([2], True)
    assert pending["page_maps"] == {"1": {"rooms": []}} and "source_format" not in pending


def test_a_markdown_stash_says_so_and_carries_no_maps() -> None:
    _running()
    result = _admit(low_text_pages=[], truncated=False, source_format="markdown")

    assert result.status == "needs_choice"
    pending = group_state.load_state("g").pending_pdf_upload
    assert pending is not None
    assert pending["source_format"] == "markdown" and pending["page_maps"] == {}


def test_a_second_upload_behind_an_unresolved_choice_is_raced_and_leaves_the_first_alone() -> None:
    _running()
    assert _admit().status == "needs_choice"
    first = group_state.load_state("g").pending_pdf_upload

    assert _admit().status == "raced"
    assert group_state.load_state("g").pending_pdf_upload == first


def test_a_changed_revision_stops_the_upload_before_it_touches_anything() -> None:
    state = _running()
    result = _admit(expected_revision=state.state_revision + 1)

    assert result.status == "stale_revision"
    saved = group_state.load_state("g")
    assert saved.scenario_title == "Old" and saved.pending_pdf_upload is None


def test_a_correction_keeps_the_party_where_it_is_and_the_claimed_cast() -> None:
    _running(
        pregens=[{"name": "林文", "occupation": "記者", "claimed_by": "u1", "source": "llm_extracted"}],
        current_map_page={"u1": "1"}, current_room_id={"u1": "hall"},
        pending_checks={"u1": {"type": "skill"}},
    )
    assert _admit().status == "needs_choice"
    state = group_state.load_state("g")
    pending = state.pending_pdf_upload
    assert pending is not None

    with patch.object(scenario_activation, "refresh_after_commit", return_value=True):
        activation = admission.activate_pending_choice("g", state, pending, _context(), "fix")

    saved = group_state.load_state("g")
    assert activation.image_refreshed and not activation.cards_stale
    assert (saved.scenario_title, saved.timeline_id) == ("New Scenario", "timeline-old")
    assert saved.current_room_id == {"u1": "hall"} and saved.game_started and saved.pending_checks
    assert {p["name"] for p in saved.pregens if p.get("claimed_by")} == {"林文"}
    assert saved.pending_pdf_upload is None


def test_a_new_scenario_choice_resets_the_timeline_and_the_decisions_bound_to_it() -> None:
    _running(
        current_room_id={"u1": "hall"}, pending_checks={"u1": {"type": "skill"}},
        pending_luck_decisions={"u1": {"options": []}},
    )
    assert _admit().status == "needs_choice"
    state = group_state.load_state("g")
    pending = state.pending_pdf_upload
    assert pending is not None

    with patch.object(scenario_activation, "refresh_after_commit", return_value=True):
        admission.activate_pending_choice("g", state, pending, _context(), "new")

    saved = group_state.load_state("g")
    assert saved.timeline_id != "timeline-old" and not saved.game_started
    assert not saved.pending_checks and not saved.pending_luck_decisions and not saved.current_room_id


def test_a_failed_image_refresh_is_reported_but_the_scenario_stays_active() -> None:
    group_state.save_state(GroupState(group_id="g"))
    with patch.object(scenario_activation, "refresh_after_commit", return_value=False):
        result = asyncio.run(admission.admit_upload(
            "g", "new-id", _context(), previous_content_hash="", low_text_pages=[], truncated=False,
        ))

    assert result.activation is not None
    assert result.activation.image_note == admission.IMAGE_REFRESH_FAILED
    assert group_state.load_state("g").scenario_library_id == "new-id"


def test_using_a_stored_scenario_keeps_the_investigators_and_clears_the_old_decisions() -> None:
    _running(pending_checks={"u1": {"type": "skill"}}, openai_previous_response_id="resp-old")
    state = group_state.load_state("g")

    with patch.object(scenario_activation, "refresh_after_commit", return_value=True):
        notice, activation = admission.activate_selected("g", state, "new-id", _context(), variant_id="original")

    saved = group_state.load_state("g")
    assert isinstance(notice, str) and activation.image_refreshed
    assert saved.scenario_library_id == "new-id" and saved.scenario_variant_id == "original"
    assert saved.timeline_id != "timeline-old" and not saved.pending_checks
    assert saved.openai_previous_response_id == "" and saved.active
