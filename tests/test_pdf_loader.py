import base64
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from app import config, pdf_ocr
from app.providers import registry

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
        page.insert_text((40, 60), "layout-aware handout text")
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

    def test_pymupdf4llm_disables_its_internal_ocr(self):
        document = pymupdf.open()
        document.new_page().insert_text((40, 60), "Native PDF source")
        pdf_bytes = document.tobytes()
        document.close()
        options = []

        def to_markdown(_doc, **kwargs):
            options.append(kwargs)
            return [{"metadata": {"page_number": 1}, "text": "Native PDF source"}]

        with patch.dict(sys.modules, {"pymupdf4llm": types.SimpleNamespace(to_markdown=to_markdown)}):
            pages = pdf_loader._pymupdf4llm_page_chunks(pdf_bytes)

        self.assertEqual(pages[1]["text"], "Native PDF source")
        self.assertEqual(options[0]["use_ocr"], False)

    def test_native_single_and_two_column_text_is_unchanged_without_internal_ocr(self):
        try:
            import pymupdf4llm
        except ImportError:
            self.skipTest("optional PyMuPDF4LLM is unavailable")

        for columns in (1, 2):
            with self.subTest(columns=columns), pymupdf.open() as document:
                page = document.new_page(width=600, height=800)
                for index in range(10):
                    y = 70 + index * 55
                    page.insert_text((50, y), f"Left {index} damage 2d6.")
                    if columns == 2:
                        page.insert_text((340, y), f"Right {index} SAN 1/1d6.")
                with_ocr = pymupdf4llm.to_markdown(document, page_chunks=True, use_ocr=True)[0]["text"]
                without_ocr = pymupdf4llm.to_markdown(document, page_chunks=True, use_ocr=False)[0]["text"]
                self.assertTrue(without_ocr)
                self.assertEqual(without_ocr, with_ocr)

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

    def test_numeric_verification_keeps_canonical_text_and_warning_history(self):
        source = 'SAN 1/1d6\n' + ('A safe native prose sentence. ' * 12)
        payload = self.pdf([source])
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': source}}), \
             patch.object(pdf_loader.pdf_quality, 'native_text', return_value=(source, ['native_two_columns'])), \
             patch.object(pdf_loader.pdf_quality, 'numeric_pairs', return_value=[]), \
             patch.object(pdf_loader.pdf_quality, 'select_text', return_value=(source, 'native', ['numeric_pair_review'])), \
             patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                          return_value=pdf_ocr.OcrResult(text='SAN 1 / 1d6', status='accepted')) as paddle:
            text, review, _, _, _ = pdf_loader.extract_text(payload, quality_report=report, ai_repair_limit=0)
        self.assertEqual(review, [])
        self.assertEqual(text, '--- 第 1 頁 ---\n' + source.strip())
        self.assertEqual(report['pages'][0]['method'], 'native')
        self.assertIn('numeric_pair_review', report['pages'][0]['warnings'])
        self.assertEqual(report['pages'][0]['paddle_numeric_verification']['warnings_resolved'],
                         ['numeric_pair_review'])
        self.assertEqual(report['pages'][0]['paddle_numeric_verification']['warnings_checked'],
                         ['numeric_pair_review'])
        paddle.assert_called_once()

    def test_numeric_verification_mismatch_keeps_review(self):
        source = 'SAN 1/1d6\n' + ('A safe native prose sentence. ' * 12)
        payload = self.pdf([source])
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': source}}), \
             patch.object(pdf_loader.pdf_quality, 'native_text', return_value=(source, ['native_two_columns'])), \
             patch.object(pdf_loader.pdf_quality, 'numeric_pairs', return_value=[]), \
             patch.object(pdf_loader.pdf_quality, 'select_text', return_value=(source, 'native', ['numeric_pair_review'])), \
             patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                          return_value=pdf_ocr.OcrResult(text='SAN 1d6/1', status='accepted')):
            text, review, _, _, _ = pdf_loader.extract_text(payload, quality_report=report, ai_repair_limit=0)
        self.assertEqual(review, [1])
        self.assertEqual(text, '--- 第 1 頁 ---\n' + source.strip())
        self.assertEqual(report['pages'][0]['paddle_numeric_verification']['warnings_resolved'], [])

    def test_no_numeric_warning_does_not_call_verification_paddle(self):
        source = 'Safe narrative without mechanics. ' * 12
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': source}}), \
             patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle') as paddle:
            _, review, _, _, _ = pdf_loader.extract_text(self.pdf([source]), ai_repair_limit=0)
        self.assertEqual(review, [])
        paddle.assert_not_called()

    def test_unknown_warning_still_requires_review(self):
        source = 'Safe narrative without mechanics. ' * 12
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': source}}), \
             patch.object(pdf_loader.pdf_quality, 'select_text', return_value=(source, 'native', ['unknown_warning'])), \
             patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle') as paddle:
            _, review, _, _, _ = pdf_loader.extract_text(self.pdf([source]), quality_report=report, ai_repair_limit=0)
        self.assertEqual(review, [1])
        self.assertNotIn('paddle_numeric_verification', report['pages'][0])
        paddle.assert_not_called()

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

    def test_swapped_pairs_fall_back_without_losing_candidate_evidence(self):
        payload = self.pdf(['STR 50 DEX 70'])
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': 'STR 70 DEX 50'}}):
            text, review, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
        self.assertIn('STR 50 DEX 70', text)
        self.assertNotIn('STR 70 DEX 50', text)
        row = report['pages'][0]
        self.assertEqual(row['candidates']['layout'], 'STR 70 DEX 50')
        self.assertEqual(row['method'], 'native')
        self.assertIn('layout_pair_mismatch', row['warnings'])
        self.assertTrue(row['evidence']['blocks'])
        self.assertEqual(len(report['pdf_sha256']), 64)
        self.assertEqual(len(row['selected_sha256']), 64)
        self.assertIn(1, review)

    def test_region_repair_changes_only_unique_damaged_block(self):
        from app import pdf_quality
        with pymupdf.open(stream=self.pdf(['Damage 2d6']), filetype='pdf') as doc:
            evidence = pdf_quality.block_evidence(doc[0])
            evidence['blocks'][0]['lines'][0]['text'] = 'Dam\ufffdage 2d6'
            with patch.object(pdf_loader, '_ocr_image', return_value='Damage 2d6') as ocr:
                result, attempts = pdf_loader._repair_local_regions(
                    doc[0], evidence, [], 'Unchanged header\nDam\ufffdage 2d6\nUnchanged footer', [1])
        self.assertEqual(result, 'Unchanged header\nDamage 2d6\nUnchanged footer')
        self.assertEqual(attempts[0]['status'], 'accepted')
        ocr.assert_called_once()

    def test_region_budget_and_duplicate_text_do_not_overwrite_source(self):
        from app import pdf_quality
        with pymupdf.open(stream=self.pdf(['Damage 2d6']), filetype='pdf') as doc:
            evidence = pdf_quality.block_evidence(doc[0])
            evidence['blocks'][0]['lines'][0]['text'] = 'Dam\ufffdage 2d6'
            with patch.object(pdf_loader, '_ocr_image', return_value='Damage 2d6') as ocr:
                source = 'Dam\ufffdage 2d6\nDam\ufffdage 2d6'
                result, attempts = pdf_loader._repair_local_regions(doc[0], evidence, [], source, [0])
                self.assertEqual(attempts[0]['status'], 'budget_exhausted')
                ocr.assert_not_called()
                result, attempts = pdf_loader._repair_local_regions(doc[0], evidence, [], source, [1])
        self.assertEqual(result, source)
        self.assertEqual(attempts[0]['status'], 'review_required')

    def test_import_pipeline_reuses_ai_transcription_and_marks_failures(self):
        provider = types.SimpleNamespace(analyze_image=lambda *args: {
            'regions': [{'block_id': 0, 'status': 'readable', 'text': 'Alice STR: 60'}]})
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
             patch.dict(registry.ANALYSIS_PROVIDERS, {config.ANALYSIS_PROVIDER: provider}):
            text, _, _, _, _ = pdf_loader.extract_text(self.pdf(['Alice STR']), quality_report=report, local_ocr_limit=0)
        self.assertIn('Alice STR: 60', text)
        self.assertNotIn('PDF_UNRESOLVED_FIELDS', text)
        self.assertEqual(report['ai_repair_requests'], 1)
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None):
            text, _, _, _, _ = pdf_loader.extract_text(self.pdf(['Alice STR']), local_ocr_limit=0, ai_repair_limit=0)
        self.assertIn('[PDF_UNRESOLVED_FIELDS: STR]', text)


    def test_unverified_layout_value_never_becomes_source(self):
        report = {}
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value={1: {'text': 'Alice STR 60'}}):
            text, _, _, _, _ = pdf_loader.extract_text(self.pdf(['Alice STR']), quality_report=report,
                                                       local_ocr_limit=0, ai_repair_limit=0)
        self.assertNotIn('60', text)
        self.assertIn('PDF_UNRESOLVED_FIELDS: STR', text)
        self.assertEqual(report['pages'][0]['method'], 'native')

    def test_prose_does_not_delete_valid_pregen_attribute(self):
        from unittest.mock import Mock

        from app import pregen_extractor
        provider = types.SimpleNamespace(analyze_image=Mock())
        with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
             patch.dict(registry.ANALYSIS_PROVIDERS, {config.ANALYSIS_PROVIDER: provider}):
            text, _, _, _, _ = pdf_loader.extract_text(self.pdf(['Alice STR 60\nAlice must make a STR roll.']))
        provider.analyze_image.assert_not_called()
        self.assertNotIn('PDF_UNRESOLVED_FIELDS', text)
        pregen = {'name': 'Alice', 'str_': 60}
        pregen_extractor._apply_pdf_unknowns(pregen, {1: text})
        self.assertEqual(pregen['str_'], 60)


