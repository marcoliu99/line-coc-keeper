"""Record what the scenario-upload orchestration does, in a form two checkouts can be compared by.

These functions ran from ``app/legacy_commands.py`` until it was retired; they
now live in ``app/services/scenario_ingestion.py`` and ``app/services/map_service.py``.
This module feeds them fixed fake extractors and a throwaway library and
returns a JSON-able trace: every call made to a collaborator (name and
arguments), every message sent, and the parts of the state that matter.
``tests/fixtures/ingestion_trace.json`` was recorded by running exactly these
flows against the last commit that still had ``legacy_commands`` (the only
difference was which module the two entry points were looked up in); the test
asserts the current code reproduces it exactly.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app import (
    db,
    pdf_loader,
    pregen_extractor,
    scenario_activation,
    scenario_index,
    scenario_library,
)
from app.models import GroupState
from app.repositories import group_state, state_transaction
from app.services import map_service, scenario_ingestion


def _stable(text: str) -> str:
    """Blank what differs on every run: asset ids, timestamps and the throwaway library path."""
    text = re.sub(r"\b[0-9a-f]{32}\b", "<id>", text)
    text = re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+\+00:00", "<ts>", text)
    return re.sub(r"PosixPath\('[^']*/library", "PosixPath('<library>", text)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []
        self.messages: list[str] = []

    def wrap(self, name: str, result: Any) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append([name, _stable(repr(args)), _stable(repr(sorted(kwargs.items())))])
            return result(*args, **kwargs) if callable(result) else result
        return call

    async def message(self, text: str) -> None:
        # Asset ids are random; everything else in a message is part of the trace.
        self.messages.append(_stable(text))


def _state_view(group: str) -> dict[str, Any]:
    state = group_state.load_state(group)
    return {
        "scenario_title": state.scenario_title,
        "scenario_text": state.scenario_text,
        "scenario_library_id": state.scenario_library_id,
        "log": len(state.log),
        "pending_scenario_upload": state.pending_scenario_upload,
        "pending_pdf_upload": state.pending_pdf_upload,
        "pregens": [p.get("name") for p in state.pregens],
        "autoroll_checks": state.autoroll_checks,
        "pending_checks": sorted(state.pending_checks),
    }


def _seed(group: str, **fields: Any) -> None:
    state = GroupState(group_id=group, **fields)
    state_transaction.mutate_value(group, lambda ctx: ctx.replace_state(state), reason="trace_seed")


def _flow_first_upload(ingestion: Any, library_dir: Path) -> dict[str, Any]:
    recorder = Recorder()
    _seed("trace-first")
    with patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", library_dir), \
            patch.object(pdf_loader, "extract_text", recorder.wrap("extract_text", ("第一章 書房。\n書房裡有一本日記。", [], False, {}, {}))), \
            patch.object(pdf_loader, "guess_title", recorder.wrap("guess_title", "Trace Scenario")), \
            patch.object(pdf_loader, "extract_preview", recorder.wrap("extract_preview", "preview")), \
            patch.object(scenario_index, "extract_scenario_index", recorder.wrap("extract_scenario_index", {"npcs": [], "locations": []})), \
            patch.object(pregen_extractor, "extract_pregens", recorder.wrap("extract_pregens", [])), \
            patch.object(scenario_activation, "refresh_after_commit", recorder.wrap("refresh_after_commit", True)):
        accepted = asyncio.run(ingestion.handle_pdf_upload(
            "trace-first", recorder.message, recorder.message, b"%PDF", "scenario.pdf", skip_similarity=True,
        ))
    return {"accepted": accepted, "calls": recorder.calls, "messages": recorder.messages, "state": _state_view("trace-first")}


def _flow_similar_reupload(ingestion: Any, library_dir: Path) -> dict[str, Any]:
    recorder = Recorder()
    _seed("trace-similar", scenario_title="Existing", scenario_text="舊劇本", scenario_library_id="existing-scenario")
    with patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", library_dir), \
            patch.object(pdf_loader, "extract_preview", recorder.wrap("extract_preview", "preview text")), \
            patch.object(pdf_loader, "guess_title", recorder.wrap("guess_title", "Trace Scenario")), \
            patch.object(scenario_library, "find_similar", recorder.wrap(
                "find_similar", [{"id": "existing-scenario", "title": "Existing", "score": 0.9}])), \
            patch.object(scenario_library, "stage_upload", recorder.wrap("stage_upload", "staged-key")):
        accepted = asyncio.run(ingestion.handle_pdf_upload(
            "trace-similar", recorder.message, recorder.message, b"%PDF", "scenario.pdf",
        ))
    return {"accepted": accepted, "calls": recorder.calls, "messages": recorder.messages, "state": _state_view("trace-similar")}


def _flow_role_sheet(ingestion: Any, library_dir: Path) -> dict[str, Any]:
    recorder = Recorder()
    card = {"name": "林文", "occupation": "記者", "str_": 65, "skills": {"圖書館使用": 70}, "source": "manual"}
    _seed("trace-role", scenario_library_id="trace-scenario")
    context = {
        "manifest": {"title": "trace-scenario", "content_hash": "hash-1"},
        "text": "scenario", "active_chapter_id": "chapter-1", "context_chapter_ids": ["chapter-1"],
        "indexes": {"npcs": [], "locations": []}, "pregens": [], "scene_maps": {}, "page_numbers": set(),
    }
    with patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", library_dir), \
            patch.object(pregen_extractor, "parse_role_sheet_text", recorder.wrap("parse_role_sheet_text", card)), \
            patch.object(scenario_library, "load_context", recorder.wrap("load_context", context)):
        asyncio.run(ingestion.handle_role_sheet_upload("trace-role", recorder.message, "角色卡內文", "role_lin.md"))
    state = group_state.load_state("trace-role")
    return {
        "calls": recorder.calls, "messages": recorder.messages,
        "pregens": [(p.get("name"), p.get("str_"), p.get("source")) for p in state.pregens],
    }


def _flow_map_upload(mapping: Any) -> dict[str, Any]:
    recorder = Recorder()
    _seed("trace-map", active=True)
    yaml_bytes = (
        "location_name: 書房\nrooms:\n  - id: study\n    name: 書房\n    exits: []\n"
    ).encode()
    asyncio.run(mapping.handle_map_upload("trace-map", recorder.message, recorder.message, yaml_bytes, "map_study.yaml"))
    state = group_state.load_state("trace-map")
    return {"messages": recorder.messages, "scene_maps": sorted(state.scene_maps)}


def record() -> dict[str, Any]:
    ingestion, mapping = scenario_ingestion, map_service
    with tempfile.TemporaryDirectory() as temp:
        library_dir = Path(temp) / "library"
        db._ensure_tables()
        return {
            "first_upload": _flow_first_upload(ingestion, library_dir),
            "similar_reupload": _flow_similar_reupload(ingestion, library_dir),
            "role_sheet": _flow_role_sheet(ingestion, library_dir),
            "map_upload": _flow_map_upload(mapping),
        }


if __name__ == "__main__":
    print(json.dumps(record(), ensure_ascii=False, indent=2, sort_keys=True, default=str))
