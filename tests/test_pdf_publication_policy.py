"""Source-safe scenarios publish while unsafe derived maps stay quarantined."""
from types import SimpleNamespace

import pymupdf
import pytest

from app import config, pdf_loader, scenario_library
from app.providers import registry


@pytest.fixture
def safe_map_pdf(monkeypatch):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox((30, 30, 550, 700), 'FLOOR PLAN Entrance\n' + 'The original source describes a safe route. ' * 12)
        for index in range(8):
            page.draw_rect((40 + index * 10, 710, 45 + index * 10, 720))
        raw = doc.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    return raw


def test_invalid_map_publishes_safe_source_with_warnings(safe_map_pdf, monkeypatch, tmp_path):
    graph = {'page_type': 'map', 'description': 'Private map candidate', 'entry_room_id': 'missing',
             'rooms': [{'id': 'room', 'name': 'Entrance', 'exits': []}]}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_args, **_options: graph))
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(safe_map_pdf, quality_report=report)
    assert 'The original source' in text
    assert 'Private map candidate' not in text
    assert report['pages'][0]['disposition'] in {'accepted', 'legacy_route'}
    assert report['pages'][0]['publication_severity'] == 'SOFT_REVIEW'
    assert report['pages'][0]['map_analysis']['status'] == 'MAP_GRAPH_INVALID'
    assert report['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert report['soft_review_pages'] == [1]
    assert report['blocked_pages'] == report['hard_block_pages'] == []
    assert maps == {}
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    sid = scenario_library.save_scenario(safe_map_pdf, title='Safe source', filename='map.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=report)
    context = scenario_library.load_context(sid)
    assert context['scene_maps'] == {}
    assert 'The original source' in context['text']


@pytest.mark.parametrize('soft_disposition', [False, True])
def test_soft_map_page_resumes_without_retrying_or_trusting_candidate(safe_map_pdf, monkeypatch, tmp_path, soft_disposition):
    from app import pdf_ingestion_drafts as drafts

    graph = {'page_type': 'map', 'description': 'Private candidate', 'entry_room_id': 'missing',
             'rooms': [{'id': 'room', 'name': 'Entrance'}]}
    calls = []
    def analyze(*_args, **_options):
        calls.append('provider')
        return graph
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=analyze))
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    report = {}
    result = pdf_loader.extract_text(safe_map_pdf, quality_report=report)
    lease = drafts.reserve('soft-resume', safe_map_pdf, 'map.pdf')
    saved = drafts.checkpoint(lease, report, result)
    if soft_disposition:
        saved['pages']['1']['report']['disposition'] = 'soft_review'
    cached = drafts.resume_pages(saved, report['extraction_identity'])
    assert list(cached) == [1]
    assert cached[1]['map'] is None
    initial_calls = len(calls)
    final = {}
    resumed = pdf_loader.extract_text(safe_map_pdf, quality_report=final, resume_pages=cached)
    assert len(calls) == initial_calls
    assert final['pages'][0]['resumed'] is True
    assert final['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert final['map_status'] == {'1': 'MAP_GRAPH_INVALID'}
    assert resumed[0] == result[0]
    assert resumed[4] == {}


@pytest.mark.parametrize(('failed_kind', 'expected_status'), [
    ('incomplete', 'MAP_GRAPH_INCOMPLETE'), ('provider_failed', 'MAP_ANALYSIS_FAILED'),
    ('audit_failed', 'MAP_GRAPH_INCOMPLETE'), ('graph_missing', 'MAP_GRAPH_MISSING')])
def test_derived_map_failure_alone_never_blocks_source(safe_map_pdf, monkeypatch, tmp_path, map_evidence_provider, failed_kind, expected_status):
    if failed_kind in {'incomplete', 'audit_failed'}:
        map_evidence_provider(final_error='missing' if failed_kind == 'incomplete' else 'unavailable')
    else:
        response = None if failed_kind == 'provider_failed' else {'page_type': 'map', 'locations': []}
        monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                            SimpleNamespace(analyze_image=lambda *_args, **_options: response))
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(safe_map_pdf, quality_report=report)
    assert report['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert report['map_status'] == {'1': expected_status}
    assert report['blocked_pages'] == []
    assert maps == {}
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    sid = scenario_library.save_scenario(safe_map_pdf, title='Warnings', filename='map.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=report)
    assert scenario_library.load_context(sid)['scene_maps'] == {}


def test_important_image_only_source_without_independent_agreement_still_hard_blocks(monkeypatch, tmp_path):
    from app import pdf_ocr

    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((30, 50), 'STR 60 DEX 55 Damage 1d6+2')
        png = page.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_image(page.rect, stream=png)
        raw = doc.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_args, **_options: None)
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec',
        'candidate': 'STR 60 DEX 55 Damage 1d6+2', 'status': 'candidate'})
    monkeypatch.setattr(pdf_loader, '_ocr_image', lambda *_: '')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_options: {'page_type': 'character_sheet', 'text': 'STR 50 DEX 55 Damage 1d6+2'}))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired) as pending:
        pdf_loader.extract_text(raw, quality_report=report)
    assert report['scenario_readiness'] == 'BLOCKED'
    assert report['blocked_pages'] == report['hard_block_pages'] == [1]
    assert report['soft_review_pages'] == []
    assert report['pages'][0]['publication_severity'] == 'HARD_BLOCK'
    assert 'source_image_transcription_unverified' in report['pages'][0]['source_blocking_reasons']
    assert report['pages'][0]['image_transcription']['reason'] == 'independent_evidence_conflict'
    assert 'STR 60' not in pending.value.result[0]
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    with pytest.raises(ValueError, match='unresolved'):
        scenario_library.save_scenario(raw, title='Unsafe', filename='source.pdf', preview='',
            text=pending.value.result[0], indexes={}, pregens=[], page_images={}, page_maps={}, parse_quality=report)
    assert scenario_library.list_scenarios() == []


