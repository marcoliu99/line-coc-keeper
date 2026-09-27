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

    def test_pymupdf4llm_zero_based_page_metadata_is_shifted(self):
        fake_pymupdf4llm = types.SimpleNamespace(
            to_markdown=lambda _doc, **_kwargs: [
                {"metadata": {"page": 0}, "text": "first"},
                {"metadata": {"page": 1}, "text": "second"},
            ]
        )
        # Exercise the normalizer with a valid document while keeping the
        # zero-based metadata shape exposed by some releases.
        document = pymupdf.open()
        document.new_page()
        document.new_page()
        pdf_bytes = document.tobytes()
        document.close()
        with patch.dict(sys.modules, {"pymupdf4llm": fake_pymupdf4llm}):
            pages = pdf_loader._pymupdf4llm_page_chunks(pdf_bytes)
        self.assertEqual(sorted(pages), [1, 2])
        self.assertEqual(pages[1]["text"], "first")
        self.assertEqual(pages[2]["text"], "second")

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


class PdfQualityRegressionTests(unittest.TestCase):
    def pdf(self, lines):
        with pymupdf.open() as doc:
            for text in lines:
                page = doc.new_page()
                page.insert_textbox(pymupdf.Rect(40, 40, 550, 790), text)
            return doc.tobytes()

    def test_layout_wins_and_readable_pages_do_not_request_paid_fallback(self):
        payload = self.pdf(['A faithful rule without any numbers. ' * 20])
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={
            1: {'text': 'A faithful rule without any numbers. ' * 20}
        }), patch.object(pdf_loader, '_markitdown_page_texts', return_value={1: ''}) as alternate:
            text, _, truncated, _, _ = pdf_loader.extract_text(payload, quality_report=report)
        alternate.assert_not_called()
        self.assertIn('faithful rule', text)
        self.assertEqual(report['pages'][0]['method'], 'layout')
        self.assertFalse(truncated)

    def test_dropped_dice_uses_native_source_and_records_warning(self):
        payload = self.pdf(['Failure causes 2d6 damage.'])
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': 'Failure causes damage.'}}):
            text, review, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
        self.assertIn('2d6', text)
        self.assertIn(1, review)
        self.assertIn('layout_numeric_loss', report['pages'][0]['warnings'])

    def test_complete_source_is_not_cut_at_old_limit(self):
        # Every page fits, but the complete source exceeds the former 240K cap.
        payload = self.pdf(['Rule and consequence remain together. ' * 40] * 180)
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None):
            text, _, truncated, _, _ = pdf_loader.extract_text(payload)
        self.assertGreater(len(text), 240000)
        self.assertIn('--- 第 180 頁 ---', text)
        self.assertFalse(truncated)

    def test_continuation_report_preserves_original_page_boundaries(self):
        payload = self.pdf(['The poison causes', 'blurred vision for 1d6 rounds.'])
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None):
            text, _, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
        self.assertEqual(report['continuations'], [{'from_page': 1, 'to_page': 2, 'status': 'candidate'}])
        self.assertIn('--- 第 2 頁 ---', text)

    def test_column_reordering_requires_clear_gutter(self):
        from app import pdf_quality
        with pymupdf.open() as doc:
            page = doc.new_page()
            for i in range(3):
                page.insert_text((40, 100 + 100 * i), f'Left {i}')
                page.insert_text((350, 110 + 100 * i), f'Right {i}')
            text, warnings = pdf_quality.native_text(page)
        self.assertIn('native_two_columns', warnings)
        self.assertLess(text.index('Left 2'), text.index('Right 0'))

    def test_fallback_subset_maps_back_to_original_page(self):
        payload = self.pdf(['first', 'second', 'third'])
        seen = []
        def convert(stream, **kwargs):
            with pymupdf.open(stream=stream.read(), filetype='pdf') as doc:
                seen.append(doc.page_count)
            return types.SimpleNamespace(text_content='## Page 1\nthird transcribed')
        converter = types.SimpleNamespace(convert=convert)
        with patch.object(pdf_loader, 'build_markitdown', return_value=converter), \
             patch.dict(sys.modules, {'markitdown': types.SimpleNamespace(StreamInfo=lambda **kwargs: None)}):
            result = pdf_loader._markitdown_page_texts(payload, [3])
        self.assertEqual(seen, [1])
        self.assertEqual(result, {3: 'third transcribed'})

    def test_page_failure_does_not_discard_other_source(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_image(pymupdf.Rect(20, 20, 80, 80), stream=_ONE_PIXEL_PNG)
            page = doc.new_page()
            page.insert_text((40, 100), 'Preserved source with 2d6 damage.')
            payload = doc.tobytes()
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
             patch.object(pdf_loader, '_markitdown_page_texts', return_value=None), \
             patch.object(pdf_loader, '_analyze_graphic_page', side_effect=RuntimeError('offline')):
            text, review, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
        self.assertIn('Preserved source with 2d6', text)
        self.assertIn('vision_failed', report['pages'][0]['warnings'])
        self.assertIn(1, review)

    def test_quality_report_persisted_with_full_library_source(self):
        import json
        import tempfile

        from app import scenario_library
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(scenario_library, 'SCENARIO_LIBRARY_DIR', Path(directory)):
            report = {'version': 'test', 'review_pages': [1]}
            scenario_id = scenario_library.save_scenario(
                self.pdf(['source']), title='Quality test', filename='test.pdf', preview='source',
                text='--- 第 1 頁 ---\nsource', indexes={}, pregens=[], page_maps={}, page_images={},
                parse_quality=report,
            )
            saved = json.loads((Path(directory) / scenario_id / 'parse_quality.json').read_text())
        self.assertEqual(saved, report)
