"""Playable source admission is independent of optional image features."""
from types import SimpleNamespace

import pymupdf
import pytest

from app import config, pdf_loader
from app.providers import registry


def image_book(header='Quick-start cover'):
    with pymupdf.open() as doc:
        page = doc.new_page()
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        image.clear_with(200)
        page.insert_image(page.rect, stream=image.tobytes('png'))
        if header:
            page.insert_text((40, 60), header)
        page = doc.new_page()
        page.insert_textbox((40, 60, 550, 700), 'Each player creates an investigator. '
                            + 'The Keeper describes the house and its occupants. ' * 12)
        return doc.tobytes()


def test_cover_does_not_require_ocr_or_block_playable_source(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError('non-source cover must not enter OCR')))
    output = {'page_role': 'cover_decorative', 'contains_gameplay_source': False,
              'contains_mechanics': False, 'contains_required_clue': False,
              'asset_only': True, 'all_source_fragments_accounted_for': True,
              'source_fragments': [], 'optional_source_quote': '', 'optional_source_page': 0}
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
        SimpleNamespace(analyze_image=lambda *_a, **_k: output, analysis_model_identity=lambda: 'test'))
    report = {}
    text, _, _, _, _ = pdf_loader.extract_text(image_book(), quality_report=report)
    assert 'The Keeper describes the house' in text
    assert report['hard_block_pages'] == []
    assert report['pages'][0]['page_criticality']['page_role'] == 'COVER_DECORATIVE'
    assert report['pages'][0]['page_criticality']['source_critical'] is False


def classification(kind, **updates):
    return {'page_role': kind, 'contains_gameplay_source': False, 'contains_mechanics': False,
        'contains_required_clue': False, 'asset_only': True, 'all_source_fragments_accounted_for': True,
        'source_fragments': [], 'optional_source_quote': '', 'optional_source_page': 0, **updates}


@pytest.mark.parametrize('kind,role', [('illustration', 'PURE_ILLUSTRATION'),
    ('cover_decorative', 'COVER_DECORATIVE'), ('empty', 'EMPTY_NON_SOURCE'),
    ('pregen', 'OPTIONAL_PREGEN'), ('map', 'MAP_DERIVED'), ('handout', 'OPTIONAL_HANDOUT')])
def test_noncritical_assets_publish_without_ocr(monkeypatch, tmp_path, kind, role):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_k: pytest.fail('noncritical OCR'))
    # Each optional claim has literal permission in independently safe canonical text.
    quote = 'Each player creates an investigator.'
    raw = image_book()
    if kind == 'handout':
        quote = 'This handout is optional.'
        with pymupdf.open(stream=raw, filetype='pdf') as doc:
            doc[1].insert_text((40, 730), quote)
            raw = doc.tobytes()
    result = classification(kind, optional_source_page=2, optional_source_quote=quote)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER,
        SimpleNamespace(analyze_image=lambda *_a, **_k: result, analysis_model_identity=lambda: 'test'))
    report = {}
    _, _, _, _, maps = pdf_loader.extract_text(raw, quality_report=report)
    assert report['hard_block_pages'] == report['blocked_pages'] == []
    assert report['pages'][0]['page_criticality']['page_role'] == role
    if kind in {'map', 'pregen', 'handout'}:
        assert report['scenario_readiness'] == 'READY_WITH_WARNINGS'
    assert maps == {}


@pytest.mark.parametrize('kind', ['source_bearing', 'mixed', 'unknown'])
def test_required_or_unknown_image_still_blocks(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_k: ('', []))
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_a, **_k: None)
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_a, **_k: classification(kind), analysis_model_identity=lambda: 'test'))
    report = {}
    with pytest.raises(pdf_loader.LayoutReviewRequired):
        pdf_loader.extract_text(image_book(), quality_report=report)
    assert report['hard_block_pages'] == [1]
    assert report['pages'][0]['source_blocking_reasons'] == ['source_image_transcription_unverified']


