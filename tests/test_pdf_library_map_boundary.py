"""Library reads quarantine legacy/tampered maps without disabling source."""
import json

import pymupdf
import pytest

from app import pdf_loader, scenario_activation, scenario_library
from app.models import GroupState


@pytest.mark.parametrize('entry', ['missing', 'room'])
def test_legacy_maps_never_install_but_source_still_loads(tmp_path, monkeypatch, entry):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((30, 80), 'Original playable narrative.')
        raw = doc.tobytes()
    sid = scenario_library.save_scenario(raw, title='Legacy', filename='legacy.pdf', preview='',
        text='[PAGE 1]\nOriginal playable narrative.', indexes={}, pregens=[], page_maps={}, page_images={})
    graph = {'entry_room_id': entry, 'rooms': [{'id': 'room', 'name': 'Entrance', 'exits': []}]}
    (tmp_path / sid / 'scene_maps.json').write_text(json.dumps({'1': graph}))
    context = scenario_library.load_context(sid)
    state = GroupState(group_id='read-boundary')
    scenario_activation.install_context_fields(state, sid, context)
    assert context['scene_maps'] == state.scene_maps == {}
    assert 'Original playable narrative.' in state.scenario_text


def test_current_certificate_installs_then_tampering_quarantines(tmp_path, monkeypatch, map_evidence_provider):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    map_evidence_provider()
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox((30, 30, 550, 700), 'FLOOR PLAN\n' + 'Original playable source. ' * 16)
        for index in range(8):
            page.draw_rect((40 + index * 10, 710, 45 + index * 10, 720))
        raw = doc.tobytes()
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(raw, quality_report=report)
    assert maps
    sid = scenario_library.save_scenario(raw, title='Certified', filename='certified.pdf', preview='',
        text=text, indexes={}, pregens=[], page_maps=maps, page_images=images, parse_quality=report)
    context = scenario_library.load_context(sid)
    state = GroupState(group_id='certified-boundary')
    scenario_activation.install_context_fields(state, sid, context)
    assert state.scene_maps == {'1': maps[1]}
    graph = maps[1]
    graph['entry_room_id'] = 'missing'
    (tmp_path / sid / 'scene_maps.json').write_text(json.dumps({'1': graph}))
    context = scenario_library.load_context(sid)
    scenario_activation.install_context_fields(state, sid, context)
    assert state.scene_maps == {}
    assert 'Original playable source.' in state.scenario_text
