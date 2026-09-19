import base64
import importlib.util
import sys
import types
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
    def test_combine_pdfs_uses_explicit_part_order(self):
        parts = []
        for label in ("part one", "part two"):
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((40, 60), label)
            parts.append(document.tobytes())
            document.close()
        source = pymupdf.open(stream=parts[0], filetype="pdf")
        source.set_toc([[1, "Part One", 1]])
        parts[0] = source.tobytes()
        source.close()
        source = pymupdf.open(stream=parts[1], filetype="pdf")
        source.set_toc([[1, "Part Two", 1]])
        parts[1] = source.tobytes()
        source.close()
        merged = pymupdf.open(stream=pdf_loader.combine_pdfs(parts), filetype="pdf")
        try:
            self.assertEqual(merged.page_count, 2)
            self.assertIn("part one", merged[0].get_text())
            self.assertIn("part two", merged[1].get_text())
            self.assertEqual([(item[1], item[2]) for item in merged.get_toc()], [("Part One", 1), ("Part Two", 2)])
        finally:
            merged.close()

    def test_pymupdf4llm_import_failure_falls_back(self):
        with patch.dict(sys.modules, {"pymupdf4llm": None}):
            self.assertIsNone(pdf_loader._pymupdf4llm_page_chunks(b"not a pdf"))

    def test_pymupdf4llm_page_chunks_are_used_for_layout_and_text(self):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((40, 60), "fallback text")
        pdf_bytes = document.tobytes()
        document.close()

        fake_pymupdf4llm = types.SimpleNamespace(
            to_markdown=lambda _doc, **_kwargs: [
                {
                    "metadata": {"page_number": 1},
                    "text": "layout-aware handout text",
                    "page_boxes": [{"class": "picture"}],
                }
            ]
        )
        with patch.dict(sys.modules, {"pymupdf4llm": fake_pymupdf4llm}), \
             patch.object(pdf_loader, "_markitdown_page_texts", return_value=None), \
             patch.object(pdf_loader, "_render_page_png", return_value=b"png"):
            text, low_pages, _truncated, page_images, _page_maps = pdf_loader.extract_text(pdf_bytes)

        self.assertIn("layout-aware handout text", text)
        self.assertEqual(low_pages, [1])
        self.assertEqual(page_images, {1: b"png"})

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