def test_optional_pregen_needs_bound_complete_permission():
    from app import pdf_page_criticality as criticality
    quote = 'Each player creates an investigator.'
    output = classification('pregen', optional_source_page=2, optional_source_quote=quote)
    assert criticality.decide(output, '', {2: quote}, {2: quote})['source_critical'] is False
    assert criticality.decide(output, '', {2: 'Required assigned investigators only.'}, {2: 'Required assigned investigators only.'})['source_critical'] is None
    forbidden = 'Players cannot create their own investigator.'
    assert criticality.decide({**output, 'optional_source_quote': forbidden}, '', {2: forbidden}, {2: forbidden})['source_critical'] is None


def test_duplicate_handout_requires_every_exact_source_fragment():
    from app import pdf_page_criticality as criticality
    text = 'The letter identifies the house owner as Walter.'
    output = classification('handout', source_fragments=[text])
    assert criticality.decide(output, '', {2: text}, {2: text})['page_role'] == 'DUPLICATE_SOURCE'
    assert criticality.decide({**output, 'source_fragments': [text, 'Unique clue with a code 123.']}, '', {2: text}, {2: text})['source_critical'] is None


def test_classifier_failure_consumed_and_cache_rebound(monkeypatch, tmp_path):
    import json
    from unittest.mock import Mock

    from app import pdf_page_criticality as criticality
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    provider = Mock(analyze_image=Mock(return_value=None), analysis_model_identity=lambda: 'test')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    for _ in range(2):
        assert criticality.classify(b'page', page=1, pdf_sha256='book', native='', safe={})['source_critical'] is None
    assert provider.analyze_image.call_count == 1
    ledger = json.loads(next((tmp_path / '.page-criticality').glob('*/ledger.json')).read_text())
    assert ledger['consumed_requests'] == 1
    assert next(iter(ledger['attempts'].values()))['status'] == 'failed'


def test_authored_pregen_section_with_alternative_characters_is_optional(monkeypatch, tmp_path):
    from app import pdf_page_criticality as criticality
    raw = image_book('STR 60')
    with pymupdf.open(stream=raw, filetype='pdf') as doc:
        doc.set_toc([[1, 'Ready-Made Investigators', 1], [1, 'Adventure', 2]])
        sections = criticality.asset_sections(doc)
    assert list(sections) == [1]
    proof = criticality.optional_section({2: 'Each player creates an investigator.'}, sections[1], classification('pregen'))
    assert proof['page_role'] == 'OPTIONAL_PREGEN'
    assert len(proof['classification_evidence']) == 2
    assert criticality.optional_section({2: 'Players must use assigned characters.'}, sections[1]) is None
    assert criticality.optional_section({2: 'Each player creates an investigator.'}, sections[1],
        classification('pregen', contains_required_clue=True)) is None


def test_pregen_section_does_not_override_ambiguous_required_source():
    from app import pdf_page_criticality as criticality
    asset = {'kind': 'pregen', 'start_page': 1, 'end_page': 1, 'title_sha256': 'title'}
    assert criticality.optional_section({2: 'The Keeper creates a character who knows the murderer identity.'}, asset,
        classification('pregen')) is None
    for output in [None, classification('mixed'), classification('source_bearing'), classification('unknown')]:
        assert criticality.optional_section({2: 'Each player creates an investigator.'}, asset, output) is None


@pytest.mark.parametrize('broken', [None, [], 'invalid'])
def test_malformed_cached_attempt_fails_closed_without_dispatch(monkeypatch, tmp_path, broken):
    import json
    from unittest.mock import Mock

    from app import pdf_page_criticality as criticality
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    provider = Mock(analyze_image=Mock(return_value=None), analysis_model_identity=lambda: 'test')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    criticality.classify(b'page', page=1, pdf_sha256='book', native='', safe={})
    path = next((tmp_path / '.page-criticality').glob('*/ledger.json'))
    ledger = json.loads(path.read_text())
    ledger['attempts'][next(iter(ledger['attempts']))] = broken
    path.write_text(json.dumps(ledger))
    result = criticality.classify(b'page', page=1, pdf_sha256='book', native='', safe={})
    assert result['source_critical'] is None
    assert provider.analyze_image.call_count == 1
    assert json.loads(path.read_text())['consumed_requests'] == 1


