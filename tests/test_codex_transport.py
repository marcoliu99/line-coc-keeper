import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import config
from app.providers.codex_provider import response_schema
from app.providers.codex_transport import AppServerTransport, CodexError, ExecTransport


class ExecTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, events, exit_code=0):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'codex'
            script.write_text('#!/usr/bin/env python3\nimport sys,json\n'
                              'sys.stdin.read()\n'
                              f'events={events!r}\n'
                              'for e in events: print(json.dumps(e),flush=True)\n'
                              f'sys.exit({exit_code})\n')
            script.chmod(0o700)
            with patch.object(config, 'CODEX_BINARY', str(script)):
                return await asyncio.wait_for(ExecTransport().request('test', response_schema([])), 3)

    async def test_requires_completed_final_and_successful_exit(self):
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'final'}},
                  {'type': 'turn.completed'}]
        self.assertEqual(await self.run_fake(events), 'final')
        with self.assertRaisesRegex(CodexError, 'nonzero_exit'):
            await self.run_fake(events, 1)

    async def test_error_diagnostic_does_not_count_as_native_tool(self):
        events = [{'type': 'item.completed', 'item': {'type': 'error', 'message': 'CLI warning'}},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'final'}},
                  {'type': 'turn.completed'}]
        self.assertEqual(await self.run_fake(events), 'final')

    async def test_failure_empty_and_native_tool_are_rejected(self):
        for events, error in [
            ([{'type': 'turn.failed'}], 'turn_failed'),
            ([{'type': 'turn.completed'}], 'empty_response'),
            ([{'type': 'item.completed', 'item': {'type': 'command_execution'}}], 'native_tool'),
            ([{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'partial'}}], 'unexpected_eof'),
        ]:
            with self.subTest(error=error), self.assertRaisesRegex(CodexError, error):
                await self.run_fake(events)

    async def test_image_flag_and_tempfile_are_cleaned_on_success(self):
        class FakeProcess:
            image_path = None
            image_bytes = None

            async def start(self, args, cwd):
                image_path = Path(args[args.index('-i') + 1])
                self.image_path = image_path
                self.image_bytes = image_path.read_bytes()
                self.proc = SimpleNamespace(stdin=SimpleNamespace(write=lambda _data: None,
                                                                    drain=self._drain, close=lambda: None))
                self.events = iter([
                    {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"ok":true}'}},
                    {'type': 'turn.completed'},
                ])

            async def _drain(self):
                return None

            async def line(self):
                return next(self.events)

            async def finish(self):
                return 0

            async def close(self):
                self.existed_during_close = self.image_path.exists()

        fake = FakeProcess()
        with patch('app.providers.codex_transport.Process', return_value=fake):
            result = await ExecTransport().request('inspect this', {'type': 'object'}, image_png=b'png-bytes')
        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(fake.image_bytes, b'png-bytes')
        self.assertTrue(fake.existed_during_close)
        self.assertFalse(fake.image_path.exists())

    async def test_image_tempfile_is_cleaned_on_failure_and_cancellation(self):
        class FakeProcess:
            async def start(self, args, _cwd):
                self.image_path = Path(args[args.index('-i') + 1])
                self.proc = SimpleNamespace(stdin=SimpleNamespace(write=lambda _data: None,
                                                                    drain=self._drain, close=lambda: None))

            async def _drain(self):
                return None

            async def line(self):
                raise CodexError('codex_turn_failed')

            async def close(self):
                self.existed_during_close = self.image_path.exists()

        failed = FakeProcess()
        with patch('app.providers.codex_transport.Process', return_value=failed), \
                self.assertRaisesRegex(CodexError, 'turn_failed'):
            await ExecTransport().request('inspect this', {}, image_png=b'png-bytes')
        self.assertTrue(failed.existed_during_close)
        self.assertFalse(failed.image_path.exists())

        entered = asyncio.Event()

        class BlockingProcess(FakeProcess):
            async def line(self):
                entered.set()
                await asyncio.Future()

        cancelled = BlockingProcess()
        with patch('app.providers.codex_transport.Process', return_value=cancelled):
            task = asyncio.create_task(ExecTransport().request('inspect this', {}, image_png=b'png-bytes'))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cancelled.existed_during_close)
        self.assertFalse(cancelled.image_path.exists())


class AppServerTests(unittest.IsolatedAsyncioTestCase):
    def transport(self):
        transport = AppServerTransport()
        transport.directory = tempfile.TemporaryDirectory()
        self.addCleanup(transport.directory.cleanup)
        transport.process = AsyncMock()
        transport.process.proc = object()
        return transport

    async def test_notification_before_rpc_response_is_retained(self):
        transport = self.transport()
        transport.process.line.side_effect = [
            {'id': 1, 'result': {'model': config.CODEX_MODEL, 'thread': {'id': 't1'}}},
            {'method': 'item/completed', 'params': {'threadId': 't1', 'turnId': 'v1',
             'item': {'type': 'agentMessage', 'text': 'final'}}},
            {'id': 2, 'result': {'turn': {'id': 'v1'}}},
            {'method': 'turn/completed', 'params': {'threadId': 't1', 'turn': {'id': 'v1', 'status': 'completed'}}},
        ]
        self.assertEqual(await transport.request('test', {}), 'final')
        sent = transport.process.send.call_args_list
        self.assertTrue(sent[0].args[0]['params']['ephemeral'])
        self.assertEqual(sent[0].args[0]['params']['approvalPolicy'], 'never')

    async def test_unexpected_server_tool_request_is_rejected(self):
        transport = self.transport()
        transport.process.line.return_value = {'id': 44, 'method': 'item/commandExecution/requestApproval', 'params': {}}
        with self.assertRaisesRegex(CodexError, 'tool_request_rejected'):
            await transport.rpc('thread/start', {})

    async def test_model_substitution_rejected(self):
        transport = self.transport()
        transport.process.line.return_value = {'id': 1, 'result': {'model': 'other', 'thread': {'id': 't'}}}
        with self.assertRaisesRegex(CodexError, 'model_mismatch'):
            await transport.request('test', {})

    async def test_failed_turn_rejected(self):
        transport = self.transport()
        transport.process.line.side_effect = [
            {'id': 1, 'result': {'model': config.CODEX_MODEL, 'thread': {'id': 't'}}},
            {'id': 2, 'result': {'turn': {'id': 'v'}}},
            {'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'v', 'status': 'failed'}}},
        ]
        with self.assertRaisesRegex(CodexError, 'turn_failed'):
            await transport.request('test', {})
