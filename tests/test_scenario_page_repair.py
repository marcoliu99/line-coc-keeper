"""repair_*.md overwrites whole pages of the loaded scenario text, like a role card changes a pregen."""
from __future__ import annotations

import asyncio

import pytest

from app import scenario_page_repair as repair
from app.models import GroupState
from app.repositories import group_state
from app.services import scenario_ingestion
from tests.state_store import replace_state

TEXT = "--- 第 1 頁 ---\n  keep one  \n\n--- 第 2 頁 ---\nold two\n\n--- 第 3 頁 ---\n\nkeep three\n\n\n--- 第 4 頁 ---\nold four\n"


def page(n: int, body: str) -> str:
    return f"--- 第 {n} 頁 ---\n{body}\n\n"


# parsing

def test_pages_are_read_by_their_markers_and_the_preamble_is_ignored():
    pages = repair.parse_pages("# 修復\n說明文字\n\n" + page(4, " new four ") + page(2, "new\ntwo"))
    assert pages == {2: "new\ntwo", 4: "new four"}


def test_a_pasted_code_block_bom_and_crlf_are_tolerated():
    content = "﻿```markdown\r\n" + page(2, "new two") + "```\r\n"
    assert repair.parse_pages(content) == {2: "new two"}


@pytest.mark.parametrize("content", [
    "no markers at all",
    page(2, "a") + page(2, "b"),
    page(0, "a"),
    "--- 第 2 頁 ---\n\n--- 第 3 頁 ---\nbody",
])
def test_unreadable_files_are_rejected_with_a_reason(content):
    with pytest.raises(repair.PageRepairError):
        repair.parse_pages(content)


# merging

def test_only_the_listed_pages_change_and_everything_else_keeps_its_exact_bytes():
    merged = repair.apply_pages(TEXT, {4: "x", 2: "a much longer replacement\n\nwith lines"})
    assert merged == ("--- 第 1 頁 ---\n  keep one  \n\n--- 第 2 頁 ---\na much longer replacement\n\nwith lines\n\n"
                      "--- 第 3 頁 ---\n\nkeep three\n\n\n--- 第 4 頁 ---\nx")


def test_a_shorter_replacement_and_the_last_page_work():
    merged = repair.apply_pages(TEXT, {1: "s", 4: "tail"})
    assert merged.startswith("--- 第 1 頁 ---\ns\n\n--- 第 2 頁 ---\nold two")
    assert merged.endswith("--- 第 4 頁 ---\ntail")


def test_a_scenario_without_ordered_markers_is_rejected():
    with pytest.raises(repair.PageRepairError):
        repair.apply_pages("plain text", {1: "x"})
    with pytest.raises(repair.PageRepairError):
        repair.apply_pages("--- 第 2 頁 ---\nb\n\n--- 第 1 頁 ---\na", {1: "x"})


def test_a_chapter_window_is_matched_by_physical_page_number_and_other_pages_are_skipped():
    window = "--- 第 10 頁 ---\nten\n\n--- 第 11 頁 ---\neleven"
    assert repair.present_pages(window) == {10, 11}
    assert repair.apply_pages(window, {11: "new", 3: "elsewhere"}) == "--- 第 10 頁 ---\nten\n\n--- 第 11 頁 ---\nnew"
    assert repair.apply_pages(window, {3: "elsewhere"}) == window


# the upload

def upload(text: str, *, state: GroupState | None):
    if state is not None:
        replace_state(state)
    replies: list[str] = []

    async def reply(message):
        replies.append(message)

    asyncio.run(scenario_ingestion.handle_page_repair_upload("g", reply, text, "repair_x.md"))
    return replies


def test_the_upload_replaces_the_pages_in_the_running_game_and_nothing_else():
    state = GroupState("g", scenario_text=TEXT, scenario_title="T", game_started=True)
    replies = upload(page(2, "new two"), state=state)
    saved = group_state.load_state("g")
    assert "new two" in saved.scenario_text and "old two" not in saved.scenario_text
    assert "keep three" in saved.scenario_text and saved.game_started and saved.scenario_title == "T"
    assert "第 2 頁" in replies[0]