@pytest.mark.parametrize('quote', [
    'The Keeper creates a character who knows the murderer identity.',
    'It is forbidden that each player creates an investigator.',
    'If each player creates an investigator, the Keeper chooses the clues.',
    'Each player creates an investigator only if the Keeper permits it.',
])
def test_optional_pregen_requires_affirmative_player_permission(quote):
    from app import pdf_page_criticality as criticality
    output = classification('pregen', optional_source_page=2, optional_source_quote=quote)
    assert criticality.decide(output, '', {2: quote}, {2: quote})['source_critical'] is None
    asset = {'kind': 'pregen', 'start_page': 1, 'end_page': 1, 'title_sha256': 'title'}
    assert criticality.optional_section({2: quote}, asset, output) is None


@pytest.mark.parametrize('quote', ['This handout is not optional.', 'This handout is optional.'])
def test_required_handout_cannot_be_overridden_by_optional_word(quote):
    from app import pdf_page_criticality as criticality
    output = classification('handout', optional_source_page=2, optional_source_quote=quote, contains_required_clue=True)
    assert criticality.decide(output, '', {2: quote}, {2: quote})['source_critical'] is True


def test_truly_empty_page_needs_no_provider_or_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, 'recover_local_ocr', lambda *_a, **_k: pytest.fail('blank OCR'))
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, None)
    with pymupdf.open(stream=image_book(), filetype='pdf') as doc:
        doc.delete_page(0)
        doc.new_page(pno=0)
        raw = doc.tobytes()
    report = {}
    pdf_loader.extract_text(raw, quality_report=report)
    assert report['hard_block_pages'] == []