def _numeric_verification_row(source, warnings, pairs=None, layout_checks=None, layout=''):
    return {'warnings': warnings, 'method': 'native', 'candidates': {'native': source, 'layout': layout},
            'numeric_pairs': pairs or [], 'layout_pair_checks': layout_checks or []}


def test_layout_numeric_loss_resolves_only_missing_anchored_evidence():
    source = 'STR 60 DEX 50\n' + ('Narrative without another value. ' * 10)
    pairs = [{'label': 'STR', 'value': '60', 'status': 'same_row_candidate', 'block': 0},
             {'label': 'DEX', 'value': '50', 'status': 'same_row_candidate', 'block': 0}]
    row = _numeric_verification_row(source, ['layout_numeric_loss'], pairs, layout='STR DEX 50')
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(text='STR 60 DEX 50', status='accepted')) as paddle:
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert result['attempted'] is True
    assert result['status'] == 'confirmed'
    assert result['warnings_resolved'] == ['layout_numeric_loss']
    paddle.assert_called_once()


@pytest.mark.parametrize(('warning', 'layout_status'), [
    ('numeric_pair_review', 'candidate_pair_unverified'),
    ('layout_pair_mismatch', 'pair_mismatch'),
])
@pytest.mark.parametrize(('candidate', 'confirmed'), [
    ('STR 60 DEX 50', True),
    ('STR 50 DEX 60', False),
    ('STR 60 DEX 50\nSTR 70', False),
])
def test_disputed_pair_requires_matching_label_value_without_contradiction(
        candidate, confirmed, warning, layout_status):
    source = 'STR 60 DEX 50\n' + ('Narrative without another value. ' * 10)
    pairs = [{'label': 'STR', 'value': '60', 'status': 'same_row_candidate', 'block': 0},
             {'label': 'DEX', 'value': '50', 'status': 'same_row_candidate', 'block': 0}]
    layout_checks = [{'label': 'STR', 'status': layout_status, 'block': 0},
                     {'label': 'DEX', 'status': 'matched', 'block': 0}]
    row = _numeric_verification_row(source, [warning], pairs, layout_checks)
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(text=candidate, status='accepted')):
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert result['warnings_resolved'] == ([warning] if confirmed else [])


