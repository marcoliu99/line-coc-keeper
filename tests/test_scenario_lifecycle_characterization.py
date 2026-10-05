"""Persisted scenario transitions before their orchestration moves modules."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app import db, pdf_loader, scenario_library
from app.commands.handlers import system, uploads
from app.models import Character, GroupState
from app.repositories import group_state, manual_pregens, state_transaction
from app.services import scenario_ingestion, scenario_lifecycle


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
    state.openai_previous_response_id = "old-provider-response"
    state.openai_previous_response_timeline_id = "before"
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
    assert state.openai_previous_response_id == state.openai_previous_response_timeline_id == ""
    assert state.current_map_page == state.current_room_id == state.party_facing == {}
    assert state.log[0]["content"] == "舊敘事"
    assert state.characters["player"].name == "現有調查員"
    assert [card["name"] for card in state.pregens] == ["新候選"]
    assert group_state.load_page_image("first", 1) == (b"new-image" if source == "pdf" else None)
    manifest = scenario_library.load_context(state.scenario_library_id)["manifest"]
    assert manifest["source_format"] == source
    assert state.active_scenario_source_hash == manifest["content_hash"]


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
    assert pending.active_scenario_source_hash == ""
    assert pending.timeline_id == "before"
    assert group_state.load_page_image("choice", 1) == b"old-image"

    replies: list[str] = []

    async def send(text: str) -> None:
        replies.append(text)

    asyncio.run(uploads.resolve_pdf_upload_choice("choice", choice, send, user_id="kp"))
    state = group_state.load_state("choice")
    assert state.pending_pdf_upload is None
    assert state.scenario_title == ("新劇本" if source == "pdf" else "new")
    assert state.active_scenario_source_hash == scenario_library.read_source(
        state.scenario_library_id,
    )[0]["content_hash"]
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
        assert state.openai_previous_response_id == state.openai_previous_response_timeline_id == ""
        assert state.current_map_page == state.current_room_id == state.party_facing == {}
        assert [card["name"] for card in state.pregens] == ["新候選"]
    else:
        assert state.timeline_id == "before"
        assert "player" in state.pending_checks and "other" in state.pending_luck_decisions
        assert "old" in state.deterministic_check_results
        assert state.resolved_check_events[0]["event_id"] == "old-result"
        assert "old" in state.check_consequence_origins and "old" in state.check_consequence_receipts
        assert state.kp_ooc_log and state.game_started
        assert state.openai_previous_response_id == "old-provider-response"
        assert state.openai_previous_response_timeline_id == "before"
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
    assert state.active_scenario_source_hash == scenario_library.read_source(
        scenario_id,
    )[0]["content_hash"]
    assert state.pending_checks == state.pending_luck_decisions == {}
    assert state.deterministic_check_results == {} and state.resolved_check_events == []
    assert "old" in state.check_consequence_origins and "old" in state.check_consequence_receipts
    assert state.log[0]["content"] == "舊敘事"
    assert state.kp_ooc_log and state.game_started
    assert state.openai_previous_response_id == state.openai_previous_response_timeline_id == ""
    assert state.characters["player"].name == "現有調查員"
    assert [card["name"] for card in state.pregens] == ["新候選"]
    assert state.current_map_page == {"player": "1"}
    assert state.current_room_id == {"player": "study"}
    assert state.party_facing == {"player": "E"}
    assert group_state.load_page_image("switch", 1) is None


def test_same_id_repair_rebinds_active_source_without_changing_timeline(
    storage: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def publish(text: str) -> str:
        return scenario_library.save_markdown_scenario(
            text.encode(), title="同一劇本", filename="scenario_same.md",
            preview=text, text=text, indexes={"npcs": [], "locations": []},
            pregens=[], scenario_id="same-source",
        )

    scenario_id = publish("--- 第 1 頁 ---\n版本一")
    first = asyncio.run(scenario_lifecycle.submit_published_scenario(
        "repair-source", scenario_id, source_format="markdown",
    ))
    active_v1 = group_state.load_state("repair-source")
    hash_v1 = scenario_library.read_source(scenario_id)[0]["content_hash"]
    assert first.outcome == "activated"
    assert active_v1.scenario_library_id == scenario_id
    assert active_v1.active_scenario_source_hash == hash_v1

    publish("--- 第 1 頁 ---\n版本二")
    hash_v2 = scenario_library.read_source(scenario_id)[0]["content_hash"]
    assert hash_v1 != hash_v2
    staged = asyncio.run(scenario_lifecycle.submit_published_scenario(
        "repair-source", scenario_id, source_format="markdown",
    ))
    pending = group_state.load_state("repair-source")
    assert staged.outcome == "pending"
    assert pending.active_scenario_source_hash == hash_v1

    stale = asyncio.run(scenario_lifecycle.activate_existing_scenario(
        "repair-source", scenario_id, authorized=lambda _state: True,
        expected_revision=pending.state_revision - 1,
    ))
    assert stale.outcome == "stale"
    assert group_state.load_state("repair-source").active_scenario_source_hash == hash_v1

    with monkeypatch.context() as failure:
        failure.setattr(
            manual_pregens, "install_pool",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("card install failed")),
        )
        with pytest.raises(OSError, match="card install failed"):
            asyncio.run(scenario_lifecycle.resolve_pending_submission("repair-source", "fix"))
    failed = group_state.load_state("repair-source")
    assert failed.active_scenario_source_hash == hash_v1
    assert failed.pending_pdf_upload is not None

    repaired = asyncio.run(scenario_lifecycle.resolve_pending_submission("repair-source", "fix"))
    active_v2 = group_state.load_state("repair-source")
    assert repaired.outcome == "activated"
    assert active_v2.scenario_library_id == scenario_id
    assert active_v2.timeline_id == active_v1.timeline_id
    assert active_v2.active_scenario_source_hash == hash_v2
    assert active_v2.scenario_text != active_v1.scenario_text


def test_reparse_keeps_active_hash_until_pending_choice_is_committed(
    storage: None, extraction: None,
) -> None:
    old_text = "--- 第 1 頁 ---\n新劇本舊"
    scenario_id = scenario_library.save_scenario(
        b"%PDF-old", title="新劇本", filename="scenario.pdf", preview=old_text,
        text=old_text, indexes={"npcs": [], "locations": []}, pregens=[],
        page_maps={}, page_images={}, scenario_id="reparse-source",
    )
    first = asyncio.run(scenario_lifecycle.submit_published_scenario(
        "reparse-binding", scenario_id, source_format="pdf",
    ))
    old_hash = group_state.load_state("reparse-binding").active_scenario_source_hash
    assert first.outcome == "activated" and old_hash
    staged = asyncio.run(scenario_lifecycle.stage_similar_pdf(
        "reparse-binding", b"%PDF-new", "scenario.pdf", "新劇本",
        [{"id": scenario_id, "title": "新劇本", "score": 1.0}],
    ))
    assert staged.outcome == "pending"

    async def send(_text: str) -> None:
        pass

    parsed = asyncio.run(scenario_lifecycle.reparse_pending_scenario(
        "reparse-binding", authorized=lambda _state: True,
        submit_pdf=scenario_ingestion.handle_pdf_upload, reply=send, push=send,
    ))
    pending = group_state.load_state("reparse-binding")
    assert parsed.outcome == "reparsed" and pending.pending_pdf_upload is not None
    assert pending.scenario_library_id == scenario_id
    assert pending.active_scenario_source_hash == old_hash

    repaired = asyncio.run(scenario_lifecycle.resolve_pending_submission("reparse-binding", "fix"))
    active = group_state.load_state("reparse-binding")
    assert repaired.outcome == "activated"
    assert active.scenario_library_id == scenario_id
    assert active.active_scenario_source_hash != old_hash
    assert active.active_scenario_source_hash == scenario_library.read_source(scenario_id)[0]["content_hash"]


def test_new_upload_rebinds_active_hash_with_new_timeline(storage: None) -> None:
    def publish(title: str, text: str) -> str:
        return scenario_library.save_markdown_scenario(
            text.encode(), title=title, filename="scenario_new.md", preview=text,
            text=text, indexes={"npcs": [], "locations": []}, pregens=[],
        )

    first_id = publish("第一劇本", "--- 第 1 頁 ---\n第一劇本")
    assert asyncio.run(scenario_lifecycle.submit_published_scenario(
        "new-binding", first_id, source_format="markdown",
    )).outcome == "activated"
    old = group_state.load_state("new-binding")
    second_id = publish("第二劇本", "--- 第 1 頁 ---\n第二劇本")
    assert asyncio.run(scenario_lifecycle.submit_published_scenario(
        "new-binding", second_id, source_format="markdown",
    )).outcome == "pending"
    assert group_state.load_state("new-binding").active_scenario_source_hash == old.active_scenario_source_hash

    assert asyncio.run(scenario_lifecycle.resolve_pending_submission(
        "new-binding", "new",
    )).outcome == "activated"
    new = group_state.load_state("new-binding")
    assert new.scenario_library_id == second_id and new.timeline_id != old.timeline_id
    assert new.active_scenario_source_hash != old.active_scenario_source_hash
    assert new.active_scenario_source_hash == scenario_library.read_source(second_id)[0]["content_hash"]

    assert asyncio.run(scenario_lifecycle.activate_existing_scenario(
        "new-binding", first_id, authorized=lambda _state: True,
    )).outcome == "activated"
    reused = group_state.load_state("new-binding")
    assert reused.scenario_library_id == first_id and reused.timeline_id != new.timeline_id
    assert reused.active_scenario_source_hash == old.active_scenario_source_hash


def test_legacy_library_without_source_hash_remains_unbound(storage: None) -> None:
    text = "--- 第 1 頁 ---\n舊格式來源"
    scenario_id = scenario_library.save_markdown_scenario(
        text.encode(), title="舊格式", filename="scenario_legacy.md", preview=text,
        text=text, indexes={"npcs": [], "locations": []}, pregens=[],
    )
    manifest_path = scenario_library.scenario_path(scenario_id) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("content_hash")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = asyncio.run(scenario_lifecycle.activate_existing_scenario(
        "legacy-library", scenario_id, authorized=lambda _state: True,
    ))
    state = group_state.load_state("legacy-library")
    assert result.outcome == "activated"
    assert state.scenario_text == text
    assert state.active_scenario_source_hash == ""


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


def test_rejected_identical_similarity_submission_keeps_existing_pending_bytes(
    storage: None,
) -> None:
    payload = b"same-pdf-source"
    matches = [{"id": "existing", "title": "Existing", "score": 0.9}]
    first = asyncio.run(scenario_lifecycle.stage_similar_pdf(
        "same-content", payload, "first.pdf", "First", matches,
    ))
    assert first.outcome == "pending"
    pending = group_state.load_state("same-content").pending_scenario_upload
    assert pending is not None

    second = asyncio.run(scenario_lifecycle.stage_similar_pdf(
        "same-content", payload, "second.pdf", "Second", matches,
    ))

    assert second.reason == "similar_pending"
    assert group_state.load_state("same-content").pending_scenario_upload == pending
    assert scenario_library.read_staged_upload(pending["key"]) == payload


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
    newer_same_bytes = scenario_library.stage_upload(b"first-part")
    assert newer_same_bytes != first
    _seed("newer-merge")
    newer_state = group_state.load_state("newer-merge")
    newer_state.staged_pdf_parts = [{"key": newer_same_bytes, "file_name": "newer.pdf"}]
    group_state.save_state(newer_state)
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
    assert group_state.load_state("newer-merge").staged_pdf_parts[0]["key"] == newer_same_bytes
    assert scenario_library.read_staged_upload(newer_same_bytes) == b"first-part"
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


def test_merged_pdf_cleanup_commit_failure_preserves_retryable_staging(
    storage: None, extraction: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed("merge-commit")
    first = scenario_library.stage_upload(b"first-part")
    second = scenario_library.stage_upload(b"second-part")
    state = group_state.load_state("merge-commit")
    state.staged_pdf_parts = [
        {"key": first, "file_name": "first.pdf"},
        {"key": second, "file_name": "second.pdf"},
    ]
    group_state.save_state(state)
    monkeypatch.setattr(pdf_loader, "combine_pdfs", lambda _parts: b"%PDF")
    commit = state_transaction.commit_snapshot
    commits = 0

    def fail_cleanup(*args, **kwargs):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise OSError("staging cleanup commit failed")
        return commit(*args, **kwargs)

    monkeypatch.setattr(state_transaction, "commit_snapshot", fail_cleanup)

    async def send(_text: str) -> None:
        pass

    asyncio.run(system.handle_system_command(
        "merge-commit", "kp", send, None, None, None,
        ["/coc", "scenario", "merge", first[:12], second[:12]],
    ))

    latest = group_state.load_state("merge-commit")
    assert latest.scenario_library_id
    assert len(latest.staged_pdf_parts) == 2
    assert scenario_library.read_staged_upload(first) == b"first-part"
    assert scenario_library.read_staged_upload(second) == b"second-part"
