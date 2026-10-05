import asyncio
import tempfile
import unittest
import unittest.mock
from pathlib import Path
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


class StopProcessTests(unittest.IsolatedAsyncioTestCase):
    """Cleanup after a timeout must not replace the timeout with its own error (a real run saw EPERM)."""

    def process(self):
        proc = AsyncMock()
        proc.pid = 4242
        proc.send_signal = unittest.mock.MagicMock()
        proc.wait = AsyncMock(return_value=0)
        return proc

    async def test_permission_denied_on_the_group_falls_back_to_the_child(self):
        from app.providers import codex_transport
        proc = self.process()
        with patch.object(codex_transport.os, 'killpg', side_effect=PermissionError(1, 'Operation not permitted')):
            await codex_transport.stop_process(proc)
        self.assertEqual(proc.send_signal.call_count, 2)
        proc.wait.assert_awaited()

    async def test_a_vanished_group_and_child_are_both_ignored(self):
        from app.providers import codex_transport
        proc = self.process()
        proc.send_signal.side_effect = ProcessLookupError
        with patch.object(codex_transport.os, 'killpg', side_effect=ProcessLookupError):
            await codex_transport.stop_process(proc)
        proc.wait.assert_awaited()

    async def test_the_original_timeout_is_not_masked_by_cleanup(self):
        from app.providers import codex_transport
        process = codex_transport.Process()
        process.proc = self.process()
        with patch.object(codex_transport.os, 'killpg', side_effect=PermissionError(1, 'Operation not permitted')), \
                self.assertRaises(TimeoutError):
            try:
                raise TimeoutError
            finally:
                await process.close()
