import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from app import keeper
from app.agents import executor, guard, narrator
from app.providers import codex_provider, registry, shutdown_async_clients


class CapabilityTests(unittest.TestCase):
    def test_all_conversation_routes_include_codex_and_contract_binds(self):
        for routes in [executor._PROVIDERS, narrator._PROVIDERS, guard._PROVIDERS, keeper._PROVIDERS]:
            self.assertIs(routes['codex'], codex_provider)
        for provider in registry.CONVERSATION_PROVIDERS.values():
            self.assertTrue(inspect.iscoroutinefunction(provider.run_conversation))
            inspect.signature(provider.run_conversation).bind('', '', [], [], '', AsyncMock(), 6, enable_wrapup=False)

    def test_analysis_modules_never_select_codex_for_analysis(self):
        from app import (
            pdf_ai_repair,
            pregen_extractor,
            scenario_compare,
            scenario_index,
            scenario_intro,
            scene_map,
        )
        for module in [pdf_ai_repair, pregen_extractor, scenario_compare, scenario_index, scenario_intro, scene_map]:
            self.assertNotIn('codex', module._PROVIDERS)
            self.assertIs(module._PROVIDERS, registry.ANALYSIS_PROVIDERS)

    def test_summary_uses_explicit_analysis_selection(self):
        provider = unittest.mock.Mock()
        provider.analyze_text.return_value = {'summary': 'updated'}
        with patch.object(keeper, 'ANALYSIS_PROVIDER', 'openai'), \
             patch.dict(keeper.ANALYSIS_PROVIDERS, {'openai': provider}):
            self.assertEqual(keeper.summarize_log_chunk('', [{'role': 'user', 'content': 'hello'}]), 'updated')
        provider.analyze_text.assert_called_once()


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_active_conversation_and_closes_child(self):
        entered = asyncio.Event()
        transport = AsyncMock()
        async def request(*args):
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
