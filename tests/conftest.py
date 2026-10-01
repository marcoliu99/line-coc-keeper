"""Isolate the suite from the checkout it runs in, before app is imported.

Two leaks, both resolved here because pytest imports this file before any test
module and therefore before `app.config` runs.

First, `app/config.py` resolves DATA_DIR, DB_PATH, BACKUP_DIR,
SCENARIO_LIBRARY_DIR and IMPORT_DIR at import time, relative to the current
working directory, and creates them. Tests write real rows through the real
repositories, so without this the suite reads and writes `data/coc_bot.db` of
whatever checkout it runs in — the live bot's database when run from the
deployment worktree. It also made reruns fail: a test that saved a state at
revision 0 found revision 1 waiting on the second run and raised
StateRevisionConflict.

Second, `app/config.py` calls `load_dotenv()`, so any `.env` beside the
checkout is applied to every setting the suite then asserts. Tests that pin a
code default — MAX_TOOL_ITERATIONS is 5, HIGH_ITERATION_WATERMARK is 4 — fail
in exactly the checkouts that have a deployment `.env`, which includes the one
the bot is run from. Eight test modules already worked around this with
`sys.modules.setdefault("dotenv", ...)`, but setdefault only wins when that
module happens to be imported first, so whether the suite passed depended on
collection order.
"""
import atexit
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import dotenv

# Everything below only works ahead of app.config's own import-time work.
if "app.config" in sys.modules:  # pragma: no cover - import-order guard
    raise RuntimeError("app.config was imported before conftest could isolate it")


def _load_dotenv_disabled(*_args, **_kwargs) -> bool:
    """Ignore a checkout's .env: tests assert code defaults, not deployment."""
    return False


# Only this one function is replaced, ahead of app.config's
# `from dotenv import load_dotenv`. scripts/bot_lifecycle.py reads
# dotenv_values and is covered by tests, so the module must stay intact.
dotenv.load_dotenv = _load_dotenv_disabled

_ROOT = Path(tempfile.mkdtemp(prefix="coc-tests-")).resolve()

# Set explicitly rather than relying on four of them being derived from
# DATA_DIR, so a later change to that derivation cannot quietly send writes
# back to the working directory.
os.environ["DATA_DIR"] = str(_ROOT / "groups")
os.environ["DB_PATH"] = str(_ROOT / "coc_bot.db")
os.environ["BACKUP_DIR"] = str(_ROOT / "backups")
os.environ["SCENARIO_LIBRARY_DIR"] = str(_ROOT / "scenarios")
os.environ["IMPORT_DIR"] = str(_ROOT / "imports")

atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)


def pytest_report_header(config):
    return f"storage sandbox: {_ROOT}\ndotenv: disabled (tests assert code defaults)"


# Failed imports intentionally survive the caller. Keep these durable files
# function-scoped, just as tests isolate their fake conversation repositories.
import pytest  # noqa: E402 - storage environment must precede app imports.


@pytest.fixture(autouse=True)
def isolated_pdf_import_drafts(monkeypatch, tmp_path):
    from app import pdf_ingestion_drafts
    monkeypatch.setattr(pdf_ingestion_drafts, 'SCENARIO_LIBRARY_DIR', tmp_path / 'pdf-import-library')


@pytest.fixture
def certified_map_result():
    """Stub the image boundary in routing tests; certification has its own provider tests."""
    from app import pdf_map_analysis, scene_map

    def result(description, graph, image=b'png'):
        assert not scene_map.validate_scene_map(graph)
        record = pdf_map_analysis.not_analyzed()
        record.update(status='MAP_GRAPH_VERIFIED', initial_status='MAP_GRAPH_VERIFIED',
                      verified=True, graph_generated=True, analysis_attempted=True,
                      image_sha256=hashlib.sha256(image).hexdigest(),
                      graph_sha256=pdf_map_analysis.graph_hash(graph), candidate_graph=graph)
        evidence = {'complete': True, 'uncertainties': [],
            'visible_locations': [{'label': room['name'], 'room_id': room['id']} for room in graph['rooms']],
            'rooms': [{'room_id': room['id'], 'verdict': 'supported', 'evidence': 'Known fixture room'} for room in graph['rooms']],
            'edges': [{'edge_id': f'{room["id"]}:{i}', 'verdict': 'supported', 'basis': 'door',
                       'evidence': 'Known fixture door'} for room in graph['rooms'] for i, _ in enumerate(room.get('exits', []))],
            'entry': {'room_id': graph.get('entry_room_id', ''),
                      'verdict': 'supported' if graph.get('entry_room_id') else 'not_visible', 'evidence': 'Known fixture entry'}}
        record['image_evidence'] = [evidence]
        record['attempts'] = [{'stage': 'image_audit', 'input_graph_sha256': record['graph_sha256'],
                               'image_sha256': record['image_sha256'], 'output_evidence': evidence}]
        return pdf_map_analysis.MapResult(description, graph, record)
    return result
