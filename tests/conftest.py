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
    from app import pdf_map_analysis, pdf_map_evidence, scene_map

    def result(description, graph, image=b'png'):
        assert not scene_map.validate_scene_map(graph)
        inventory, errors = pdf_map_evidence.merge_inventory([], [
            {'label': room.get('visible_label', room['name']), 'floor_or_section': 'ground', 'id_hint': room['id'],
             'visible': True, 'evidence': 'Known fixture room'} for room in graph['rooms']])
        assert not errors
        connectivity = {'entry': {'status': 'resolved', 'room_id': graph['entry_room_id'], 'evidence': 'Known fixture entry'},
            'edges': [{'id': f'edge_{i}_{j}', 'from': room['id'], 'to': edge['to'], 'compass': edge['compass'],
                       'type': 'door', 'visual_basis': 'door', 'evidence': 'Known fixture door'}
                      for i, room in enumerate(graph['rooms']) for j, edge in enumerate(room.get('exits', []))], 'missing_locations': []}
        built, errors = pdf_map_evidence.build_graph(inventory, connectivity)
        assert not errors
        graph.clear()
        graph.update(built)
        record = pdf_map_analysis.not_analyzed()
        record['inventory'] = inventory
        record['connectivity'] = connectivity
        record['inventory_audit'] = {'complete': True, 'uncertainties': [], 'confirmed_ids': [r['id'] for r in inventory], 'missing_locations': []}
        record.update(status='MAP_GRAPH_VERIFIED', initial_status='MAP_GRAPH_VERIFIED',
                      verified=True, graph_generated=True, analysis_attempted=True,
                      image_sha256=hashlib.sha256(image).hexdigest(),
                      graph_sha256=pdf_map_analysis.graph_hash(graph), candidate_graph=graph)
        evidence = {'complete': True, 'uncertainties': [],
            'visible_locations': [{'label': room['visible_label'], 'room_id': room['id']} for room in graph['rooms']],
            'rooms': [{'room_id': room['id'], 'verdict': 'supported', 'evidence': 'Known fixture room'} for room in graph['rooms']],
            'edges': [{'edge_id': f'{room["id"]}:{i}', 'verdict': 'supported', 'basis': 'door',
                       'evidence': 'Known fixture door'} for room in graph['rooms'] for i, _ in enumerate(room.get('exits', []))],
            'entry': {'room_id': graph.get('entry_room_id', ''),
                      'verdict': 'supported' if graph.get('entry_room_id') else 'not_visible', 'evidence': 'Known fixture entry'}}
        record['image_evidence'] = [evidence]
        raw = [{'label': r['label'], 'floor_or_section': r['floor_or_section'], 'id_hint': r['id_hints'][0],
                'visible': True, 'evidence': r['evidence']} for r in inventory]
        record['attempts'] = [
            {'stage': 'phase1_generation', 'image_sha256': record['image_sha256'], 'output_evidence': {'page_type': 'map', 'locations': raw}},
            {'stage': 'phase1_audit', 'image_sha256': record['image_sha256'], 'output_evidence': record['inventory_audit']},
            {'stage': 'phase2_generation', 'image_sha256': record['image_sha256'], 'output_evidence': connectivity},
            {'stage': 'image_audit', 'input_graph_sha256': record['graph_sha256'],
                               'image_sha256': record['image_sha256'], 'output_evidence': evidence}]
        return pdf_map_analysis.MapResult(description, graph, record)
    return result


@pytest.fixture
def map_evidence_provider(monkeypatch):
    """Exercise production phase schemas while stubbing only external image inference."""
    import json
    from types import SimpleNamespace

    from app import config
    from app.providers import registry

    def install(*, entry='resolved', final_error=None, phase_error=None):
        calls = []
        def analyze(_png, tool, prompt, **_options):
            name = tool['name']
            calls.append(name)
            location = {'label': 'Entrance', 'floor_or_section': 'ground', 'id_hint': 'door',
                        'visible': True, 'kind': 'location', 'evidence': 'Visible entrance label'}
            if name == 'inventory_map_locations':
                return {'page_type': 'map', 'description': 'A visible entrance.', 'locations': [location]}
            payload = json.loads(prompt.split('\nINPUT_JSON\n')[1])
            if name == 'audit_map_inventory':
                return {'complete': True, 'uncertainties': [], 'confirmed_ids': [r['id'] for r in payload['inventory']], 'missing_locations': []}
            if name == 'extract_map_connectivity':
                edges = [] if not phase_error else [{'id': 'bad', 'from': 'door', 'to': 'absent', 'type': 'door',
                    'visual_basis': 'wall', 'compass': 'E', 'evidence': 'Solid wall'}]
                return {'entry': {'status': entry, 'room_id': 'door' if entry == 'resolved' else '',
                                 'evidence': 'Visible entry' if entry == 'resolved' else ''}, 'edges': edges, 'missing_locations': []}
            if name == 'patch_map_evidence':
                return {'add_locations': [], 'remove_edges': [{'edge_id': 'bad', 'evidence': 'Original image solid wall', 'reason': 'No visible opening'}] if phase_error == 'repair' else [],
                        'replace_edges': [], 'add_edges': [], 'entry_update': None}
            graph = payload['graph']
            evidence = {'complete': True, 'uncertainties': [],
                'visible_locations': [{'label': r['visible_label'], 'room_id': r['id']} for r in graph['rooms']],
                'rooms': [{'room_id': r['id'], 'verdict': 'supported', 'evidence': 'Visible room'} for r in graph['rooms']],
                'edges': [{'edge_id': e['edge_id'], 'verdict': 'supported', 'basis': e['visual_basis'], 'evidence': 'Visible opening'} for e in payload['edge_inventory']],
                'entry': {'room_id': graph['entry_room_id'], 'verdict': 'supported', 'evidence': 'Visible entry'}}
            if final_error == 'missing':
                evidence['visible_locations'].append({'label': 'Lamp Room', 'room_id': ''})
            if final_error == 'unavailable':
                return None
            if final_error == 'unsupported':
                evidence['rooms'][0]['verdict'] = 'unsupported'
            return evidence
        monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(analyze_image=analyze))
        return calls
    return install
