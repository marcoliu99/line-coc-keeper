"""Uncertainty loses authority instead of blocking an independently safe core."""
from types import SimpleNamespace

import pymupdf
import pytest

from app import config, pdf_loader
from app.providers import registry


def book():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox((40, 60, 550, 700), 'The Keeper describes the house and its occupants. ' * 12)
        page = doc.new_page()
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        image.clear_with(200)
        page.insert_image(page.rect, stream=image.tobytes('png'))
        page.insert_text((40, 60), 'Unverified asset')
        return doc.tobytes()


@pytest.mark.parametrize('role', ['unknown', 'source_bearing', 'mixed'])
def test_candidate_image_quarantined_before_gameplay_authority(monkeypatch, tmp_path, role):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_a, **_kw: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_kw: ('', []))
    result = {'page_role': role, 'contains_gameplay_source': True, 'contains_mechanics': False,
                  'contains_required_clue': False, 'asset_only': False,
                  'all_source_fragments_accounted_for': False, 'source_fragments': [],
                  'optional_source_quote': '', 'optional_source_page': 0}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
                        SimpleNamespace(analyze_image=lambda *_a, **_kw: result,
                                        analysis_model_identity=lambda: 'test'))
    report = {}
    text, *_ = pdf_loader.extract_text(book(), quality_report=report)
    assert report['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert report['hard_block_pages'] == []
    assert report['quarantined_pages'] == [2]
    assert 'Unverified asset' not in text
    assert 'The Keeper describes' in text
    assert report['pages'][1]['source_authority'] == 'QUARANTINED'
    assert report['pages'][1]['selected_text'] == ''


def test_credits_cannot_replace_quarantined_playable_core():
    from app import pdf_admission
    rows = [{'page': 1, 'candidates': {'native': 'Credits\nWritten by Example'},
             'source_blocking_reasons': [], 'disposition': 'accepted'},
            {'page': 2, 'candidates': {'native': ''}, 'requires_image_transcription': True,
             'source_blocking_reasons': ['source_image_transcription_unverified']}]
    pdf_admission.compose(['Credits\nWritten by Example', 'candidate'], rows)
    # Unsafe gameplay prevented: publishing credits with no actual scenario source.
    assert any('canonical_playable_source_missing' in row['source_blocking_reasons'] for row in rows)


def test_required_receipt_overrides_noncore_heading():
    import hashlib

    from app import pdf_admission
    quote = 'The Keeper must read the unique instruction in the attached sheet before opening the scene.'
    rows = [{'page': 1, 'candidates': {'native': quote}, 'source_blocking_reasons': []},
            {'page': 2, 'candidates': {'native': 'Character Sheet'},
             'source_blocking_reasons': ['source_image_transcription_unverified'],
             'required_source_evidence': {'requirement_page': 1, 'requirement_quote': quote,
                'requirement_sha256': hashlib.sha256(quote.encode()).hexdigest(),
                'missing_fragment': 'Unique Keeper-only setup instruction.', 'region_bbox': [0, 0, 100, 100]}}]
    pdf_admission.compose([quote, 'Character Sheet'], rows)
    # Unsafe gameplay prevented: loss of an explicitly required setup instruction.
    assert rows[1]['source_blocking_reasons'] == ['source_image_transcription_unverified']


def test_cached_quarantine_is_not_promoted_to_verified():
    from app import pdf_admission
    rows = [{'page': 1, 'candidates': {'native': 'The Keeper describes the scene.'},
             'source_blocking_reasons': [], 'disposition': 'accepted'},
            {'page': 2, 'source_authority': 'QUARANTINED', 'source_blocking_reasons': [],
             'disposition': 'soft_review', 'selected_text': ''}]
    result = pdf_admission.compose(['The Keeper describes the scene.', ''], rows)
    assert result[1] == '' and rows[1]['source_authority'] == 'QUARANTINED'


@pytest.mark.parametrize('native,illustration', [('Credits\nWritten by Example', False), ('', True)])
def test_known_non_source_only_document_has_no_playable_core(native, illustration):
    from app import pdf_admission
    rows = [{'page': 1, 'candidates': {'native': native}, 'source_blocking_reasons': [],
             'verified_illustration': illustration, 'disposition': 'accepted'}]
    pdf_admission.compose([native], rows)
    # Unsafe gameplay prevented: there are no scenario instructions to run at all.
    assert rows[0]['source_blocking_reasons'] == ['canonical_playable_source_missing']

@pytest.mark.parametrize('retained', ['Example Adventure', '[PDF_OPTIONAL_ASSET: unresolved]'])
def test_title_or_placeholder_cannot_replace_unread_scenario_body(retained):
    from app import pdf_admission
    rows = [{'page': 1, 'candidates': {'native': retained}, 'source_blocking_reasons': []},
            {'page': 2, 'candidates': {'native': ''}, 'requires_image_transcription': True,
             'source_blocking_reasons': ['source_image_transcription_unverified']}]
    pdf_admission.compose([retained, ''], rows)
    # Unsafe gameplay prevented: publishing a title while all scenario instructions are unread.
    assert any('canonical_playable_source_missing' in row['source_blocking_reasons'] for row in rows)


@pytest.mark.parametrize('retained', ['Enter the house.', '開始時，調查員在屋外。'])
def test_short_complete_body_can_publish_with_unknown_appendix(retained):
    from app import pdf_admission
    rows = [{'page': 1, 'candidates': {'native': retained}, 'source_blocking_reasons': []},
            {'page': 2, 'candidates': {'native': ''}, 'requires_image_transcription': True,
             'source_blocking_reasons': ['source_image_transcription_unverified']}]
    assert pdf_admission.compose([retained, 'candidate'], rows) == [retained, '']
    assert not any(row['source_blocking_reasons'] for row in rows)


def test_geometry_identified_heading_does_not_count_as_body():
    from app import pdf_admission
    title = 'A Mystery.'
    rows = [{'page': 1, 'candidates': {'native': title}, 'source_blocking_reasons': [],
             'layout_decision': {'blocks': [{'text': title, 'role': 'heading'}]}}]
    pdf_admission.compose([title], rows)
    assert rows[0]['source_blocking_reasons'] == ['canonical_playable_source_missing']


def test_required_missing_fragment_is_not_duplicated_by_opposite_instruction():
    import hashlib

    from app import pdf_admission
    quote = 'The Keeper must read the unique image instruction before the scene.'
    source = quote + ' Do not open the cellar door.'
    rows = [{'page': 1, 'candidates': {'native': source}, 'source_blocking_reasons': []},
            {'page': 2, 'candidates': {'native': ''}, 'requires_image_transcription': True,
             'source_blocking_reasons': ['source_image_transcription_unverified'],
             'required_source_evidence': {'requirement_page': 1, 'requirement_quote': quote,
                'requirement_sha256': hashlib.sha256(quote.encode()).hexdigest(),
                'missing_fragment': 'open the cellar door.', 'region_bbox': [0, 0, 100, 100]}}]
    pdf_admission.compose([source, ''], rows)
    # Unsafe gameplay prevented: losing a required instruction with the opposite rule effect.
    assert rows[1]['source_blocking_reasons'] == ['source_image_transcription_unverified']


def test_short_bound_mechanics_are_not_mistaken_for_a_title():
    from app import pdf_admission
    rows = [{'page': 1, 'candidates': {'native': 'STR 60'}, 'source_blocking_reasons': [],
             'numeric_pairs': [{'label': 'STR', 'value': '60', 'status': 'same_row_candidate', 'block': 0}]},
            {'page': 2, 'candidates': {'native': ''}, 'requires_image_transcription': True,
             'source_blocking_reasons': ['source_image_transcription_unverified']}]
    assert pdf_admission.compose(['STR 60', ''], rows) == ['STR 60', '']
    assert not any(row['source_blocking_reasons'] for row in rows)


def test_title_only_native_page_cannot_publish_an_unread_raster_body(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_a, **_kw: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_kw: ('', []))
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, None)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 60), 'Example Adventure')
        page = doc.new_page()
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        image.clear_with(200)
        page.insert_image(page.rect, stream=image.tobytes('png'))
        raw = doc.tobytes()
    report = {}
    # Unsafe gameplay prevented: a readable cover is not a recovered scenario body.
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(raw, quality_report=report)
    assert report['scenario_readiness'] == 'BLOCKED'
    assert any('canonical_playable_source_missing' in row['source_blocking_reasons']
               for row in report['pages'])


