"""Similarity previews cannot decide whether a valid PDF has playable source."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest

from app import config, pdf_loader
from app.providers import registry


@pytest.fixture
def image_frontmatter_pdf():
    with pymupdf.open() as image:
        page = image.new_page(width=120, height=120)
        page.draw_circle((60, 60), 30, fill=(0.3, 0.3, 0.3))
        png = page.get_pixmap().tobytes('png')
    with pymupdf.open() as document:
        for _ in range(3):
            page = document.new_page()
            page.insert_image(page.rect, stream=png)
        page = document.new_page()
        page.insert_textbox((30, 30, 550, 700),
            'Keeper instructions. Investigators arrive at the house. '
            'The scene begins at the entrance. Describe the setting and answer questions. ' * 4)
        return document.tobytes()


def test_image_only_frontmatter_has_no_native_similarity_preview(image_frontmatter_pdf):
    assert pdf_loader.extract_preview(image_frontmatter_pdf) == ''
    assert 'Keeper instructions' in pdf_loader.extract_preview(image_frontmatter_pdf, page_limit=4)


@pytest.mark.parametrize('encrypted', [False, True])
def test_invalid_or_locked_pdf_is_not_an_empty_preview(encrypted):
    if encrypted:
        with pymupdf.open() as document:
            document.new_page()
            raw = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                   owner_pw='owner', user_pw='reader')
    else:
        raw = b'not a PDF'
    with pytest.raises(ValueError):
        pdf_loader.extract_preview(raw)


def test_valid_image_frontmatter_reaches_production_publication(image_frontmatter_pdf, monkeypatch, tmp_path):
    from app import db, pdf_ingestion_drafts, pdf_ocr, scenario_library
    from app import legacy_commands as commands
    from app.repositories import group_state

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    db._ensure_tables()
    monkeypatch.setattr(group_state, 'DATA_DIR', tmp_path / 'images')
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(pdf_ingestion_drafts, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(pdf_loader, '_pymupdf4llm_page_chunks', lambda *_: None)
    monkeypatch.setattr(pdf_loader, '_markitdown_page_texts', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pdf_loader, '_ocr_image', lambda *_: '')
    monkeypatch.setattr(pdf_ocr, 'paddle_candidate', lambda *_: {'engine': 'paddleocr', 'model': 'PP-OCRv5_mobile_rec', 'status': 'empty', 'candidate': ''})
    monkeypatch.setitem(registry.ANALYSIS_PROVIDERS, config.ANALYSIS_PROVIDER, SimpleNamespace(
        analyze_image=lambda *_args, **_kwargs: None, analyze_text=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(commands.scenario_index, 'extract_scenario_index', lambda *_: {'npcs': [], 'locations': []})
    monkeypatch.setattr(commands.pregen_extractor, 'extract_pregens', lambda *_: [])
    similar = Mock(side_effect=AssertionError('Empty preview must not trigger similarity lookup'))
    monkeypatch.setattr(scenario_library, 'find_similar', similar)
    messages = []
    async def reply(message):
        messages.append(str(message))

    assert asyncio.run(commands.handle_pdf_upload('image-frontmatter', reply, reply,
        image_frontmatter_pdf, 'synthetic.pdf')) is True
    similar.assert_not_called()
    state = group_state.load_state('image-frontmatter')
    assert state.scenario_library_id
    assert 'Keeper instructions' in state.scenario_text
    context = scenario_library.load_context(state.scenario_library_id)
    assert 'Keeper instructions' in context['text']
    assert '已成功匯入，可以開始遊戲' in messages[-1]


@pytest.mark.parametrize('encrypted', [False, True])
def test_unreadable_preview_still_reports_failed_upload(encrypted, monkeypatch, tmp_path):
    from app import legacy_commands as commands
    from app import pdf_ingestion_drafts
    from app.models import GroupState

    if encrypted:
        with pymupdf.open() as document:
            document.new_page()
            raw = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                   owner_pw='owner', user_pw='reader')
    else:
        raw = b'not a PDF'
    monkeypatch.setattr(pdf_ingestion_drafts, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(commands, 'load_state', lambda _: GroupState(group_id='unreadable'))
    extract = Mock(side_effect=AssertionError('Unreadable PDF must not enter extraction'))
    monkeypatch.setattr(pdf_loader, 'extract_text', extract)
    messages = []
    async def reply(message):
        messages.append(str(message))

    assert asyncio.run(commands.handle_pdf_upload('unreadable', reply, reply, raw, 'synthetic.pdf')) is False
    extract.assert_not_called()
    assert '❌' in messages[-1] and '尚未啟用' in messages[-1]
    assert '重新上傳' in messages[-1]
