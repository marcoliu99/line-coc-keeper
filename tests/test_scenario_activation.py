"""Scenario state is committed before derived page images are published."""

import asyncio
from pathlib import Path

import pytest

from app import checkpoints, db, keeper, scenario_activation
from app.commands.handlers import system
from app.models import GroupState
from app.repositories import group_state
from app.services import scenario_ingestion


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(group_state, "DATA_DIR", tmp_path / "images")
    db._ensure_tables()
    return tmp_path


def test_failed_commit_does_not_publish_images(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    group_state.save_page_image("group", 1, b"old")
    old = group_state.load_page_image("group", 1)
    called = False

    def copy_images(_scenario: str, _pages: set[int], _save) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", copy_images)

    def reject() -> None:
        raise RuntimeError("sqlite commit failed")

    with pytest.raises(RuntimeError, match="sqlite commit failed"):
        scenario_activation.commit_and_refresh(reject, "group", "new", {"page_numbers": {2}})

    assert not called
    assert group_state.load_page_image("group", 1) == old


def test_image_failure_preserves_committed_state(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_library_id="new")
    group_state.save_page_image("group", 2, b"old")

    def fail_copy(_scenario: str, _pages: set[int], _save) -> None:
        assert group_state.load_state("group").scenario_library_id == "new"
        raise OSError("image source failed")

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", fail_copy)
    value, refreshed = scenario_activation.commit_and_refresh(
        lambda: group_state.save_state(state), "group", "new", {"page_numbers": {2}},
    )

    assert value is None
    assert not refreshed
    assert group_state.load_state("group").scenario_library_id == "new"
    assert group_state.load_page_image("group", 2) is None


def test_partial_source_copy_failure_cannot_publish_old_or_partial_images(
    storage: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_state.save_page_image("group", 2, b"old")

    def fail_after_one(_scenario: str, _pages: set[int], save) -> None:
        save(2, b"partial-new")
        raise OSError("second image missing")

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", fail_after_one)
    _, refreshed = scenario_activation.commit_and_refresh(
        lambda: group_state.save_state(GroupState(group_id="group", scenario_library_id="new")),
        "group", "new", {"page_numbers": {2, 3}},
    )

    assert not refreshed
    assert group_state.load_page_image("group", 2) is None


def test_success_replaces_old_images_after_commit(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_library_id="new")
    group_state.save_page_image("group", 1, b"old")

    def copy_images(_scenario: str, pages: set[int], save) -> None:
        assert group_state.load_state("group").scenario_library_id == "new"
        assert pages == {2}
        save(2, b"new")

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", copy_images)
    _, refreshed = scenario_activation.commit_and_refresh(
        lambda: group_state.save_state(state), "group", "new", {"page_numbers": {2}},
    )

    assert refreshed
    assert group_state.load_page_image("group", 1) is None
    assert group_state.load_page_image("group", 2) == b"new"


def test_stale_postcommit_refresh_cannot_overwrite_newer_scenario_images(
    storage: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_state.save_state(GroupState(group_id="group", scenario_library_id="old"))

    def copy_images(scenario_id: str, _pages: set[int], save) -> None:
        if scenario_id == "A":
            newer = group_state.load_state("group")
            newer.scenario_library_id = "B"
            group_state.save_state(newer)
            scenario_activation.refresh_context_images("group", "B", {"page_numbers": {1}})
            save(1, b"image-A")
        else:
            save(1, b"image-B")

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", copy_images)

    def commit_a() -> None:
        current = group_state.load_state("group")
        current.scenario_library_id = "A"
        group_state.save_state(current)

    scenario_activation.commit_and_refresh(commit_a, "group", "A", {"page_numbers": {1}})

    assert group_state.load_state("group").scenario_library_id == "B"
    assert group_state.load_page_image("group", 1) == b"image-B"


def test_stale_same_scenario_refresh_cannot_overwrite_newer_revision(
    storage: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_state.save_state(GroupState(group_id="group", scenario_library_id="old"))
    copies = 0

    def copy_images(_scenario_id: str, _pages: set[int], save) -> None:
        nonlocal copies
        copies += 1
        if copies == 1:
            newer = group_state.load_state("group")
            newer.scenario_text = "newer published version"
            group_state.save_state(newer)
            scenario_activation.refresh_context_images("group", "same", {"page_numbers": {1}})
            save(1, b"old-version")
        else:
            save(1, b"new-version")

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", copy_images)

    def commit_a() -> None:
        current = group_state.load_state("group")
        current.scenario_library_id = "same"
        group_state.save_state(current)

    scenario_activation.commit_and_refresh(commit_a, "group", "same", {"page_numbers": {1}})

    assert group_state.load_state("group").scenario_text == "newer published version"
    assert group_state.load_page_image("group", 1) == b"new-version"


def test_stale_copy_failure_does_not_clear_newer_images(
    storage: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_state.save_state(GroupState(group_id="group", scenario_library_id="old"))

    def copy_images(scenario_id: str, _pages: set[int], save) -> None:
        if scenario_id == "A":
            newer = group_state.load_state("group")
            newer.scenario_library_id = "B"
            group_state.save_state(newer)
            scenario_activation.refresh_context_images("group", "B", {"page_numbers": {1}})
            raise OSError("old image unavailable")
        save(1, b"image-B")

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", copy_images)

    def commit_a() -> None:
        current = group_state.load_state("group")
        current.scenario_library_id = "A"
        group_state.save_state(current)

    _, refreshed = scenario_activation.commit_and_refresh(commit_a, "group", "A", {"page_numbers": {1}})
    assert not refreshed
    assert group_state.load_page_image("group", 1) == b"image-B"


def test_unrelated_write_during_image_copy_still_publishes_active_scenario_images(
    storage: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {
        "page_numbers": {1}, "text": "same scene",
        "manifest": {"updated_at": "library-version-1"},
    }
    group_state.save_state(GroupState(group_id="group", scenario_library_id="old"))
    group_state.save_page_image("group", 1, b"old-image")
    copies = 0

    def copy_images(_scenario_id: str, _pages: set[int], save) -> None:
        nonlocal copies
        copies += 1
        if copies == 1:
            unrelated = group_state.load_state("group")
            unrelated.keeper_persona = "concurrent persona change"
            group_state.save_state(unrelated)
        save(1, b"new-image")

    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images", copy_images)

    def commit_a() -> None:
        current = group_state.load_state("group")
        current.scenario_library_id = "A"
        current.scenario_text = "same scene"
        group_state.save_state(current)

    scenario_activation.commit_and_refresh(commit_a, "group", "A", context)
    assert group_state.load_state("group").keeper_persona == "concurrent persona change"
    assert group_state.load_page_image("group", 1) == b"new-image"


def _context() -> dict:
    return {
        "manifest": {"title": "New scenario", "content_hash": "2" * 64},
        "text": "New text", "active_chapter_id": "second",
        "context_chapter_ids": ["second"], "indexes": {"npcs": [], "locations": []},
        "pregens": [], "scene_maps": {}, "page_numbers": {2},
    }


def test_scenario_use_keeps_committed_state_on_image_failure(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", kp_assistant_user_id="kp")
    group_state.save_state(state)
    group_state.save_page_image("group", 1, b"old")
    monkeypatch.setattr(system.scenario_library, "load_context", lambda *_: _context())
    monkeypatch.setattr(system.scenario_library, "copy_context_images",
                        lambda *_: (_ for _ in ()).throw(OSError("image failure")))
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    asyncio.run(system.handle_system_command("group", "kp", reply, None, None, None,
                                             ["/coc", "scenario", "use", "new"]))

    assert group_state.load_state("group").scenario_library_id == "new"
    assert group_state.load_page_image("group", 1) is None
    assert "圖片快取刷新失敗" in replies[-1]


def test_scenario_use_commit_failure_does_not_touch_images(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", kp_assistant_user_id="kp")
    group_state.save_state(state)
    group_state.save_page_image("group", 1, b"old")
    monkeypatch.setattr(system.scenario_library, "load_context", lambda *_: _context())
    monkeypatch.setattr(system.state_transaction, "commit_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("db failed")))

    async def reply(_text: str) -> None:
        pass

    with pytest.raises(OSError, match="db failed"):
        asyncio.run(system.handle_system_command("group", "kp", reply, None, None, None,
                                                 ["/coc", "scenario", "use", "new"]))

    assert group_state.load_state("group").scenario_library_id == ""
    assert group_state.load_page_image("group", 1) == b"old"


def test_chapter_advance_image_failure_does_not_undo_commit(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_library_id="scenario", active_chapter_id="first",
                       active_scenario_source_hash="1" * 64)
    group_state.save_state(state)
    monkeypatch.setattr(scenario_activation.scenario_library, "next_chapter_id", lambda *_: "second")
    monkeypatch.setattr(scenario_activation.scenario_library, "load_context", lambda *_: _context())
    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images",
                        lambda *_: (_ for _ in ()).throw(OSError("image failure")))

    result = keeper._execute_tool(state, "advance_scenario_chapter", {}, [], [])

    assert result["ok"]
    assert "圖片快取刷新失敗" in result["notice"]
    assert group_state.load_state("group").active_chapter_id == "second"
    assert group_state.load_state("group").active_scenario_source_hash == "2" * 64


def test_chapter_advance_commit_failure_preserves_images(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_library_id="scenario", active_chapter_id="first",
                       active_scenario_source_hash="1" * 64)
    group_state.save_state(state)
    group_state.save_page_image("group", 1, b"old")
    monkeypatch.setattr(scenario_activation.scenario_library, "next_chapter_id", lambda *_: "second")
    monkeypatch.setattr(scenario_activation.scenario_library, "load_context", lambda *_: _context())
    monkeypatch.setattr(group_state, "write_state_tx",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("db failed")))

    result = keeper._execute_tool(state, "advance_scenario_chapter", {}, [], [])

    assert not result["ok"]
    assert group_state.load_state("group").active_chapter_id == "first"
    assert group_state.load_state("group").active_scenario_source_hash == "1" * 64
    assert group_state.load_page_image("group", 1) == b"old"


def test_upload_choice_image_failure_preserves_activation(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_text="Old text")
    state.pending_pdf_upload = {"scenario_id": "new", "low_text_pages": [], "truncated": False}
    group_state.save_state(state)
    group_state.save_page_image("group", 1, b"old")
    monkeypatch.setattr(scenario_ingestion.scenario_library, "load_context", lambda *_: _context())
    monkeypatch.setattr(scenario_ingestion.scenario_library, "copy_context_images",
                        lambda *_: (_ for _ in ()).throw(OSError("image failed")))

    result = asyncio.run(scenario_ingestion.apply_pdf_upload_choice("group", "new"))

    assert "圖片快取刷新失敗" in result
    assert group_state.load_state("group").scenario_library_id == "new"
    assert group_state.load_page_image("group", 1) is None


def test_upload_choice_commit_failure_preserves_old_state(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_text="Old text")
    state.pending_pdf_upload = {"scenario_id": "new", "low_text_pages": [], "truncated": False}
    group_state.save_state(state)
    group_state.save_page_image("group", 1, b"old")
    monkeypatch.setattr(scenario_ingestion.scenario_library, "load_context", lambda *_: _context())
    monkeypatch.setattr(scenario_ingestion.state_transaction, "commit_snapshot",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("db failed")))

    with pytest.raises(OSError, match="db failed"):
        asyncio.run(scenario_ingestion.apply_pdf_upload_choice("group", "new"))

    assert group_state.load_state("group").scenario_library_id == ""
    assert group_state.load_page_image("group", 1) == b"old"


def test_rollback_commit_failure_does_not_refresh_images(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_library_id="old", scenario_title="Old")
    group_state.save_state(state)
    checkpoint = checkpoints.create_checkpoint(state, label="old")
    state.scenario_library_id = "new"
    state.scenario_title = "New"
    group_state.save_state(state)
    group_state.save_page_image("group", 1, b"new")
    monkeypatch.setattr(group_state, "write_state_tx",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("db failed")))

    with pytest.raises(OSError, match="db failed"):
        checkpoints.rollback("group", checkpoint["checkpoint_id"], actor_id="kp")

    assert group_state.load_state("group").scenario_title == "New"
    assert group_state.load_page_image("group", 1) == b"new"


def test_rollback_image_failure_retains_committed_state(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", scenario_library_id="old", scenario_title="Old")
    group_state.save_state(state)
    checkpoint = checkpoints.create_checkpoint(state, label="old")
    state.scenario_title = "New"
    group_state.save_state(state)
    monkeypatch.setattr(scenario_activation.scenario_library, "load_context", lambda *_: _context())
    monkeypatch.setattr(scenario_activation.scenario_library, "copy_context_images",
                        lambda *_: (_ for _ in ()).throw(OSError("image failed")))

    restored, _, _ = checkpoints.rollback("group", checkpoint["checkpoint_id"], actor_id="kp")

    assert restored.scenario_title == "Old"
    assert group_state.load_state("group").scenario_title == "Old"
