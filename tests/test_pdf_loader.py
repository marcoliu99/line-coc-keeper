import base64
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf


_MODULE_SPEC = importlib.util.spec_from_file_location(
    "app._pdf_loader_test_impl", Path(__file__).parents[1] / "app" / "pdf_loader.py"
)
assert _MODULE_SPEC is not None and _MODULE_SPEC.loader is not None
pdf_loader = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = pdf_loader
_MODULE_SPEC.loader.exec_module(pdf_loader)


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class PdfLoaderImagePersistenceTests(unittest.TestCase):
    def test_graphic_page_is_saved_even_when_ocr_text_is_long(self):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_image(pymupdf.Rect(20, 20, 80, 80), stream=_ONE_PIXEL_PNG)
        page.insert_textbox(pymupdf.Rect(20, 100, 560, 780), "OCR角色卡文字 " * 80)
        pdf_bytes = document.tobytes()
        document.close()

        with patch.object(pdf_loader, "_markitdown_page_texts", return_value={1: "OCR角色卡文字 " * 80}), \
             patch.object(pdf_loader, "_analyze_graphic_page") as analyze:
            text, low_pages, truncated, page_images, page_maps = pdf_loader.extract_text(pdf_bytes)

        self.assertTrue(text)
        self.assertFalse(low_pages)
        self.assertFalse(truncated)
        self.assertIn(1, page_images)
        self.assertTrue(page_images[1].startswith(b"\x89PNG"))
        self.assertEqual(page_maps, {})
        analyze.assert_not_called()