@pytest.mark.parametrize('candidate', ['STR DEX 50', 'STR 50 DEX 60', 'STR 60 DEX 50\nSTR 70'])
def test_layout_numeric_loss_keeps_review_for_incomplete_or_conflicting_evidence(candidate):
    source = 'STR 60 DEX 50\n' + ('Narrative without another value. ' * 10)
    pairs = [{'label': 'STR', 'value': '60', 'status': 'same_row_candidate', 'block': 0},
             {'label': 'DEX', 'value': '50', 'status': 'same_row_candidate', 'block': 0}]
    row = _numeric_verification_row(source, ['layout_numeric_loss'], pairs, layout='STR DEX 50')
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(text=candidate, status='accepted')):
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert result['warnings_resolved'] == []


@pytest.mark.parametrize(('native', 'layout', 'paddle', 'confirmed'), [
    ('Armor 1\nMove 8', 'Armor\nMove 8', 'Armor 1\nMove 8', True),
    ('Armor 1\nRoom 2', 'Armor\nRoom 2', 'Armor\nRoom 1', False),
    ('STR 60\nDEX 60', 'STR\nDEX 60', 'STR 60\nDEX 60', True),
    ('STR 60\nDEX 60', 'STR\nDEX 60', 'STR\nDEX 60', False),
    ('Damage 1d6+2', 'Damage', 'Damage 1d6+2', True),
    ('Damage 1d6+2', 'Damage', 'Damage 1d6', False),
    ('SAN 1/1d6', 'SAN', 'SAN 1/1d6', True),
    ('SAN 1/1d6', 'SAN', 'SAN 1d6/1', False),
    ('Armor 1\nDamage 1d6+2', 'Armor\nDamage', 'Armor 1\nDamage 1d6', False),
])
def test_layout_loss_checks_whole_mechanic_near_same_anchor(native, layout, paddle, confirmed):
    source = native + '\n' + ('Narrative without another value. ' * 10)
    row = _numeric_verification_row(source, ['layout_numeric_loss'], layout=layout)
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(text=paddle, status='accepted')):
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert bool(result['warnings_resolved']) is confirmed
    assert result['status'] == ('confirmed' if confirmed else 'inconclusive')
    assert row['candidates']['native'] == source
    assert ('layout_numeric_loss' in result['warnings_unresolved']) is not confirmed
    assert sum(map(len, (result['confirmed_mechanics'], result['unconfirmed_mechanics']))) \
        == len(result['missing_mechanics'])


