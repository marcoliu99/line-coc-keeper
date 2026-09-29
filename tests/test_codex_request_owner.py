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

    async def test_shutdown_blocks_waiter_granted_just_before_close(self):
        owner = codex_request_owner.RequestOwner()
        with patch.object(config, 'CODEX_MAX_CONCURRENCY', 1):
            lease = await owner.acquire(codex_request_owner.deadline())
            queued = asyncio.create_task(owner.acquire(codex_request_owner.deadline()))
            await asyncio.sleep(0)
            lease.release()
            await owner.shutdown()
            with self.assertRaises(asyncio.CancelledError):
                await queued
        self.assertEqual(owner.in_use, 0)

    async def test_shutdown_waits_for_analysis_transport_cleanup_on_other_loop(self):
        owner = codex_request_owner.RequestOwner()
        started = threading.Event()
        closing = threading.Event()
        finish_close = threading.Event()
        transport = AsyncMock()

        async def blocked_request(*_args, **_kwargs):
            started.set()
            await asyncio.Future()

        async def slow_close():
            closing.set()
            await asyncio.to_thread(finish_close.wait)

        transport.request.side_effect = blocked_request
        transport.close.side_effect = slow_close
        tool = {'name': 'report', 'input_schema': {'type': 'object',
                'properties': {'label': {'type': 'string'}}, 'required': ['label']}}
        with patch.object(codex_request_owner, 'OWNER', owner), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            analysis = asyncio.create_task(asyncio.to_thread(
                codex_provider.analyze_text, 'source', tool, 'extract'))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            shutdown = asyncio.create_task(owner.shutdown())
            try:
                self.assertTrue(await asyncio.to_thread(closing.wait, 2))
                await asyncio.sleep(.02)
                self.assertFalse(shutdown.done())
            finally:
                finish_close.set()
            await asyncio.wait_for(shutdown, 2)
            self.assertIsNone(await asyncio.wait_for(analysis, 2))
        self.assertEqual(owner.in_use, 0)
        transport.close.assert_awaited_once()
