import asyncio
import json
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import config
from app.providers import codex_provider as cp
from app.providers.codex_transport import CodexError, Process, child_environment

TOOL = {'name': 'inspect', 'description': 'inspect an object', 'input_schema': {
    'type': 'object', 'properties': {'target': {'type': 'string'}}, 'required': ['target']}}


def final(content='done'):
    return json.dumps({'decision': {'type': 'final', 'content': content}})


def call(target='desk', name='inspect'):
    return json.dumps({'decision': {'type': 'tool_call', 'name': name,
                                   'arguments_json': json.dumps({'target': target})}})


class CodexProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = AsyncMock()
        self.factory = patch.object(cp, 'ExecTransport', return_value=self.transport)
        self.factory.start()
        self.mode = patch.object(config, 'CODEX_TRANSPORT', 'exec')
        self.mode.start()
        self.tool = AsyncMock(return_value={'ok': True, 'evidence_ref': 'tool:1'})

    async def asyncTearDown(self):
        self.factory.stop()
        self.mode.stop()

    async def run_provider(self, events, **kwargs):
        self.transport.request.side_effect = events
        return await cp.run_conversation('static', 'current state', [TOOL], [], 'inspect',
                                         self.tool, kwargs.pop('max_iterations', 6), **kwargs)

    async def test_plain_text_and_executor_json_are_returned_unchanged(self):
        for text in ['你推開門。', '{"disposition":"await_check"}']:
            self.assertEqual(await self.run_provider([final(text)]), text)
        self.tool.assert_not_awaited()

    async def test_two_tools_then_narration_with_receipts(self):
        self.assertEqual(await self.run_provider([call(), call('lamp'), final()]), 'done')
        self.assertEqual(self.tool.await_count, 2)
        prompt = json.loads(self.transport.request.call_args.args[0])
        self.assertEqual(len(prompt['current_conversation']), 2)
        self.assertEqual(prompt['current_conversation'][0]['result']['evidence_ref'], 'tool:1')

    async def test_malformed_recovery_is_bounded_and_does_not_execute(self):
        self.assertEqual(await self.run_provider(['bad', final()]), 'done')
        with self.assertRaisesRegex(CodexError, 'invalid_decision'):
            await self.run_provider(['bad', 'bad'])
        self.tool.assert_not_awaited()

    async def test_unknown_tool_and_invalid_arguments_are_not_dispatched(self):
        for data in [call(name='shell'), call(target=7), '{"decision":{"type":"final","content":""}}']:
            self.assertEqual(await self.run_provider([data, final()]), 'done')
        self.tool.assert_not_awaited()

    async def test_tool_failure_is_receipt_and_not_retried(self):
        self.tool.side_effect = RuntimeError('private diagnostic')
        self.assertEqual(await self.run_provider([call(), final('unfinished')]), 'unfinished')
        prompt = self.transport.request.call_args.args[0]
        self.assertNotIn('private diagnostic', prompt)
        self.assertIn('completion_uncertain', prompt)
        self.tool.reset_mock()
        with self.assertRaisesRegex(CodexError, 'duplicate_tool'):
            await self.run_provider([call(), call()])
        self.assertEqual(self.tool.await_count, 1)

    async def test_iteration_limit_never_runs_an_extra_wrapup(self):
        with self.assertRaisesRegex(CodexError, 'iteration_limit'):
            await self.run_provider([call()], max_iterations=1, enable_wrapup=False)
        self.assertEqual(self.transport.request.await_count, 1)
        self.assertEqual(self.tool.await_count, 1)

    async def test_current_allowlist_is_rechecked_before_execution(self):
        lists = iter([[TOOL], []])
        with self.assertRaisesRegex(CodexError, 'not_allowed'):
            await self.run_provider([call()], tools_for_request=lambda: next(lists))
        self.tool.assert_not_awaited()

    async def test_shared_budget_across_agents_resets_between_turns(self):
        @cp.with_codex_turn
        async def turn():
            await self.run_provider([call(), final()])
            await self.run_provider([final('remaining')])
            prompt = json.loads(self.transport.request.call_args.args[0])
            self.assertEqual(prompt['tools'], [])
        with patch.object(config, 'MAX_TOOLS_PER_TURN', 1):
            await turn()
            await turn()
        self.assertEqual(self.tool.await_count, 2)

    async def test_timeout_includes_queue_and_releases_admission(self):
        gate = asyncio.Semaphore(0)
        with patch.object(cp, 'semaphore', return_value=gate), patch.object(config, 'CODEX_TIMEOUT', .01), self.assertRaises(TimeoutError):
            await self.run_provider([final()])
        self.assertEqual(gate._value, 0)
        self.transport.request.assert_not_awaited()

    async def test_queued_cancellation_does_not_release_unowned_slot(self):
        gate = asyncio.Semaphore(0)
        with patch.object(cp, 'semaphore', return_value=gate):
            task = asyncio.create_task(cp.run_conversation('', '', [], [], '', self.tool, 1))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(gate._value, 0)
        self.transport.request.assert_not_awaited()

    async def test_extra_fields_and_host_only_arguments_are_rejected(self):
        invalid = [
            {'decision': {'type': 'final', 'content': 'done', 'extra': True}},
            {'decision': {'type': 'tool_call', 'name': 'inspect',
                          'arguments_json': '{"target":"desk","_player_action":"fake"}'}},
        ]
        for data in invalid:
            self.assertEqual(await self.run_provider([json.dumps(data), final()]), 'done')
        self.tool.assert_not_awaited()

    async def test_finalization_cannot_create_extra_tool_budget(self):
        with patch.object(config, 'MAX_TOOLS_PER_TURN', 1):
            self.assertEqual(await self.run_provider([call(), call('lamp'), final()]), 'done')
        self.tool.assert_awaited_once()
        prompt = json.loads(self.transport.request.call_args.args[0])
        self.assertEqual(prompt['tools'], [])

    async def test_running_cancellation_closes_transport_and_releases_slot(self):
        started = asyncio.Event()
        async def blocked(*_args):
            started.set()
            await asyncio.Future()
        self.transport.request.side_effect = blocked
        gate = asyncio.Semaphore(1)
        with patch.object(cp, 'semaphore', return_value=gate):
            task = asyncio.create_task(cp.run_conversation('', '', [], [], '', self.tool, 1))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(gate._value, 1)
        self.transport.close.assert_awaited_once()

    async def test_completed_mutation_survives_deadline_without_replay(self):
        async def slow_tool(*_args):
            await asyncio.sleep(.02)
            return {'ok': True}
        self.tool.side_effect = slow_tool
        with patch.object(config, 'CODEX_TIMEOUT', .01), self.assertRaises(TimeoutError):
            await self.run_provider([call(), final()])
        self.tool.assert_awaited_once()
        self.assertEqual(self.transport.request.await_count, 1)


class CodexProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_stderr_flood_aborts_even_if_stdout_never_arrives(self):
        process = Process()
        with tempfile.TemporaryDirectory() as cwd, patch.object(config, 'CODEX_BINARY', sys.executable), \
                patch.object(config, 'CODEX_MAX_OUTPUT_BYTES', 1024):
            await process.start(['-c', 'import sys,time; sys.stderr.write("x"*5000);sys.stderr.flush();time.sleep(30)'], cwd)
            try:
                with self.assertRaisesRegex(CodexError, 'output_limit'):
                    await asyncio.wait_for(process.line(), 2)
            finally:
                await process.close()
            self.assertIsNotNone(process.proc.returncode)

    async def test_stdout_flood_after_completion_is_still_bounded(self):
        process = Process()
        with tempfile.TemporaryDirectory() as cwd, patch.object(config, 'CODEX_BINARY', sys.executable), \
                patch.object(config, 'CODEX_MAX_OUTPUT_BYTES', 1024):
            await process.start(['-c', 'import sys,time;print("{}",flush=True);sys.stdout.write("x"*5000);sys.stdout.flush();time.sleep(30)'], cwd)
            try:
                self.assertEqual(await process.line(), {})
                with self.assertRaisesRegex(CodexError, 'output_limit'):
                    await asyncio.wait_for(process.finish(), 2)
            finally:
                await process.close()
            self.assertIsNotNone(process.proc.returncode)

    async def test_malformed_event_and_process_cleanup(self):
        process = Process()
        with tempfile.TemporaryDirectory() as cwd, patch.object(config, 'CODEX_BINARY', sys.executable):
            await process.start(['-c', 'print("not-json",flush=True);import time;time.sleep(30)'], cwd)
            try:
                with self.assertRaisesRegex(CodexError, 'invalid_event'):
                    await process.line()
            finally:
                await process.close()
            self.assertIsNotNone(process.proc.returncode)

    def test_child_does_not_inherit_api_or_bot_credentials(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'secret', 'CODEX_API_KEY': 'secret',
                                      'DISCORD_BOT_TOKEN': 'secret', 'SOME_NEW_SECRET': 'secret'}):
            environment = child_environment()
        self.assertFalse(any('secret' == value for value in environment.values()))
        self.assertIn('HOME', environment)

    def test_duplicate_and_nonfinite_json_rejected(self):
        for data in ['{"decision":{"type":"final","content":"a","content":"b"}}',
                     '{"decision":NaN}']:
            with self.assertRaises(ValueError):
                cp.parse_decision(data, [])
