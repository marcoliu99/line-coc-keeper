"""Persisted scenario transitions before their orchestration moves modules."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import db, pdf_loader, scenario_library
from app.commands.handlers import system, uploads
from app.models import Character, GroupState
from app.repositories import group_state, manual_pregens
from app.services import scenario_ingestion


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(group_state, "DATA_DIR", tmp_path / "images")
    monkeypatch.setattr(scenario_library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
    db._ensure_tables()


@pytest.fixture
def extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scenario_ingestion.pdf_loader, "extract_preview", lambda _: "preview")
    monkeypatch.setattr(
        scenario_ingestion.pdf_loader, "extract_text",
        lambda _source, **_kw: ("--- 第 1 頁 ---\n新劇本", [], False, {1: b"new-image"}, {}),
    )
    monkeypatch.setattr(scenario_ingestion.pdf_loader, "guess_title", lambda *_a, **_kw: "新劇本")
    monkeypatch.setattr(scenario_ingestion.scenario_library, "find_similar", lambda *_a: [])
    monkeypatch.setattr(
        scenario_ingestion.scenario_index, "extract_scenario_index",
        lambda _text: {"npcs": [], "locations": []},
    )
    monkeypatch.setattr(
        scenario_ingestion.pregen_extractor, "extract_pregens",
        lambda _text: [{"name": "新候選", "source": "llm_extracted"}],
    )


def _seed(group: str, *, existing: bool = False) -> None:
    state = GroupState(group, timeline_id="before", kp_assistant_user_id="kp")
    state.characters["player"] = Character("現有調查員", "player", character_id="investigator-1")
    state.log = [{"role": "assistant", "content": "舊敘事", "authority": "presentation"}]
    state.pending_checks["player"] = {"type": "skill", "timeline_id": "before"}
    state.pending_luck_decisions["other"] = {"timeline_id": "before"}
    state.deterministic_check_results["old"] = {"timeline_id": "before"}
    state.resolved_check_events = [{"event_id": "old-result"}]
    state.check_consequence_origins = {"old": {"source": "old"}}
    state.check_consequence_receipts = {"old": {"done": True}}
    state.kp_ooc_log = [{"role": "assistant", "content": "private"}]
    state.game_started = True
    state.current_map_page = {"player": "1"}
    state.current_room_id = {"player": "study"}
    state.party_facing = {"player": "E"}
    state.scene_maps = {"1": {"rooms": [{"id": "study"}]}}
    state.pregens = [{"name": "舊候選", "source": "llm_extracted"}]
    if existing:
        state.scenario_text = "--- 第 1 頁 ---\n舊劇本"
        state.scenario_title = "舊劇本"
        state.active = True
    group_state.save_state(state)
    group_state.save_page_image(group, 1, b"old-image")


async def _submit(group: str, source: str) -> tuple[bool, list[str]]:
    messages: list[str] = []

    async def send(text: str) -> None:
        messages.append(text)

    if source == "pdf":
        accepted = await scenario_ingestion.handle_pdf_upload(
            group, send, send, b"%PDF", "scenario.pdf", skip_similarity=True,
        )
    else:
        accepted = await scenario_ingestion.handle_scenario_markdown_upload(
            group, send, send, "# 新劇本\n內容".encode(), "scenario_new.md",
        )
    return accepted, messages


@pytest.mark.parametrize("source", ["pdf", "markdown"])
def test_first_submission_preserves_investigators_and_history_but_resets_new_scenario_state(
    storage: None, extraction: None, source: str,
) -> None:
    _seed("first")
    accepted, messages = asyncio.run(_submit("first", source))
    state = group_state.load_state("first")

    assert accepted and "已載入劇本" in messages[-1]
    assert state.active and state.scenario_title == ("新劇本" if source == "pdf" else "new")
    assert state.timeline_id != "before"
    assert state.pending_pdf_upload is None and state.pending_scenario_upload is None
    assert state.pending_checks == state.pending_luck_decisions == {}
    assert state.deterministic_check_results == {}
    assert state.resolved_check_events == []
    assert state.check_consequence_origins == state.check_consequence_receipts == {}
    assert state.kp_ooc_log == [] and not state.game_started
    assert state.current_map_page == state.current_room_id == state.party_facing == {}
    assert state.log[0]["content"] == "舊敘事"
    assert state.characters["player"].name == "現有調查員"
    assert [card["name"] for card in state.pregens] == ["新候選"]
    assert group_state.load_page_image("first", 1) == (b"new-image" if source == "pdf" else None)
    assert scenario_library.load_context(state.scenario_library_id)["manifest"]["source_format"] == source


@pytest.mark.parametrize("source", ["pdf", "markdown"])
@pytest.mark.parametrize("choice", ["new", "fix"])
def test_pending_submission_and_choice_preserve_distinct_transition_policies(
    storage: None, extraction: None, source: str, choice: str,
) -> None:
    _seed("choice", existing=True)
    accepted, _ = asyncio.run(_submit("choice", source))
    pending = group_state.load_state("choice")
    assert accepted and pending.scenario_title == "舊劇本"
    assert pending.pending_pdf_upload is not None
    assert pending.pending_pdf_upload["scenario_id"]
    assert pending.timeline_id == "before"
    assert group_state.load_page_image("choice", 1) == b"old-image"

    replies: list[str] = []

    async def send(text: str) -> None:
        replies.append(text)

    asyncio.run(uploads.resolve_pdf_upload_choice("choice", choice, send, user_id="kp"))
    state = group_state.load_state("choice")
    assert state.pending_pdf_upload is None
    assert state.scenario_title == ("新劇本" if source == "pdf" else "new")
    assert state.active and state.characters["player"].name == "現有調查員"
    assert state.log[0]["content"] == "舊敘事"
    assert group_state.load_page_image("choice", 1) == (b"new-image" if source == "pdf" else None)
    if choice == "new":
        assert state.timeline_id != "before"
        assert state.pending_checks == state.pending_luck_decisions == {}
        assert state.deterministic_check_results == {}
        assert state.resolved_check_events == []
        assert state.check_consequence_origins == state.check_consequence_receipts == {}
        assert state.kp_ooc_log == [] and not state.game_started
        assert state.current_map_page == state.current_room_id == state.party_facing == {}
        assert [card["name"] for card in state.pregens] == ["新候選"]
    else:
        assert state.timeline_id == "before"
        assert "player" in state.pending_checks and "other" in state.pending_luck_decisions
        assert "old" in state.deterministic_check_results
        assert state.resolved_check_events[0]["event_id"] == "old-result"
        assert "old" in state.check_consequence_origins and "old" in state.check_consequence_receipts
        assert state.kp_ooc_log and state.game_started
        assert state.current_map_page == {"player": "1"}
        assert state.current_room_id == {"player": "study"}
        assert [card["name"] for card in state.pregens] == ["新候選"]
    assert "已載入劇本" in replies[-1]


def test_scenario_use_keeps_its_own_reset_and_valid_location_policy(
    storage: None, extraction: None,
) -> None:
    _seed("switch", existing=True)
    initial = group_state.load_state("switch")
    initial.current_map_page["other"] = "1"
    initial.current_room_id["other"] = "missing"
    initial.party_facing["other"] = "W"
    group_state.save_state(initial)
    scenario_id = scenario_library.save_markdown_scenario(
        "# 新劇本".encode(), title="新劇本", filename="scenario_new.md", preview="新劇本",
        text="--- 第 1 頁 ---\n新劇本", indexes={"npcs": [], "locations": []},
        pregens=[{"name": "新候選", "source": "llm_extracted"}],
    )
    # Real library text has no map; install a map in the published item through
    # its existing source file to exercise the current location-preservation rule.
    (scenario_library.scenario_path(scenario_id) / "scene_maps.json").write_text(
        '{"1": {"rooms": [{"id": "study"}]}}', encoding="utf-8"
    )
    replies: list[str] = []

    async def send(text: str) -> None:
        replies.append(text)

    asyncio.run(system.handle_system_command(
        "switch", "kp", send, None, None, None, ["/coc", "scenario", "use", scenario_id],
    ))
    state = group_state.load_state("switch")
    assert replies[-1].startswith("KP 已選擇《新劇本》")
    assert state.timeline_id != "before" and state.active
    assert state.pending_checks == state.pending_luck_decisions == {}
    assert state.deterministic_check_results == {} and state.resolved_check_events == []
    assert "old" in state.check_consequence_origins and "old" in state.check_consequence_receipts
    assert state.log[0]["content"] == "舊敘事"
    assert state.characters["player"].name == "現有調查員"
    assert [card["name"] for card in state.pregens] == ["新候選"]
    assert state.current_map_page == {"player": "1"}
    assert state.current_room_id == {"player": "study"}
    assert state.party_facing == {"player": "E"}
    assert group_state.load_page_image("switch", 1) is None


def test_similar_pdf_stages_raw_source_without_activation(
    storage: None, extraction: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed("similar", existing=True)
    monkeypatch.setattr(
        scenario_library, "find_similar",
        lambda *_args: [{"id": "old", "title": "舊劇本", "score": 0.9}],
    )
    monkeypatch.setattr(
        pdf_loader, "extract_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full parse must wait")),
    )
    messages: list[str] = []

    async def send(text: str) -> None:
        messages.append(text)

    accepted = asyncio.run(scenario_ingestion.handle_pdf_upload(
        "similar", send, send, b"%PDF-original", "scenario.pdf",
    ))
    state = group_state.load_state("similar")
    assert not accepted and "偵測到相似劇本" in messages[-1]
    assert state.scenario_title == "舊劇本" and state.timeline_id == "before"
    assert state.pending_pdf_upload is None and state.pending_scenario_upload is not None
    assert scenario_library.read_staged_upload(state.pending_scenario_upload["key"]) == b"%PDF-original"
    assert group_state.load_page_image("similar", 1) == b"old-image"


def test_failed_investigator_install_rolls_back_scenario_and_keeps_images(
    storage: None, extraction: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed("atomic")
    monkeypatch.setattr(
        manual_pregens, "install_pool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("card install failed")),
    )

    with pytest.raises(OSError, match="card install failed"):
        asyncio.run(_submit("atomic", "pdf"))

    state = group_state.load_state("atomic")
    assert state.scenario_title == "" and state.timeline_id == "before"
    assert [card["name"] for card in state.pregens] == ["舊候選"]
    assert group_state.load_page_image("atomic", 1) == b"old-image"


@pytest.mark.parametrize("fails", [True, False])
def test_merged_pdf_parts_are_consumed_only_after_accepted_submission(
    storage: None, extraction: None, monkeypatch: pytest.MonkeyPatch, fails: bool,
) -> None:
    _seed("merge")
    first = scenario_library.stage_upload(b"first-part")
    second = scenario_library.stage_upload(b"second-part")
    state = group_state.load_state("merge")
    state.staged_pdf_parts = [
        {"key": first, "file_name": "first.pdf"},
        {"key": second, "file_name": "second.pdf"},
    ]
    group_state.save_state(state)
    monkeypatch.setattr(pdf_loader, "combine_pdfs", lambda _parts: b"%PDF")
    if fails:
        monkeypatch.setattr(
            pdf_loader, "extract_text",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("parse failed")),
        )
    replies: list[str] = []

    async def send(text: str) -> None:
        replies.append(text)

    asyncio.run(system.handle_system_command(
        "merge", "kp", send, None, None, None,
        ["/coc", "scenario", "merge", first[:12], second[:12]],
    ))
    latest = group_state.load_state("merge")
    if fails:
        assert len(latest.staged_pdf_parts) == 2
        assert scenario_library.read_staged_upload(first) == b"first-part"
        assert scenario_library.read_staged_upload(second) == b"second-part"
        assert latest.scenario_library_id == ""
        assert "讀取 PDF 失敗" in replies[-1]
    else:
        assert latest.staged_pdf_parts == [] and latest.scenario_library_id
        with pytest.raises(FileNotFoundError):
            scenario_library.read_staged_upload(first)
        with pytest.raises(FileNotFoundError):
            scenario_library.read_staged_upload(second)