@pytest.mark.parametrize('feature_warning', [None, 'optional_pregen_unavailable',
                                           'optional_handout_unavailable', 'topology_assistance_unavailable'])
@pytest.mark.parametrize('post_commit_error', [None, 'cleanup', 'notice'])
def test_first_upload_with_thirty_safe_pages_and_invalid_map_can_start(safe_map_pdf, monkeypatch, tmp_path, map_evidence_provider, post_commit_error, feature_warning):
    import asyncio
    import json

    from app import db
    from app import legacy_commands as commands
    from app.repositories import group_state

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    db._ensure_tables()
    monkeypatch.setattr(group_state, 'DATA_DIR', tmp_path / 'images')
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    map_evidence_provider(phase_error='invalid')
    if feature_warning:
        original_extract = pdf_loader.extract_text
        def extract_with_warning(*args, **kwargs):
            result = original_extract(*args, **kwargs)
            kwargs['quality_report'].setdefault('feature_warnings', []).append(feature_warning)
            return result
        monkeypatch.setattr(pdf_loader, 'extract_text', extract_with_warning)

    registry.ANALYSIS_PROVIDERS[config.ANALYSIS_PROVIDER].analyze_text = lambda *_args, **_options: None
    with pymupdf.open(stream=safe_map_pdf, filetype='pdf') as doc:
        for _ in range(30):
            page = doc.new_page()
            page.insert_textbox((30, 30, 550, 700), 'Playable original narrative. ' * 15)
        raw = doc.tobytes()
    messages = []
    async def reply(message):
        messages.append(str(message))
    if post_commit_error:
        def fail(*_args, **_kwargs):
            raise OSError('private-post-commit-marker')
        if post_commit_error == 'cleanup':
            from app import pdf_ingestion_drafts
            monkeypatch.setattr(pdf_ingestion_drafts, 'discard_owned', fail)
        else:
            monkeypatch.setattr(commands.scenario_templates, 'preference_notice', fail)
        with pytest.raises(OSError):
            asyncio.run(commands.handle_pdf_upload('new-soft-scenario', reply, reply, raw,
                                                  'new-scenario.pdf', skip_similarity=True))
        installed = group_state.load_state('new-soft-scenario')
        assert installed.scenario_library_id and installed.scenario_text
        assert '已成功匯入，可以開始遊戲' in messages[-1]
        assert '尚未啟用' not in messages[-1]
        assert 'private-post-commit-marker' not in messages[-1]
        return
    assert asyncio.run(commands.handle_pdf_upload('new-soft-scenario', reply, reply, raw,
        'new-scenario.pdf', skip_similarity=True)) is True
    state = group_state.load_state('new-soft-scenario')
    assert state.scenario_library_id
    assert 'The original source' in state.scenario_text
    assert state.scene_maps == {}
    assert state.pending_pdf_upload is None
    root = scenario_library.SCENARIO_LIBRARY_DIR / state.scenario_library_id
    assert json.loads((root / 'scene_maps.json').read_text()) == {}
    quality = json.loads((root / 'parse_quality.json').read_text())
    assert quality['page_count'] == 31
    assert quality['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert quality['blocked_pages'] == []
    assert '自動地圖功能已停用' in '\n'.join(messages)
    assert '已成功匯入，可以開始遊戲' in '\n'.join(messages)
    assert '部分輔助功能不可用或需要核對' in '\n'.join(messages)
    assert '/coc start' in '\n'.join(messages)
    if feature_warning:
        explanations = {'optional_pregen_unavailable': '部分預製角色未解析',
                        'optional_handout_unavailable': '部分玩家手冊未解析',
                        'topology_assistance_unavailable': '隱藏路線自動輔助不可用'}
        assert explanations[feature_warning] in messages[-1]
        assert feature_warning not in messages[-1]

    assert '可開始遊戲' in '\n'.join(messages)
    assert not any('scenario continue' in message for message in messages)

    # Start through the existing public command with a real investigator and persisted state.
    from app.commands.handlers import system
    from app.models import Character

    state.kp_assistant_user_id = 'kp'
    state.characters['player'] = Character(name='Investigator', owner_id='player')
    group_state.save_state(state)
    monkeypatch.setitem(registry.CONVERSATION_PROVIDERS, config.LLM_PROVIDER, SimpleNamespace(
        analyze_text=lambda *_args, **_options: {'found': True, 'text': 'You arrive at the entrance.', 'page': 1}))
    asyncio.run(system.handle_system_command('new-soft-scenario', 'kp', reply, None, None, None, ['/coc', 'start']))
    started = group_state.load_state('new-soft-scenario')
    assert started.game_started is True
    assert started.scene_maps == {}


def test_published_quality_keeps_map_warnings_but_candidate_provenance_is_private(safe_map_pdf, monkeypatch, tmp_path):
    import json

    graph = {'page_type': 'map', 'description': 'Private graph detail', 'entry_room_id': 'missing',
             'rooms': [{'id': 'room', 'name': 'Entrance'}]}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_args, **_options: graph))
    report = {}
    text, _, _, images, maps = pdf_loader.extract_text(safe_map_pdf, quality_report=report)
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path)
    sid = scenario_library.save_scenario(safe_map_pdf, title='Private provenance', filename='map.pdf', preview='',
        text=text, indexes={}, pregens=[], page_images=images, page_maps=maps, parse_quality=report)
    published = json.loads((tmp_path / sid / 'parse_quality.json').read_text())
    assert published['map_status'] == {'1': 'MAP_GRAPH_INVALID'}
    assert published['soft_review_pages'] == [1]
    assert 'candidate_graph' not in published['pages'][0]['map_analysis']
    assert 'output_graph' not in published['pages'][0]['map_analysis']['attempts'][0]
    private = tmp_path / sid / '.ingestion-provenance.json'
    assert private.stat().st_mode & 0o777 == 0o600
    saved = json.loads(private.read_text())
    assert saved['pages'][0]['map_analysis']['candidate_graph'] == graph
    assert saved['pages'][0]['map_analysis']['attempts'][0]['output_evidence'] == graph
    assert report['pages'][0]['map_analysis']['candidate_graph'] == graph


