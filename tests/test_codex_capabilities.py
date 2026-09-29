import asyncio
import inspect
import os
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from app import config, keeper
from app.providers import codex_provider, registry, shutdown_async_clients


class CapabilityTests(unittest.TestCase):
    def test_codex_is_rejected_for_analysis_provider_at_startup(self):
        env = os.environ.copy()
        env.update(LLM_PROVIDER='codex', ANALYSIS_PROVIDER='codex')
        env.pop('OPENAI_API_KEY', None)
        result = subprocess.run(
            [sys.executable, '-c', 'import app.config'],
            check=False, capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Codex is not enabled for PDF', result.stderr)

    def test_all_conversation_routes_include_codex_and_contract_binds(self):
        # executor, narrator and guard all select through the registry.
        with patch.object(config, 'LLM_PROVIDER', 'codex'):
            self.assertIs(registry.conversation_provider(), codex_provider)
            self.assertIs(registry.require_conversation_provider(), codex_provider)
        for provider in registry.CONVERSATION_PROVIDERS.values():
            self.assertTrue(inspect.iscoroutinefunction(provider.run_conversation))
            inspect.signature(provider.run_conversation).bind('', '', [], [], '', AsyncMock(), 6, enable_wrapup=False)

    def test_analysis_registry_and_consumers_follow_their_provider_setting(self):
        from app import (
            pdf_ai_repair,
            pregen_extractor,
            scenario_compare,
            scenario_index,
            scenario_intro,
            scene_map,
        )
        self.assertNotIn('codex', registry.ANALYSIS_PROVIDERS)
        with patch.object(config, 'ANALYSIS_PROVIDER', 'codex'):
            self.assertIsNone(registry.analysis_provider())
        for module in [pdf_ai_repair, pregen_extractor, scene_map]:
            self.assertIs(module.analysis_provider, registry.analysis_provider)
        for module in [scenario_compare, scenario_index, scenario_intro]:
            self.assertIs(module.conversation_provider, registry.conversation_provider)
        self.assertIs(keeper.conversation_provider, registry.conversation_provider)

    def test_non_pdf_structured_analysis_uses_llm_provider(self):
        from app import scenario_compare, scenario_index, scenario_intro
        from app.providers import openai_provider

        with patch.object(codex_provider, 'analyze_text', side_effect=[
                 {'npcs': [], 'locations': []},
                 {'found': True, 'text': 'Opening', 'page': 1, 'opening_check': None},
                 {'discrepancies': []},
                 {'summary': 'updated'},
             ]) as analyze_text, \
             patch.object(config, 'LLM_PROVIDER', 'codex'), \
             patch.object(config, 'ANALYSIS_PROVIDER', 'openai'), \
             patch.object(openai_provider, 'analyze_text', side_effect=AssertionError('wrong provider')):
            scenario_index.extract_scenario_index('scenario text')
            scenario_intro.extract_opening_narration('scenario text')
            scenario_compare.compare_scenario_text('original', 'other parse')
            self.assertEqual(keeper.summarize_log_chunk('', [{'role': 'user', 'content': 'hello'}]), 'updated')
        self.assertEqual(analyze_text.call_count, 4)

    def test_pregen_and_page_image_extraction_use_analysis_provider(self):
        from app import pregen_extractor, scene_map
        from app.providers import openai_provider

        with patch.object(config, 'LLM_PROVIDER', 'codex'), \
             patch.object(config, 'ANALYSIS_PROVIDER', 'openai'), \
             patch.object(openai_provider, 'analyze_text', return_value={'pregens': []}) as analyze_text, \
             patch.object(openai_provider, 'analyze_image', return_value={
                 'description': 'a hall', 'page_type': 'map',
                 'rooms': [{'id': 'a', 'name': 'Hall', 'exits': []}],
             }) as analyze_image:
            self.assertEqual(pregen_extractor.extract_pregens('scenario text'), [])
            description, mapped = scene_map.analyze_page_image(b'png')
        self.assertEqual(description, 'a hall')
        self.assertIsNotNone(mapped)
        analyze_text.assert_called_once()
        analyze_image.assert_called_once()


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_active_conversation_and_closes_child(self):
        entered = asyncio.Event()
        transport = AsyncMock()
        async def request(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        transport.request.side_effect = request
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            task = asyncio.create_task(codex_provider.run_conversation('', '', [], [], '', AsyncMock(), 1))
            await entered.wait()
            await shutdown_async_clients()
            self.assertTrue(task.cancelled())
            transport.close.assert_awaited_once()
