"""Exercise the merged PDF extractor through scenario ingestion and lifecycle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pymupdf
import pytest

from app import db, locks, pdf_loader, scenario_library
from app.models import GroupState
from app.repositories import group_state
from app.services import scenario_ingestion, scenario_lifecycle


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(group_state, "DATA_DIR", tmp_path / "images")
    monkeypatch.setattr(scenario_library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
    db._ensure_tables()

    def native_chunks(source: bytes) -> dict[int, dict[str, str]]:
        with pymupdf.open(stream=source, filetype="pdf") as document:
            return {index + 1: {"text": page.get_text("text")} for index, page in enumerate(document)}

    monkeypatch.setattr(pdf_loader, "_pymupdf4llm_page_chunks", native_chunks)
    monkeypatch.setattr(
        pdf_loader.pdf_layout, "reorder_with_paddle",
        lambda *_args, **_kwargs: pdf_loader.pdf_layout.LayoutResult(reason="model_unavailable"),
    )
    monkeypatch.setattr(
        pdf_loader.pdf_ai_repair, "repair_page",
        lambda _page, _row, text, _budget: (text, {"regions": [], "unresolved_labels": []}),
    )
    monkeypatch.setattr(
        scenario_ingestion.scenario_index, "extract_scenario_index",
        lambda _text: {"npcs": [], "locations": []},
    )
    monkeypatch.setattr(scenario_ingestion.pregen_extractor, "extract_pregens", lambda _text: [])


def _pdf(*, blank: bool = False) -> bytes:
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        if not blank:
            for index in range(8):
                page.insert_text(
                    (40, 60 + index * 25),
                    f"The investigator finds an archival clue in the study, entry {index}. "
                    "The Keeper checks the scenario source before presenting it.",
                    fontsize=9,
                )
        return document.tobytes()


def _seed(group_id: str, *, existing: bool = False) -> None:
    state = GroupState(group_id=group_id, timeline_id="before")
    state.pending_checks["player"] = {"type": "skill", "timeline_id": "before"}
    state.pregens = [{"name": "old candidate"}]
    if existing:
        state.scenario_title = "Current scenario"
        state.scenario_text = "--- 第 1 頁 ---\nCurrent scenario"
        state.active = True
    group_state.save_state(state)
    group_state.save_page_image(group_id, 1, b"old-image")


async def _send(messages: list[str], text: str) -> None:
    messages.append(text)


@pytest.mark.parametrize("existing", [False, True])
def test_real_pdf_pipeline_respects_first_activation_and_existing_pending(
    storage: None, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    group_id = "pdf-existing" if existing else "pdf-first"
    _seed(group_id, existing=existing)
    monkeypatch.setattr(scenario_library, "find_similar", lambda *_args: [])
    messages: list[str] = []
    source = _pdf()

    accepted = asyncio.run(scenario_ingestion.handle_pdf_upload(
        group_id, lambda text: _send(messages, text), lambda text: _send(messages, text),
        source, "scenario_integration.pdf",
    ))

    state = group_state.load_state(group_id)
    assert accepted
    if existing:
        assert state.scenario_title == "Current scenario" and state.timeline_id == "before"
        assert state.pending_pdf_upload is not None
        candidate_id = state.pending_pdf_upload["scenario_id"]
        assert state.pending_checks and state.pregens == [{"name": "old candidate"}]
        assert group_state.load_page_image(group_id, 1) == b"old-image"
    else:
        assert state.scenario_library_id and state.active and state.timeline_id != "before"
        assert state.pending_pdf_upload is None and state.pending_scenario_upload is None
        assert state.pending_checks == {} and state.pregens == []
        assert group_state.load_page_image(group_id, 1) is None
        candidate_id = state.scenario_library_id
    context = scenario_library.load_context(candidate_id)
    assert "archival clue" in context["text"]
    assert context["manifest"]["source_format"] == "pdf"
    if existing:
        assert "全新的劇本" in messages[-1]
    else:
        assert "已載入劇本" in messages[-1]


def test_real_pdf_similarity_stages_raw_bytes_without_full_parse(
    storage: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed("pdf-similar", existing=True)
    source = _pdf()
    monkeypatch.setattr(scenario_library, "find_similar", lambda *_args: [
        {"id": "candidate", "title": "Candidate", "score": 0.95},
    ])
    messages: list[str] = []
    accepted = asyncio.run(scenario_ingestion.handle_pdf_upload(
        "pdf-similar", lambda text: _send(messages, text),
        lambda text: _send(messages, text), source, "scenario_similar.pdf",
    ))
    state = group_state.load_state("pdf-similar")
    assert not accepted and state.timeline_id == "before"
    assert state.scenario_title == "Current scenario" and state.pending_pdf_upload is None
    assert state.pending_scenario_upload is not None
    assert scenario_library.read_staged_upload(state.pending_scenario_upload["key"]) == source
    assert group_state.load_page_image("pdf-similar", 1) == b"old-image"
    assert "偵測到相似劇本" in messages[-1]


@pytest.mark.parametrize("blank", [False, True])
def test_real_pdf_reparse_applies_or_restores_after_unlocked_parse(
    storage: None, monkeypatch: pytest.MonkeyPatch, blank: bool,
) -> None:
    _seed("pdf-reparse", existing=True)
    source = _pdf(blank=blank)
    key = scenario_library.stage_upload(source)
    state = group_state.load_state("pdf-reparse")
    state.pending_scenario_upload = {"key": key, "file_name": "scenario_reparse.pdf", "matches": []}
    group_state.save_state(state)
    original_extract = pdf_loader.extract_text
    observed_unlocked = False

    def extract(source_bytes: bytes, **kwargs):
        nonlocal observed_unlocked
        observed_unlocked = not locks.get_conversation_lock("pdf-reparse").locked()
        return original_extract(source_bytes, **kwargs)

    monkeypatch.setattr(pdf_loader, "extract_text", extract)
    messages: list[str] = []
    result = asyncio.run(scenario_lifecycle.reparse_pending_scenario(
        "pdf-reparse", authorized=lambda _state: True,
        submit_pdf=scenario_ingestion.handle_pdf_upload,
        reply=lambda text: _send(messages, text), push=lambda text: _send(messages, text),
    ))
    latest = group_state.load_state("pdf-reparse")
    assert observed_unlocked and latest.scenario_title == "Current scenario"
    assert latest.timeline_id == "before" and group_state.load_page_image("pdf-reparse", 1) == b"old-image"
    if blank:
        assert result.outcome == "rejected" and latest.pending_pdf_upload is None
        assert latest.pending_scenario_upload is not None
        assert scenario_library.read_staged_upload(key) == source
        assert "讀取 PDF 失敗" in messages[-1]
    else:
        assert result.outcome == "reparsed" and latest.pending_scenario_upload is None
        assert latest.pending_pdf_upload is not None
        assert scenario_library.load_context(latest.pending_pdf_upload["scenario_id"])["text"]
        with pytest.raises(FileNotFoundError):
            scenario_library.read_staged_upload(key)


def test_real_pdf_multipart_submission_consumes_only_its_staged_parts(
    storage: None,
) -> None:
    _seed("pdf-multipart")
    part = _pdf()
    first = scenario_library.stage_upload(part)
    second = scenario_library.stage_upload(part)
    assert first != second
    state = group_state.load_state("pdf-multipart")
    state.staged_pdf_parts = [
        {"key": first, "file_name": "first.pdf"},
        {"key": second, "file_name": "second.pdf"},
    ]
    group_state.save_state(state)
    merged = pdf_loader.combine_pdfs([part, part])
    messages: list[str] = []

    accepted = asyncio.run(scenario_lifecycle.submit_merged_pdf(
        "pdf-multipart", merged, "scenario_merged.pdf",
        ((first, "first.pdf"), (second, "second.pdf")),
        scenario_ingestion.handle_pdf_upload,
        lambda text: _send(messages, text), lambda text: _send(messages, text),
    ))

    latest = group_state.load_state("pdf-multipart")
    assert accepted and latest.scenario_library_id and latest.staged_pdf_parts == []
    assert latest.timeline_id != "before" and latest.pending_pdf_upload is None
    assert scenario_library.load_context(latest.scenario_library_id)["manifest"]["page_count"] == 2
    for key in (first, second):
        with pytest.raises(FileNotFoundError):
            scenario_library.read_staged_upload(key)


def test_pdf_quality_warning_does_not_block_playable_candidate(
    storage: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed("pdf-warning")
    original = pdf_loader.pdf_quality.repair_duplicate_source_layout

    def warning(*args, **kwargs):
        text, _decision = original(*args, **kwargs)
        return text, {"status": "duplicate_source_emission_ambiguous"}

    monkeypatch.setattr(pdf_loader.pdf_quality, "repair_duplicate_source_layout", warning)
    messages: list[str] = []
    accepted = asyncio.run(scenario_ingestion.handle_pdf_upload(
        "pdf-warning", lambda text: _send(messages, text),
        lambda text: _send(messages, text), _pdf(), "scenario_warning.pdf", skip_similarity=True,
    ))
    state = group_state.load_state("pdf-warning")
    assert accepted and state.active and state.scenario_library_id
    assert state.timeline_id != "before"
    quality = json.loads((scenario_library.scenario_path(state.scenario_library_id) / "parse_quality.json").read_text())
    assert "duplicate_source_emission_ambiguous" in quality["pages"][0]["warnings"]
    assert "已載入劇本" in messages[-1]


def test_source_bound_deduplication_reaches_published_scenario(
    storage: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed("pdf-deduplicated")
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text(
            (70, 100),
            "South Dock\nThe lantern sheds 1d6+2 light.\nThe pier remains ahead.",
            fontsize=9,
        )
        native = page.get_text("text")
        source = document.tobytes()
    layout = (
        "South Dock The lantern sheds 1d6+2 light.\n\n"
        "The lantern sheds 1d6+2 light. The pier remains ahead."
    )
    monkeypatch.setattr(pdf_loader, "_pymupdf4llm_page_chunks", lambda _source: {1: {"text": layout}})
    monkeypatch.setattr(
        pdf_loader.pdf_quality, "native_text", lambda _page: (native, ["ambiguous_columns"]),
    )
    messages: list[str] = []
    accepted = asyncio.run(scenario_ingestion.handle_pdf_upload(
        "pdf-deduplicated", lambda text: _send(messages, text),
        lambda text: _send(messages, text), source, "scenario_deduplicated.pdf",
        skip_similarity=True,
    ))
    state = group_state.load_state("pdf-deduplicated")
    assert accepted and state.active and state.scenario_library_id
    context = scenario_library.load_context(state.scenario_library_id)
    assert context["text"].count("The lantern sheds 1d6+2 light.") == 1
    quality = json.loads((scenario_library.scenario_path(state.scenario_library_id) / "parse_quality.json").read_text())
    assert quality["pages"][0]["source_duplicate"]["status"] == "duplicate_source_emission_repaired"