def test_layout_loss_requires_every_missing_mechanic():
    missing, confirmed, unconfirmed, anchors = pdf_loader._missing_mechanics_supported(
        'Armor 1\nDamage 1d6+2', 'Armor\nDamage', 'Armor 1\nDamage 1d6')
    assert missing == ['1', '1d6+2']
    assert confirmed == ['1']
    assert unconfirmed == ['1d6+2']
    assert anchors == 2


def test_layout_loss_without_local_anchor_stays_unconfirmed():
    missing, confirmed, unconfirmed, anchors = pdf_loader._missing_mechanics_supported('1', '', '1')
    assert missing == ['1']
    assert confirmed == []
    assert unconfirmed == ['1']
    assert anchors == 0


@pytest.mark.parametrize(('native', 'layout', 'paddle', 'expected'), [
    ('SAN 0/1d4', 'SAN', 'SAN 0 / 1D4', '0/1d4'),
    ('SAN 1d3/1d10', 'SAN', 'SAN 1d3/1d10', '1d3/1d10'),
    ('Chance 25%', 'Chance', 'Chance 25%', '25%'),
    ('Damage 1d10-1', 'Damage', 'Damage 1d10-1', '1d10-1'),
])
def test_layout_loss_preserves_complete_mechanic(native, layout, paddle, expected):
    missing, confirmed, unconfirmed, anchors = pdf_loader._missing_mechanics_supported(native, layout, paddle)
    assert missing == [expected]
    assert confirmed == [expected]
    assert unconfirmed == []
    assert anchors == 1


@pytest.mark.parametrize('status', ['unavailable', 'rejected', 'error', 'empty'])
def test_layout_loss_paddle_failure_keeps_review(status):
    source = 'Armor 1\n' + ('Narrative without another value. ' * 10)
    row = _numeric_verification_row(source, ['layout_numeric_loss'], layout='Armor')
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(status=status)):
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert result['status'] == status
    assert result['warnings_unresolved'] == ['layout_numeric_loss']


@pytest.mark.parametrize('status', ['unavailable', 'rejected', 'error', 'empty'])
def test_failed_numeric_verification_retains_warning_without_tesseract(status):
    source = 'SAN 1/1d6\n' + ('Narrative without another value. ' * 10)
    row = _numeric_verification_row(source, ['numeric_pair_review'])
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(status=status)) as paddle, \
         patch.object(pdf_loader.shutil, 'which', side_effect=AssertionError('Tesseract must not run')):
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert result['status'] == status
    assert result['warnings_unresolved'] == ['numeric_pair_review']
    paddle.assert_called_once()


def test_unresolved_source_pair_cannot_be_confirmed_by_ocr_alone():
    source = 'STR 60\n' + ('Narrative without another value. ' * 10)
    row = _numeric_verification_row(source, ['source_pair_unresolved'],
                                    [{'label': 'STR', 'status': 'unresolved', 'block': 0}])
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle',
                      return_value=pdf_ocr.OcrResult(text='STR 60', status='accepted')):
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert result['attempted'] is True
    assert result['warnings_resolved'] == []


def test_low_text_page_skips_second_verification_inference():
    source = 'SAN 1/1d6'
    row = _numeric_verification_row(source, ['numeric_pair_review'])
    with patch.object(pdf_loader.pdf_ocr, 'recognize_with_paddle') as paddle:
        result = pdf_loader._verify_numeric_with_paddle(None, row, source, b'image')
    assert result['attempted'] is False
    assert result['warnings_unresolved'] == ['numeric_pair_review']
    paddle.assert_not_called()