def test_uploading_the_same_pages_again_changes_nothing():
    state = GroupState("g", scenario_text=TEXT)
    upload(page(2, "new two"), state=state)
    revision = group_state.load_state("g").state_revision
    replies = upload(page(2, "new two"), state=None)
    assert group_state.load_state("g").state_revision == revision
    assert "沒有變動" in replies[0]


def test_a_bad_file_or_no_scenario_changes_nothing_and_says_why():
    assert "找不到頁碼標記" in upload("hello", state=GroupState("g", scenario_text=TEXT))[0]
    assert group_state.load_state("g").scenario_text == TEXT
    assert "沒有載入劇本" in upload(page(1, "x"), state=GroupState("g"))[0]
    assert "只有 4 頁" in upload(page(9, "x"), state=GroupState("g", scenario_text=TEXT))[0]


def test_the_repair_survives_loading_the_scenario_again_from_the_library(tmp_path):
    from app import scenario_activation, scenario_library

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(scenario_library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
        sid = scenario_library.save_markdown_scenario(
            TEXT.encode(), title="T", filename="s.md", preview="p", text=TEXT, indexes={}, pregens=[])
        context = scenario_library.load_context(sid)
        state = GroupState("g")
        scenario_activation.install_context_fields(state, sid, context)
        replace_state(state)
        replies = upload(page(2, "new two"), state=None)
        assert "第 2 頁" in replies[0]
        saved = group_state.load_state("g")
        assert saved.scenario_library_id == sid and "new two" in saved.scenario_text
        reloaded = GroupState("g")  # /coc scenario use: the library text is installed again
        scenario_activation.install_context_fields(reloaded, sid, scenario_library.load_context(sid))
        assert "new two" in reloaded.scenario_text and "old two" not in reloaded.scenario_text
        assert "keep three" in reloaded.scenario_text
        other = GroupState("other")  # another conversation of the same scenario is not affected
        scenario_activation.install_context_fields(other, sid, scenario_library.load_context(sid))
        assert "old two" in other.scenario_text


def test_a_changed_library_source_ignores_the_saved_pages():
    from app import db
    from app.repositories import page_repairs
    with db.transaction() as conn:
        page_repairs.save(conn, "g", "s", "hash-a", {2: "new two"})
    assert "new two" in page_repairs.apply_saved("g", "s", "hash-a", TEXT)
    assert page_repairs.apply_saved("g", "s", "hash-b", TEXT) == TEXT
    with db.transaction() as conn:  # a new source starts a fresh set instead of mixing old pages in
        page_repairs.save(conn, "g", "s", "hash-b", {4: "new four"})
    assert page_repairs.load("g", "s", "hash-b") == {4: "new four"}


def test_pages_outside_the_loaded_chapter_window_are_saved_and_laid_over_when_they_load(tmp_path):
    from app import scenario_library
    from app.repositories import page_repairs
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(scenario_library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
        full = "\n\n".join(f"--- 第 {n} 頁 ---\nbody {n}" for n in range(1, 13))
        sid = scenario_library.save_markdown_scenario(
            full.encode(), title="T", filename="s.md", preview="p", text=full, indexes={}, pregens=[])
        manifest = scenario_library.source_manifest(sid)
        window = "--- 第 10 頁 ---\nbody 10\n\n--- 第 11 頁 ---\nbody 11"
        replace_state(GroupState("g", scenario_text=window, scenario_library_id=sid,
                                 active_scenario_source_hash=manifest["content_hash"]))
        replies = upload(page(11, "fixed 11") + page(3, "fixed 3"), state=None)
        assert "已替換第 11 頁" in replies[0] and "第 3 頁不在目前載入的章節裡" in replies[0]
        assert "fixed 11" in group_state.load_state("g").scenario_text
        later = page_repairs.apply_saved("g", sid, manifest["content_hash"], "--- 第 2 頁 ---\nb2\n\n--- 第 3 頁 ---\nb3")
        assert later == "--- 第 2 頁 ---\nb2\n\n--- 第 3 頁 ---\nfixed 3"
        assert "只有 12 頁" in upload(page(13, "x"), state=None)[0]


def test_a_held_conversation_gets_the_hold_notice_and_nothing_changes():
    from unittest.mock import patch

    from app.services import mutation_admission
    replace_state(GroupState("g", scenario_text=TEXT))
    with patch.object(mutation_admission, "is_held", return_value=True):
        replies = upload(page(2, "new two"), state=None)
    assert replies == [mutation_admission.NOTICE]
    assert group_state.load_state("g").scenario_text == TEXT


def test_the_repair_survives_newgame_and_loading_the_same_scenario_again(tmp_path):
    """/coc newgame wipes the game, so the repair has to come back with the scenario, as role cards do."""
    from app import scenario_library
    from app.commands.handlers import system
    from app.services import scenario_lifecycle
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(scenario_library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
        sid = scenario_library.save_markdown_scenario(
            TEXT.encode(), title="T", filename="s.md", preview="p", text=TEXT, indexes={}, pregens=[])

        async def play():
            replies: list[str] = []

            async def reply(message):
                replies.append(message)

            await scenario_lifecycle.submit_published_scenario("g", sid, source_format="markdown")
            await scenario_ingestion.handle_page_repair_upload("g", reply, page(2, "new two"), "repair_x.md")
            await system._handle_newgame("g", reply)
            assert group_state.load_state("g").scenario_text == ""
            await scenario_lifecycle.submit_published_scenario("g", sid, source_format="markdown")
            return group_state.load_state("g").scenario_text

        text = asyncio.run(play())
        assert "new two" in text and "old two" not in text and "keep three" in text


def test_a_closing_fence_belongs_to_the_last_page_unless_the_whole_answer_is_fenced():
    code = "```json\n{}\n```"
    assert repair.parse_pages(page(2, "intro\n" + code)) == {2: "intro\n" + code}
    assert repair.parse_pages("```\n" + page(2, "intro\n" + code) + "```") == {2: "intro\n" + code}


def test_pages_separated_by_a_single_newline_keep_their_markers():
    text = "--- 第 1 頁 ---\none\n--- 第 2 頁 ---\ntwo\n--- 第 3 頁 ---\nthree"
    merged = repair.apply_pages(text, {2: "new two"})
    assert merged == "--- 第 1 頁 ---\none\n--- 第 2 頁 ---\nnew two\n--- 第 3 頁 ---\nthree"
    assert repair.present_pages(merged) == {1, 2, 3}


def test_a_legacy_entry_without_a_content_hash_still_saves_the_pages():
    from app import db
    from app.repositories import page_repairs
    replace_state(GroupState("g", scenario_text=TEXT, scenario_library_id="legacy", active_scenario_source_hash=""))
    replies = upload(page(2, "new two"), state=None)
    assert "newgame" in replies[0]
    assert page_repairs.load("g", "legacy", "") == {2: "new two"}
    with db.transaction() as conn:
        page_repairs.save(conn, "g", "legacy", "", {4: "new four"})
    assert page_repairs.load("g", "legacy", "") == {2: "new two", 4: "new four"}


def test_an_outer_fence_after_a_heading_or_note_is_stripped_with_its_closing_fence():
    content = "# 修復\n說明\n```markdown\n" + page(2, "new two") + "```\n"
    assert repair.parse_pages(content) == {2: "new two"}
    assert repair.parse_pages("# 修復\n" + page(2, "intro\n```py\nx\n```")) == {2: "intro\n```py\nx\n```"}


def test_a_game_saved_before_the_state_kept_the_source_hash_binds_to_the_library_hash(tmp_path):
    from app import scenario_library
    from app.repositories import page_repairs
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(scenario_library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
        sid = scenario_library.save_markdown_scenario(
            TEXT.encode(), title="T", filename="s.md", preview="p", text=TEXT, indexes={}, pregens=[])
        library_hash = scenario_library.source_manifest(sid)["content_hash"]
        replace_state(GroupState("g", scenario_text=TEXT, scenario_library_id=sid, active_scenario_source_hash=""))
        upload(page(2, "new two"), state=None)
        assert page_repairs.load("g", sid, library_hash) == {2: "new two"}