@pytest.mark.parametrize('asset,counterpart,complete,blocked', [
    ('Setup sheet', '', True, True),
    ('Cover', '', True, False),
    ('Setup sheet', 'The Keeper sets the opening event to dusk.', True, False),
    ('Setup sheet', 'The Keeper sets the opening event to dusk.', False, True),
])
def test_production_binds_explicit_required_sheet_dependency(monkeypatch, tmp_path, asset, counterpart, complete, blocked):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_a, **_kw: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_kw: ('', []))
    fragment = 'The Keeper sets the opening event to dusk.'
    classification = {'page_role': 'handout' if asset != 'Cover' else 'cover_decorative',
        'contains_gameplay_source': asset != 'Cover', 'contains_mechanics': False,
        'contains_required_clue': False, 'asset_only': True,
        'all_source_fragments_accounted_for': complete, 'source_fragments': [fragment] if asset != 'Cover' else [],
        'optional_source_quote': '', 'optional_source_page': 0}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
        SimpleNamespace(analyze_image=lambda *_a, **_kw: classification, analysis_model_identity=lambda: 'required-dependency'))
    quote = 'The Keeper must follow the unique setup instruction on the attached sheet to begin the scenario.'
    with pymupdf.open() as doc:
        doc.new_page().insert_textbox((40, 60, 550, 700), quote + '\n' + counterpart)
        page = doc.new_page()
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        image.clear_with(200)
        page.insert_image(page.rect, stream=image.tobytes('png'))
        page.insert_text((40, 60), asset)
        raw = doc.tobytes()
    report = {}
    if blocked:
        with pytest.raises(pdf_loader.LayoutReviewRequired):
            pdf_loader.extract_text(raw, quality_report=report)
        assert report['hard_block_pages'] == [2]
        assert report['pages'][1]['required_source_evidence']['requirement_page'] == 1
    else:
        pdf_loader.extract_text(raw, quality_report=report)
        assert report['hard_block_pages'] == []
        assert 'required_source_evidence' not in report['pages'][1]