class PdfFinalReviewTests(unittest.TestCase):
    def test_numeric_evidence_only_resolves_its_verified_warning(self):
        row = {'warnings': ['native_two_columns', 'numeric_pair_review'],
               'paddle_numeric_verification': {'status': 'confirmed',
                                               'warnings_resolved': ['numeric_pair_review']}}
        self.assertFalse(pdf_loader._page_requires_review(row, 'x' * 300))
        row['paddle_numeric_verification']['status'] = 'inconclusive'
        self.assertTrue(pdf_loader._page_requires_review(row, 'x' * 300))
        row['paddle_numeric_verification']['status'] = 'confirmed'
        row['warnings'].append('future_unknown_warning')
        self.assertTrue(pdf_loader._page_requires_review(row, 'x' * 300))

    def test_final_warning_classification_preserves_history(self):
        cases = [
            (['low_text'], 200, False),
            (['low_text'], 199, True),
            (['low_text', 'local_ocr_repaired'], 200, False),
            (['local_ocr_repaired'], 200, False),
            (['local_ocr_review'], 300, True),
            (['numeric_pair_review'], 300, True),
            (['ocr_evidence_loss'], 300, True),
            (['vision_review_required'], 300, True),
            (['native_two_columns'], 300, False),
            (['layout_unavailable'], 300, False),
            (['ambiguous_columns'], 300, True),
            (['source_pair_unresolved'], 300, True),
            (['layout_pair_mismatch'], 300, True),
            (['layout_numeric_loss'], 300, True),
            (['layout_text_loss'], 300, True),
            (['ocr_pair_review'], 300, True),
            (['ocr_pair_mismatch'], 300, True),
            (['ai_fields_unresolved'], 300, True),
            (['vision_failed'], 300, True),
            (['vision_pair_review'], 300, True),
            (['vision_pair_mismatch'], 300, True),
            (['low_text', 'numeric_pair_review'], 300, True),
            (['local_ocr_repaired', 'ocr_evidence_loss'], 300, True),
            (['future_unknown_warning'], 300, True),
            (['empty_page'], 0, True),
        ]
        for warnings, length, expected in cases:
            with self.subTest(warnings=warnings, length=length):
                row = {'warnings': warnings.copy()}
                self.assertEqual(pdf_loader._page_requires_review(row, 'x' * length), expected)
                self.assertEqual(row['warnings'], warnings)

    def test_extract_text_uses_final_ocr_text_without_erasing_history(self):
        native = 'source ' * 11
        with pymupdf.open() as doc:
            doc.new_page()
            payload = doc.tobytes()
        for repaired in (False, True):
            for length in (199, 200, 250):
                with self.subTest(repaired=repaired, length=length):
                    final = native + 'x' * (length - len(native))
                    report = {}
                    repairs = [{'status': 'accepted'}] if repaired else []
                    with patch.object(pdf_loader, '_pymupdf4llm_page_chunks', return_value=None), \
                         patch.object(pdf_loader.pdf_quality, 'native_text', return_value=(native, [])), \
                         patch.object(pdf_loader.pdf_layout, 'reorder_with_paddle', return_value=pdf_loader.pdf_layout.LayoutResult(reason='incomplete_mapping')), \
                         patch.object(pdf_loader, '_repair_local_regions', return_value=(native, repairs)), \
                         patch.object(pdf_loader, '_page_has_graphic_content', return_value=True), \
                         patch.object(pdf_loader, '_render_page_png', return_value=b'png'), \
                         patch.object(pdf_loader, '_markitdown_page_texts', return_value={1: final}), \
                         patch.object(pdf_loader.pdf_ai_repair, 'repair_page', side_effect=lambda _page, _row, text, _budget: (text, {'regions': [], 'unresolved_labels': []})), \
                         patch.object(pdf_loader, '_analyze_graphic_page', return_value=('', None)) as vision:
                        text, review, _, _, _ = pdf_loader.extract_text(payload, quality_report=report)
                    self.assertIn(final, text)
                    self.assertEqual(review, [1] if length < 200 else [])
                    self.assertEqual(report['review_pages'], review)
                    self.assertIn('low_text', report['pages'][0]['warnings'])
                    self.assertEqual('local_ocr_repaired' in report['pages'][0]['warnings'], repaired)
                    if length >= 200:
                        vision.assert_not_called()
