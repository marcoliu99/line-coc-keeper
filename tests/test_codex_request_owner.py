import asyncio
import json
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app import config
from app.providers import codex_provider, codex_request_owner


class RequestOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_and_conversation_share_one_slot_and_instructions(self):
        owner = codex_request_owner.RequestOwner()
        started = threading.Event()
        release = threading.Event()
        analysis = AsyncMock()
        conversation = AsyncMock()

        async def analyze(*args, **kwargs):
            self.assertNotIn('game tools', kwargs['instructions'])
            started.set()
            await asyncio.to_thread(release.wait)
            return '{"label":"ok"}'

        analysis.request.side_effect = analyze
        conversation.request.return_value = json.dumps({'decision': {'type': 'final', 'content': 'done'}})
        factory = iter([analysis, conversation])
        tool = {'name': 'report', 'input_schema': {'type': 'object', 'properties': {
            'label': {'type': 'string'}}, 'required': ['label']}}
        with patch.object(codex_request_owner, 'OWNER', owner), \
             patch.object(config, 'CODEX_MAX_CONCURRENCY', 1), \
             patch.object(config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', side_effect=lambda: next(factory)):
            analysis_task = asyncio.create_task(asyncio.to_thread(
                codex_provider.analyze_text, 'source', tool, 'extract'))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            conversation_task = asyncio.create_task(codex_provider.run_conversation(
                '', '', [], [], '', AsyncMock(), 1))
            await asyncio.sleep(.02)
            conversation.request.assert_not_awaited()
            release.set()
            self.assertEqual(await analysis_task, {'label': 'ok'})
            self.assertEqual(await conversation_task, 'done')
        self.assertEqual(owner.in_use, 0)
        self.assertIn('host tools', conversation.request.call_args.kwargs['instructions'])

    async def test_shutdown_cancels_queued_request(self):
        owner = codex_request_owner.RequestOwner()
        with patch.object(config, 'CODEX_MAX_CONCURRENCY', 1):
            lease = await owner.acquire(codex_request_owner.deadline())
            queued = asyncio.create_task(owner.acquire(codex_request_owner.deadline()))
            await asyncio.sleep(0)
            await owner.shutdown()
            with self.assertRaises(asyncio.CancelledError):
                await queued
            self.assertEqual(owner.in_use, 1)
            lease.release()
        self.assertEqual(owner.in_use, 0)