def test_map_warning_does_not_hide_unresolved_source_mechanics(safe_map_pdf, monkeypatch):
    with pymupdf.open(stream=safe_map_pdf, filetype='pdf') as doc:
        page = doc[0]
        page.add_redact_annot((0, 0, 595, 700))
        page.apply_redactions()
        page.insert_text((30, 50), 'FLOOR PLAN')
        page.insert_text((30, 80), 'Alice STR')
        raw = doc.tobytes()
    graph = {'page_type': 'map', 'entry_room_id': 'missing',
             'rooms': [{'id': 'room', 'name': 'Entrance'}]}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_args, **_options: graph))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(raw, quality_report=report, local_ocr_limit=0, ai_repair_limit=0)
    assert report['map_status'] == {'1': 'MAP_GRAPH_INVALID'}
    assert report['hard_block_pages'] == [1]
    assert report['soft_review_pages'] == []
    assert report['pages'][0]['source_blocking_reasons'] == [
        'source_image_transcription_unverified', 'source_mechanics_unresolved']
    assert report['pages'][0]['derived_feature_warnings'] == ['MAP_GRAPH_INVALID']


def test_entirely_missing_playable_source_is_a_reported_hard_block(monkeypatch):
    with pymupdf.open() as doc:
        doc.new_page()
        raw = doc.tobytes()
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(raw, quality_report=report)
    assert report['scenario_readiness'] == 'BLOCKED'
    assert report['blocked_pages'] == report['hard_block_pages'] == [1]
    assert report['pages'][0]['source_blocking_reasons'] == ['canonical_playable_source_missing']
