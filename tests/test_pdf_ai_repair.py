from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf
import pytest

from app import pdf_ai_repair as repair
from app import pdf_quality, pregen_extractor


def setup_page(doc, text='STR'):
    page = doc.new_page()
    page.insert_text((40, 100), text)
    evidence = pdf_quality.block_evidence(page)
    row = {'numeric_pairs': pdf_quality.numeric_pairs(evidence), 'evidence': evidence, 'local_repairs': []}
    return page, row


def test_ai_fills_readable_region_without_keeper_session():
    provider = SimpleNamespace(analyze_image=Mock(return_value={'regions': [{'block_id': 0, 'status': 'readable', 'text': 'STR: 60'}]}))
    with pymupdf.open() as doc, patch.dict(repair._PROVIDERS, {repair.LLM_PROVIDER: provider}):
        page, row = setup_page(doc)
        text, result = repair.repair_page(page, row, 'STR', [1])
    assert text == 'STR: 60' and result['status'] == 'accepted'
    assert result['unresolved_labels'] == []
    assert result['crop_sha256']
    provider.analyze_image.assert_called_once()


@pytest.mark.parametrize('status,text', [('blank', ''), ('unreadable', ''), ('readable', 'STR')])
def test_blank_unreadable_or_unchanged_fields_remain_unresolved(status, text):
    provider = SimpleNamespace(analyze_image=Mock(return_value={'regions': [{'block_id': 0, 'status': status, 'text': text}]}))
    with pymupdf.open() as doc, patch.dict(repair._PROVIDERS, {repair.LLM_PROVIDER: provider}):
        page, row = setup_page(doc)
        _, result = repair.repair_page(page, row, 'STR', [1])
    assert result['unresolved_labels'] == ['STR']


def test_luck_blank_is_never_completed_or_sent_alone():
    provider = SimpleNamespace(analyze_image=Mock())
    with pymupdf.open() as doc, patch.dict(repair._PROVIDERS, {repair.LLM_PROVIDER: provider}):
        page, row = setup_page(doc, 'LUCK')
        _, result = repair.repair_page(page, row, 'LUCK', [1])
    provider.analyze_image.assert_not_called()
    assert result['status'] == 'not_needed'
    assert not repair.validate('STR LUCK', 'STR 60 LUCK 50', [
        {'label': 'STR', 'status': 'unresolved', 'block': 0},
        {'label': 'LUCK', 'status': 'unresolved', 'block': 0}])


def test_budget_and_provider_failure_preserve_source():
    provider = SimpleNamespace(analyze_image=Mock(side_effect=RuntimeError('offline')))
    with pymupdf.open() as doc, patch.dict(repair._PROVIDERS, {repair.LLM_PROVIDER: provider}):
        page, row = setup_page(doc)
        text, result = repair.repair_page(page, row, 'STR', [0])
        assert result['status'] == 'budget_exhausted'
        provider.analyze_image.assert_not_called()
        text, result = repair.repair_page(page, row, text, [1])
    assert text == 'STR' and result['status'] == 'unavailable'


def test_unrequested_numbers_and_duplicate_region_answers_rejected():
    assert not repair.validate('STR', 'STR 60 HP 20', [{'label': 'STR', 'status': 'unresolved', 'block': 0}])
    response = {'regions': [{'block_id': 0, 'status': 'readable', 'text': 'STR 60'}] * 2}
    with pymupdf.open() as doc, patch.dict(repair._PROVIDERS, {repair.LLM_PROVIDER: SimpleNamespace(analyze_image=lambda *args: response)}):
        page, row = setup_page(doc)
        text, result = repair.repair_page(page, row, 'STR', [1])
    assert text == 'STR' and result['unresolved_labels'] == ['STR']


def test_unresolved_attribute_cannot_become_constructor_default():
    pregen = {'name': 'Alice', 'str_': 50}
    pregen_extractor._apply_pdf_unknowns(pregen, {1: 'Alice\n[PDF_UNRESOLVED_FIELDS: STR]'})
    assert 'str_' not in pregen
    with pytest.raises(ValueError, match='不能用預設值'):
        pregen_extractor.pregen_to_character(pregen, 'player')


def test_other_character_page_unaffected_and_manual_value_resolves():
    pregen = {'name': 'Alice', 'str_': 60}
    pregen_extractor._apply_pdf_unknowns(pregen, {1: 'Bob\n[PDF_UNRESOLVED_FIELDS: STR]'})
    assert pregen['str_'] == 60 and 'pdf_unresolved_fields' not in pregen


def test_manual_correction_survives_later_pdf_reextraction():
    incomplete = {'source': 'llm_extracted', 'name': 'Alice', 'pdf_unresolved_fields': ['str_']}
    manual = {'source': 'manual', 'name': 'Alice', 'str_': 70}
    merged = pregen_extractor._merge_pregens(incomplete, manual)
    assert merged['str_'] == 70 and not merged.get('pdf_unresolved_fields')
    again = pregen_extractor._merge_pregens(merged, incomplete)
    assert again['str_'] == 70 and not again.get('pdf_unresolved_fields')
