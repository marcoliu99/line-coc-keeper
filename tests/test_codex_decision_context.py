import json
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from app.keeper_tools import registry as tool_registry
from app.models import Character, GroupState
from app.providers import codex_provider
from app.providers.codex_transport import TOOL_CALL_EXAMPLE, CodexError
from app.services import turn_context


class DecisionContextTests(unittest.TestCase):
    def setUp(self):
        self.state = GroupState(group_id='context')
        self.state.characters['a'] = Character(name='A', owner_id='a')
        self.state.characters['b'] = Character(name='B', owner_id='b')
        self.state.pending_checks['a'] = {'check_id': 'check-a', 'action_context': 'read document'}
        self.tools = [t for t in tool_registry.TOOLS if t['name'] in {
            'skill_check', 'sanity_check', 'clear_pending_check', 'add_carried_item'}]

    def test_busy_actor_is_excluded_but_other_actor_and_independent_actions_remain(self):
        original = deepcopy(self.tools)
        scoped = turn_context.check_creation_tools(self.state, self.tools)
        for tool in scoped:
            if tool['name'] in {'skill_check', 'sanity_check'}:
                self.assertEqual(tool['input_schema']['properties']['investigator']['enum'], ['B'])
        self.assertIn('clear_pending_check', {t['name'] for t in scoped})
        self.assertIn('add_carried_item', {t['name'] for t in scoped})
        self.assertEqual(self.tools, original)

    def test_luck_is_blocking_and_clear_restores_tools_on_next_request(self):
        self.state.pending_luck_decisions['b'] = {'decision_id': 'luck-b'}
        scoped = turn_context.check_creation_tools(self.state, self.tools)
        self.assertNotIn('skill_check', {t['name'] for t in scoped})
        self.state.pending_checks.clear()
        restored = turn_context.check_creation_tools(self.state, self.tools)
        tool = next(t for t in restored if t['name'] == 'skill_check')
        self.assertEqual(tool['input_schema']['properties']['investigator']['enum'], ['A'])

    def test_existing_allowlist_is_not_broadened(self):
        tool = deepcopy(next(t for t in self.tools if t['name'] == 'skill_check'))
        tool['input_schema']['properties']['investigator']['enum'] = ['A']
        self.assertEqual(turn_context.check_creation_tools(self.state, [tool]), [])

    def test_waiting_candidate_uses_exact_identity_and_luck_precedence(self):
        pending = turn_context.executor_decision_context(self.state, 'a')
        candidate = pending['waiting_resolution_candidate']
        self.assertEqual(candidate['check_id'], 'check-a')
        self.assertEqual(candidate['disposition'], 'await_check')
        self.assertEqual(candidate['evidence_refs'], ['state'])
        self.state.pending_luck_decisions['a'] = {'decision_id': 'luck-a'}
        candidate = turn_context.executor_decision_context(self.state, 'a')['waiting_resolution_candidate']
        self.assertEqual(candidate['disposition'], 'await_luck')
        self.assertEqual(candidate['check_id'], 'luck-a')
        self.assertIsNone(turn_context.executor_decision_context(self.state, 'b')['waiting_resolution_candidate'])

    def test_protocol_example_is_executable_json_not_a_native_tool_call(self):
        tool = next(t for t in self.tools if t['name'] == 'skill_check')
        decision = codex_provider.parse_decision(TOOL_CALL_EXAMPLE, [tool])
        self.assertEqual(decision['name'], 'skill_check')
        self.assertEqual(decision['arguments'], {'investigator': 'Marco', 'skill': '偵查'})


class FreshSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_rechecks_argument_constraints_not_only_tool_name(self):
        tool = {'name': 'skill_check', 'input_schema': {'type': 'object', 'properties': {
            'investigator': {'type': 'string', 'enum': ['A', 'B']}}, 'required': ['investigator']}}
        narrowed = deepcopy(tool)
        narrowed['input_schema']['properties']['investigator']['enum'] = ['B']
        transport = AsyncMock()
        transport.request.return_value = json.dumps({'decision': {'type': 'tool_call',
            'name': 'skill_check', 'arguments_json': '{"investigator":"A"}'}})
        callback = AsyncMock()
        tools = iter([[tool], [narrowed]])
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport), \
             self.assertRaisesRegex(CodexError, 'schema_changed'):
            await codex_provider.run_conversation('', '', [tool], [], '', callback, 3,
                tools_for_request=lambda: next(tools))
        callback.assert_not_awaited()

    async def test_context_is_refreshed_after_tool_result(self):
        value = {'version': 1}
        tool = {'name': 'change', 'input_schema': {'type': 'object', 'properties': {}}}
        transport = AsyncMock()
        transport.request.side_effect = [json.dumps({'decision': {'type': 'tool_call',
            'name': 'change', 'arguments_json': '{}'}}),
            json.dumps({'decision': {'type': 'final', 'content': 'done'}})]
        async def execute(*_args):
            value['version'] = 2
            return {'ok': True}
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            await codex_provider.run_conversation('', '', [tool], [], '', execute, 3,
                                                 decision_context=lambda: value)
        prompts = [json.loads(c.args[0]) for c in transport.request.call_args_list]
        self.assertEqual([p['decision_context']['version'] for p in prompts], [1, 2])

    async def test_incomplete_without_dispatch_gets_one_bounded_retry(self):
        tool = {'name': 'change', 'input_schema': {'type': 'object', 'properties': {}}}
        incomplete = json.dumps({'decision': {'type': 'final', 'content': json.dumps({
            'disposition': 'incomplete', 'reason': 'waiting for unsent tool'})}})
        transport = AsyncMock()
        transport.request.side_effect = [incomplete, json.dumps({'decision': {'type': 'tool_call',
            'name': 'change', 'arguments_json': '{}'}}),
            json.dumps({'decision': {'type': 'final', 'content': 'done'}})]
        callback = AsyncMock(return_value={'ok': True})
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            result = await codex_provider.run_conversation('', '', [tool], [], '', callback, 4,
                                                          response_stage='executor')
        self.assertEqual(result, 'done')
        callback.assert_awaited_once_with('change', {})
        prompt = json.loads(transport.request.call_args_list[1].args[0])
        self.assertIn('No host tool call', prompt['current_conversation'][0]['instruction'])

    async def test_real_blocker_remains_incomplete_after_one_retry(self):
        tool = {'name': 'change', 'input_schema': {'type': 'object', 'properties': {}}}
        content = json.dumps({'disposition': 'incomplete', 'reason': 'scenario does not support it'})
        transport = AsyncMock()
        transport.request.return_value = json.dumps({'decision': {'type': 'final', 'content': content}})
        callback = AsyncMock()
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            result = await codex_provider.run_conversation('', '', [tool], [], '', callback, 6,
                                                          response_stage='executor')
        self.assertEqual(result, content)
        self.assertEqual(transport.request.await_count, 2)
        callback.assert_not_awaited()

    async def test_no_retry_without_room_for_tool_and_final(self):
        tool = {'name': 'change', 'input_schema': {'type': 'object', 'properties': {}}}
        content = json.dumps({'disposition': 'incomplete'})
        transport = AsyncMock()
        transport.request.return_value = json.dumps({'decision': {'type': 'final', 'content': content}})
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            await codex_provider.run_conversation('', '', [tool], [], '', AsyncMock(), 2,
                                                 response_stage='executor')
        self.assertEqual(transport.request.await_count, 1)

    async def test_final_feedback_repairs_handoff_without_replaying_tool(self):
        tool = {'name': 'change', 'input_schema': {'type': 'object', 'properties': {}}}
        transport = AsyncMock()
        transport.request.side_effect = [json.dumps({'decision': {'type': 'tool_call',
            'name': 'change', 'arguments_json': '{}'}}),
            json.dumps({'decision': {'type': 'final', 'content': 'wrong handoff'}}),
            json.dumps({'decision': {'type': 'final', 'content': 'waiting with receipt'}})]
        callback = AsyncMock(return_value={'ok': True})
        seen = []
        def feedback(content):
            seen.append(content)
            return {'validation_code': 'unfinished_check_or_luck', 'reason': 'pending remains'}
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            result = await codex_provider.run_conversation('', '', [tool], [], '', callback, 4,
                response_stage='executor', final_feedback=feedback)
        self.assertEqual(result, 'waiting with receipt')
        self.assertEqual(seen, ['wrong handoff'])
        callback.assert_awaited_once()
        prompt = json.loads(transport.request.call_args_list[-1].args[0])
        self.assertEqual(prompt['current_conversation'][-1]['validation_feedback']['validation_code'],
                         'unfinished_check_or_luck')

    async def test_final_feedback_never_forces_a_tool_or_unbounded_retry(self):
        transport = AsyncMock()
        transport.request.return_value = json.dumps({'decision': {'type': 'final', 'content': 'blocked'}})
        callback = AsyncMock()
        with patch.object(codex_provider.config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport):
            result = await codex_provider.run_conversation('', '', [], [], '', callback, 6,
                final_feedback=lambda _: {'reason': 'still blocked'})
        self.assertEqual(result, 'blocked')
        self.assertEqual(transport.request.await_count, 2)
        callback.assert_not_awaited()