def test_continue_retries_failed_classification_without_repeating_source_work(monkeypatch, tmp_path):
    import hashlib

    from app import pdf_page_criticality
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_a, **_kw: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_kw: ('', []))
    calls = []
    cover = {'page_role': 'cover_decorative', 'contains_gameplay_source': False, 'contains_mechanics': False,
        'contains_required_clue': False, 'asset_only': True, 'all_source_fragments_accounted_for': True,
        'source_fragments': [], 'optional_source_quote': '', 'optional_source_page': 0}
    def analyze(_png, tool, *_args, **_kwargs):
        if tool['name'] == pdf_page_criticality.TOOL['name']:
            calls.append(tool['name'])
            return None if len(calls) == 1 else cover
        return None
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
        SimpleNamespace(analyze_image=analyze, analysis_model_identity=lambda: 'retry-production'))
    raw = book()
    first = {}
    _, _, _, images, _ = pdf_loader.extract_text(raw, quality_report=first)
    assert first['pages'][1]['page_criticality']['classification_attempt_status'] == 'failed'
    def cached(report):
        return {row['page']: {'pdf_sha256': hashlib.sha256(raw).hexdigest(),
            'pipeline_version': pdf_loader.PIPELINE_VERSION, 'renderer_version': pdf_loader.RENDERER_VERSION,
            'extraction_identity': pdf_loader.extraction_identity(), 'selected_text': row['selected_text'],
            'selected_sha256': row['selected_sha256'], 'report': row, 'image': images.get(row['page'])}
            for row in report['pages']}
    second = {}
    pdf_loader.extract_text(raw, quality_report=second, resume_pages=cached(first), retry_failed_classification=True)
    assert len(calls) == 2
    assert all(row['resumed'] for row in second['pages'])
    assert second['pages'][1]['page_criticality']['classification_attempt_status'] == 'completed'
    assert second['pages'][1]['page_criticality']['page_role'] == 'COVER_DECORATIVE'
    third = {}
    pdf_loader.extract_text(raw, quality_report=third, resume_pages=cached(second), retry_failed_classification=True)
    assert len(calls) == 2
