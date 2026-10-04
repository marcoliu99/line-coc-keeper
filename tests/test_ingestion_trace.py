"""The upload orchestration still does what it did before ``legacy_commands`` was retired (L5).

``tests/fixtures/ingestion_trace.json`` was recorded by ``tests/ingestion_trace.py``
on the last commit that still contained ``app/legacy_commands.py``. The same
script runs here against ``app/services/scenario_ingestion.py`` and
``app/services/map_service.py``; the call order, arguments, messages, warnings
and resulting state must match exactly. Regenerate the file only for an
intentional behaviour change.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests import ingestion_trace

GOLDEN = Path(__file__).parent / "fixtures" / "ingestion_trace.json"


def test_upload_flows_reproduce_the_trace_recorded_before_the_module_was_retired():
    recorded = json.loads(GOLDEN.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(ingestion_trace.record(), ensure_ascii=False, sort_keys=True, default=str))
    assert current == recorded


def test_the_golden_trace_covers_each_upload_path():
    recorded = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(recorded) == {"first_upload", "similar_reupload", "role_sheet", "map_upload"}
    first = recorded["first_upload"]
    assert [call[0] for call in first["calls"]][:2] == ["extract_text", "guess_title"]
    assert any("預製調查員" in message for message in first["messages"])
    assert recorded["similar_reupload"]["state"]["pending_scenario_upload"]["key"] == "staged-key"