def test_source_context_change_reuses_image_observation_but_rebinds_source(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from app import pdf_page_criticality as criticality
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    quote = 'This handout is optional.'
    provider = Mock(analyze_image=Mock(return_value=classification('handout',
        optional_source_page=2, optional_source_quote=quote)), analysis_model_identity=lambda: 'test')
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    assert criticality.classify(b'page', page=1, pdf_sha256='book', native='', safe={2: quote})['source_critical'] is False
    assert criticality.classify(b'page', page=1, pdf_sha256='book', native='', safe={2: 'Required clue only.'})['source_critical'] is None
    assert provider.analyze_image.call_count == 1


def test_appendix_triage_reuses_observations_and_binds_every_region(monkeypatch, tmp_path):
    import json
    from unittest.mock import Mock

    from app import pdf_page_criticality as criticality
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    permission = 'Each player creates an investigator.'
    rule = 'First Aid heals 1 HP.'
    observed = classification('mixed', contains_gameplay_source=True, contains_mechanics=True,
        asset_only=False, source_fragments=['Character gear and backstory.', rule])
    provider = Mock(analyze_image=Mock(return_value=observed), analysis_model_identity=lambda: 'test')
    provider.analyze_text = Mock(return_value={'pages': [{'page': 1, 'answer': 'NO', 'regions': [
        {'fragment_ids': [1], 'kind': 'character_asset', 'source_evidence': []},
        {'fragment_ids': [2], 'kind': 'duplicate_reference', 'source_evidence': [{'page': 3, 'quote': rule}]},
    ]}]})
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    safe = {2: permission, 3: rule}
    current = {1: criticality.classify(b'page', page=1, pdf_sha256='book', native='', safe=safe)}
    assets = {1: {'kind': 'pregen', 'start_page': 1, 'end_page': 1, 'title_sha256': 'title'}}
    result = criticality.triage({1: b'page'}, pdf_sha256='book', safe=safe, assets=assets, current=current)
    assert result[1]['source_critical'] is False
    assert result[1]['page_role'] == 'OPTIONAL_PREGEN'
    assert result[1]['required_regions'] == []
    assert len(result[1]['optional_regions']) == 2
    assert provider.analyze_image.call_count == 1
    for _ in range(2):
        criticality.triage({1: b'page'}, pdf_sha256='book', safe=safe, assets=assets, current=current)
    assert provider.analyze_text.call_count == 1
    ledger = json.loads(next((tmp_path / '.page-criticality').glob('*/ledger.json')).read_text())
    assert ledger['consumed_requests'] == 2
    # Unsafe gameplay prevented: a unique Keeper instruction must not disappear as optional character material.
    assert criticality.triage({1: b'page'}, pdf_sha256='book', safe={2: permission, 3: 'Unrelated rule.'},
        assets=assets, current=current) == {}


@pytest.mark.parametrize(('fragment', 'quote'), [
    ('First Aid heals 10 HP.', 'First Aid causes 10 HP loss.'),
    ('First Aid heals 10 HP.', 'First Aid cannot heal 10 HP.'),
    ('Medicine heals 1d3 HP.', 'First Aid heals 1d3 HP.'),
    ('A roll succeeds at 50%.', 'A roll succeeds at 50.'),
    ('Damage is 1d6+2.', 'Damage is 1d6.'),
])
def test_reference_cannot_bind_opposite_or_damaged_mechanics(fragment, quote):
    # Unsafe gameplay prevented: unique contradictory mechanics must not be discarded as duplicate reference.
    from app import pdf_page_criticality as criticality
    assert criticality.reference_compatible(fragment, quote) is False


def test_optional_reference_needs_explicit_author_instruction():
    from app import pdf_page_criticality as criticality
    quote = 'Quick Reference Rules: a handy rules reminder and something you might refer to once you have more experience of playing the game.'
    assert criticality.reference_permission(quote) is True
    assert criticality.reference_permission('Quick Reference Rules: you must use these rules before playing.') is False


def test_labelled_reference_ids_use_author_permission_without_losing_required_fragment(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from app import pdf_page_criticality as criticality
    monkeypatch.setattr(config, 'SCENARIO_LIBRARY_DIR', tmp_path)
    reminder = 'Quick Reference Rules: a handy rules reminder and something you might refer to once you have more experience of playing the game.'
    safe = {2: 'Each player creates an investigator.', 3: reminder}
    fragments = ['Quick Reference Rules: Skill rolls.', 'Natural Heal rate: weekly healing roll.']
    provider = Mock(analyze_image=Mock(return_value=classification('mixed', asset_only=False,
        source_fragments=fragments)), analysis_model_identity=lambda: 'test')
    provider.analyze_text = Mock(return_value={'pages': [{'page': 1, 'answer': 'NO', 'regions': [
        {'fragment_ids': [1], 'kind': 'optional_reference', 'source_evidence': [{'page': 3, 'quote': reminder}]},
    ]}]})
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, provider)
    current = {1: criticality.classify(b'page', page=1, pdf_sha256='book', native='', safe=safe)}
    assets = {1: {'kind': 'pregen', 'start_page': 1, 'end_page': 1, 'title_sha256': 'title'}}
    result = criticality.triage({1: b'page'}, pdf_sha256='book', safe=safe, assets=assets, current=current)
    # Unsafe gameplay prevented: omitted fragments may contain unique scenario-specific rules.
    assert result == {}


def test_missing_scenario_rule_cannot_be_filled_from_reference_heading():
    from app import pdf_page_criticality as criticality
    reminder = 'Quick Reference Rules: a handy rules reminder and something you might refer to once you have more experience of playing the game.'
    regions = [{'fragment_ids': [1], 'kind': 'optional_reference', 'source_evidence': [{'page': 3, 'quote': reminder}]}]
    fragments = ['Quick Reference Rules', 'Pushing rolls: In this scenario a pushed failure permanently kills the investigator.']
    assert criticality._reference_regions(regions, fragments, {3: reminder}) == regions


@pytest.mark.parametrize('counterpart,expected', [
    ('Pushing rolls: failure causes 1d6 damage.', True),
    ('Pushing rolls: failure heals 1d6 damage.', False),
])
def test_missing_reference_fragment_requires_exact_already_bound_counterpart(counterpart, expected):
    from app import pdf_page_criticality as criticality
    reminder = 'Quick Reference Rules: a handy rules reminder and something you might refer to once you have more experience of playing the game.'
    safe = {2: 'Each player creates an investigator.', 3: reminder}
    observed = {1: classification('mixed', asset_only=False, source_fragments=[counterpart]),
                4: classification('mixed', asset_only=False, source_fragments=['Quick Reference Rules', 'Pushing rolls: failure causes 1d6 damage.'])}
    region = {'fragment_ids': [1], 'kind': 'optional_reference', 'source_evidence': [{'page': 3, 'quote': reminder}]}
    output = {'pages': [{'page': p, 'answer': 'NO', 'regions': [region]} for p in observed]}
    assets = {p: {'kind': 'pregen', 'start_page': 1, 'end_page': 4, 'title_sha256': 'title'} for p in observed}
    result = criticality._triage_decisions(output, observed, safe, assets)
    assert (4 in result) is expected
