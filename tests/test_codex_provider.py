import asyncio
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app import config
from app.providers import codex_provider as cp
from app.providers import codex_request_owner
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

    async def test_a_turns_retry_may_repeat_a_lookup_but_not_a_mutation(self):
        """docs/specs/bug/rerun6_combat_friction_design_spec.md: the Executor's retry of a turn that changed nothing
        re-read the enemy's stat block with the same arguments and the turn failed as a duplicate."""
        lookup = {**TOOL, 'name': 'get_enemy_stat_block'}

        @cp.with_codex_turn
        async def turn(tools, first, second):
            self.transport.request.side_effect = [first, final('incomplete'), second, final()]
            await cp.run_conversation('static', 'state', tools, [], 'go', self.tool, 6)
            return await cp.run_conversation('static', 'state', tools, [], 'go', self.tool, 6)

        lookup_call = call(name='get_enemy_stat_block')
        self.assertEqual(await turn([lookup], lookup_call, lookup_call), 'done')
        self.assertEqual(self.tool.await_count, 2)
        self.tool.reset_mock()
        with self.assertRaisesRegex(CodexError, 'duplicate_tool'):
            await turn([TOOL], call(), call())  # a mutation (or a roll) is never sent twice in one turn
        self.assertEqual(self.tool.await_count, 1)
        self.tool.reset_mock()
        self.transport.request.side_effect = [lookup_call, lookup_call]
        with self.assertRaisesRegex(CodexError, 'duplicate_tool'):  # the same look-up twice in one conversation
            await cp.run_conversation('static', 'state', [lookup], [], 'go', self.tool, 6)
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
        owner = codex_request_owner.RequestOwner()
        with patch.object(codex_request_owner, 'OWNER', owner), patch.object(config, 'CODEX_MAX_CONCURRENCY', 1):
            lease = await owner.acquire(codex_request_owner.deadline())
            with patch.object(config, 'CODEX_TIMEOUT', .01), self.assertRaises(TimeoutError):
                await self.run_provider([final()])
            lease.release()
        self.assertEqual(owner.in_use, 0)
        self.transport.request.assert_not_awaited()

    async def test_queued_cancellation_does_not_release_unowned_slot(self):
        owner = codex_request_owner.RequestOwner()
        with patch.object(codex_request_owner, 'OWNER', owner), patch.object(config, 'CODEX_MAX_CONCURRENCY', 1):
            lease = await owner.acquire(codex_request_owner.deadline())
            task = asyncio.create_task(cp.run_conversation('', '', [], [], '', self.tool, 1))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(owner.in_use, 1)
            lease.release()
        self.assertEqual(owner.in_use, 0)
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
        async def blocked(*_args, **_kwargs):
            started.set()
            await asyncio.Future()
        self.transport.request.side_effect = blocked
        owner = codex_request_owner.RequestOwner()
        with patch.object(codex_request_owner, 'OWNER', owner):
            task = asyncio.create_task(cp.run_conversation('', '', [], [], '', self.tool, 1))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(owner.in_use, 0)
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


class CodexAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.transport = AsyncMock()
        self.factory = patch.object(cp, 'ExecTransport', return_value=self.transport)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def test_text_analysis_projects_strict_schema_and_restores_dynamic_maps(self):
        tool = {'name': 'report', 'description': 'Report fields', 'input_schema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'luck': {'type': 'integer'},
                'skills': {'type': 'object', 'additionalProperties': {'type': 'integer'}},
                'extra_fields': {'type': 'object', 'additionalProperties': {}},
            },
            'required': ['name', 'skills', 'extra_fields'],
        }}
        self.transport.request.return_value = json.dumps({
            'name': 'Avery', 'luck': None,
            'skills': [{'key': '偵查', 'value': 55}],
            'extra_fields': [{'key': '信念', 'value': '查明真相'}],
        }, ensure_ascii=False)

        result = cp.analyze_text('visible source', tool, 'Extract the fields')

        self.assertEqual(result, {'name': 'Avery', 'skills': {'偵查': 55},
                                  'extra_fields': {'信念': '查明真相'}})
        projected = self.transport.request.call_args.args[1]
        self.assertEqual(projected['required'], ['name', 'luck', 'skills', 'extra_fields'])
        self.assertFalse(projected['additionalProperties'])
        self.assertEqual(projected['properties']['skills']['type'], 'array')
        self.assertEqual(projected['properties']['extra_fields']['items']['properties']['value']['type'], 'string')

    def test_opening_check_nullable_object_survives_strict_projection(self):
        from jsonschema import Draft202012Validator

        from app import scenario_intro

        tool = scenario_intro._REPORT_TOOL
        for opening_check in (
            None,
            {'type': 'skill', 'skill': '偵查', 'loss_success': None,
             'loss_failure': None, 'reason': None},
        ):
            with self.subTest(opening_check=opening_check):
                wire = {'found': True, 'text': 'Opening', 'page': None,
                        'opening_check': opening_check}
                self.transport.request.return_value = json.dumps(wire, ensure_ascii=False)
                result = cp.analyze_text('Opening source', tool, 'Extract opening')
                projected = self.transport.request.call_args.args[1]
                Draft202012Validator(projected).validate(wire)
                self.assertEqual(result['opening_check'],
                                 None if opening_check is None else {'type': 'skill', 'skill': '偵查'})

    def test_bad_json_and_original_schema_violation_return_none(self):
        tool = {'name': 'report', 'description': 'Report', 'input_schema': {
            'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count'],
        }}
        for output in ['not json', '{"count":"not a number"}']:
            with self.subTest(output=output):
                self.transport.request.return_value = output
                self.assertIsNone(cp.analyze_text('source', tool, 'extract'))

    def test_independent_sync_calls_share_the_configured_concurrency_limit(self):
        counter_lock = threading.Lock()
        active = 0
        maximum = 0

        async def request(*_args, **_kwargs):
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            await asyncio.sleep(0.03)
            with counter_lock:
                active -= 1
            return '{"label":"ok"}'

        self.transport.request.side_effect = request
        tool = {'name': 'report', 'description': 'Report', 'input_schema': {
            'type': 'object', 'properties': {'label': {'type': 'string'}}, 'required': ['label'],
        }}
        with patch.object(config, 'CODEX_MAX_CONCURRENCY', 1):
            threads = [threading.Thread(target=cp.analyze_text, args=('source', tool, 'extract'))
                       for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(maximum, 1)
