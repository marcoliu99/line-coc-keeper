"""Opt-in OAuth checks for the installed Codex CLI; never runs in normal CI."""
from __future__ import annotations

import io
import os
import unittest

from PIL import Image

from app.providers.codex_provider import analyze_image, analyze_text

ENABLED = os.environ.get('RUN_CODEX_ANALYSIS_SMOKE', '').strip().lower() in {'1', 'true', 'yes'}


@unittest.skipUnless(ENABLED, 'set RUN_CODEX_ANALYSIS_SMOKE=1 to use the authenticated Codex CLI')
class CodexAnalysisSmokeTests(unittest.TestCase):
    def test_authenticated_cli_text_analysis(self):
        tool = {'name': 'report', 'description': 'Report the code and value.', 'input_schema': {
            'type': 'object',
            'properties': {'code': {'type': 'string'}, 'value': {'type': 'integer'}},
            'required': ['code', 'value'],
        }}
        result = analyze_text('The source states: CASE-482 has a value of 37.', tool,
                              'Extract the exact code and its stated value.')
        self.assertEqual(result, {'code': 'CASE-482', 'value': 37})

    def test_authenticated_cli_image_analysis(self):
        image = Image.new('RGB', (32, 32), (255, 0, 0))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        tool = {'name': 'report', 'description': 'Report the dominant color.', 'input_schema': {
            'type': 'object',
            'properties': {'color': {'type': 'string', 'enum': ['red', 'green', 'blue']}},
            'required': ['color'],
        }}
        result = analyze_image(buffer.getvalue(), tool,
                               'Inspect the attached PNG and identify its dominant color.')
        self.assertEqual(result, {'color': 'red'})
