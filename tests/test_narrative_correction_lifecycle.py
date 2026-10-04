import unittest
from unittest.mock import AsyncMock, patch

from app import config, db, keeper
from app.agents import (
    assistant,
    context_builder,
    executor,
    narrator,
    supervisor,
    tool_gateway,
)
from app.commands.handlers import correct
from app.models import GroupState
from app.providers import registry
from app.repositories.group_state import load_state
from app.services import narrative_corrections as corrections


class CorrectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def state(self):
        state = GroupState(group_id='correction-lifecycle-' + self._testMethodName, timeline_id='timeline-test')
        state.narrative_corrections = [
            {'id': str(i), 'timeline_id': state.timeline_id, 'status': 'approved',
             'target_message_id': str(i), 'resolution': f'correction-{i}'} for i in range(30)
        ]
        return state

    def test_old_effective_decisions_survive_pruning_and_reload(self):
        state = self.state()
        corrections.prune_closed(state)
        corrections.save(state)
        restored = load_state(state.group_id)
        context, overflow = corrections.projection(restored)
        self.assertFalse(overflow)
        self.assertIn('"resolution": "correction-0"', context)
        self.assertEqual(len(restored.narrative_corrections), 30)
        archive = db.get_json('narrative_correction_archive', f'{state.group_id}:{state.timeline_id}:0')
        self.assertEqual(archive['resolution'], 'correction-0')

    async def test_overflow_stops_supervisor_before_context_or_effects(self):
        state = self.state()
        for r in state.narrative_corrections:
            r['resolution'] = 'X' * 1000
        with patch.object(keeper, '_ensure_turn_timeline', return_value=state.timeline_id), \
                patch.object(context_builder, 'build_context', AsyncMock()) as build:
            reply, private, images = await supervisor.run_turn(state, 'p', 'P', 'look', None, 'player', state.group_id)
        build.assert_not_awaited()
        self.assertIn('超出', reply)
        self.assertEqual((private, images), ([], []))

    async def test_all_live_agents_receive_correction_without_system_injection(self):
        state = self.state()
        with patch.object(context_builder, 'SCENARIO_RAG_ENABLED', False):
            message = await context_builder.build_context(state, 'p', 'P', 'look', None, 'player', state.group_id)
        provider = AsyncMock()
        provider.OPENAI_MODEL = 'fake'
        provider.run_conversation.return_value = '{}'
        with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, openai=provider):
            await executor.run_executor(message)
        self.assertIn('correction-0', provider.run_conversation.call_args.args[4])
        self.assertNotIn('correction-0', provider.run_conversation.call_args.args[0])
        for kind in ('player_action', 'opening_fallback', 'resolved_check_followup'):
            message.payload['turn_kind'] = kind
            message.payload['resolved_check_context'] = {'investigator': 'P', 'roll': 30, 'outcome': 'success'}
            with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, openai=provider):
                await narrator.run_narrator(message)
            self.assertIn('correction-0', provider.run_conversation.call_args.args[4])
        with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.dict(registry.CONVERSATION_PROVIDERS, openai=provider), \
                patch.object(keeper, '_ensure_turn_timeline', return_value=state.timeline_id), \
                patch.object(keeper, '_commit_kp_ooc_turn_result', return_value=True), \
                patch.object(assistant.guard, 'enforce_narrative_safety', AsyncMock(return_value='ok')):
            await assistant.run_assistant(message)
        self.assertIn('correction-0', provider.run_conversation.call_args.args[4])
        self.assertIsNone(provider.run_conversation.call_args.kwargs['previous_response_id'])
        self.assertEqual(state.log, [])

    async def test_only_kp_scoped_pending_hold_blocks_mutation(self):
        state = GroupState(group_id='hold', timeline_id='t')
        report = {'id': 'a', 'timeline_id': 't', 'status': 'pending', 'issue': '地下室不存在'}
        state.narrative_corrections = [report]
        self.assertFalse(corrections.blocking_reply(state, '地下室'))
        report['hold_scope'] = ['地下室', 'basement']
        execute = tool_gateway.make_tool_executor(state, [], [], 'player', [])
        with patch.object(keeper, '_execute_tool', return_value={'ok': True}) as mutate:
            result = await execute('record_established_fact', {'fact': 'basement has enemies'})
            self.assertFalse(result['ok'])
            mutate.assert_not_called()
            result = await execute('record_established_fact', {'fact': '書房門打開'})
            self.assertTrue(result['ok'])
            mutate.assert_called_once()
        report['status'] = 'rejected'
        self.assertFalse(corrections.blocking_reply(state, '地下室'))

    def test_message_receipt_is_bound_to_conversation_and_timeline(self):
        state = GroupState(group_id='receipt', timeline_id='a')
        corrections.record_message(state, '123456', '原始敘事')
        self.assertEqual(corrections.target_receipt(state, '123456')['excerpt'], '原始敘事')
        state.timeline_id = 'b'
        self.assertIsNone(corrections.target_receipt(state, '123456'))
        state.group_id = 'different'
        self.assertIsNone(corrections.target_receipt(state, '123456'))

    async def test_kp_supersession_keeps_audit_and_invalidates_provider_chain(self):
        state = self.state()
        state.kp_assistant_user_id = 'kp'
        state.openai_previous_response_id = 'old-chain'
        reply = AsyncMock()
        with patch.object(correct, 'load_state', return_value=state), patch.object(correct.correction_summary, 'schedule') as rebuild:
            await correct.handle_correct_command(state.group_id, 'player', reply,
                                                 ['/coc', 'correct', 'supersede', '0', '1'])
            self.assertEqual(state.narrative_corrections[0]['status'], 'approved')
            await correct.handle_correct_command(state.group_id, 'kp', reply,
                                                 ['/coc', 'correct', 'supersede', '0', '1'])
        rebuild.assert_called_once_with(state.group_id)
        self.assertEqual(state.narrative_corrections[1]['summary_rebuild_status'], 'pending')
        self.assertEqual(state.narrative_corrections[0]['status'], 'superseded')
        self.assertEqual(state.openai_previous_response_id, '')
        archived = db.get_json('narrative_correction_archive', f'{state.group_id}:{state.timeline_id}:0')
        self.assertEqual(archived['superseded_by'], '1')
        self.assertNotIn('"resolution": "correction-0"', corrections.projection(state)[0])

    async def test_missing_target_never_creates_a_report(self):
        state = GroupState(group_id='unknown-target', timeline_id='t')
        reply = AsyncMock()
        with patch.object(correct, 'load_state', return_value=state), patch.object(corrections, 'save') as save:
            await correct.handle_correct_command(state.group_id, 'player', reply,
                                                 ['/coc', 'correct', '123456', '疑點'])
        self.assertIn('無法確認目標', reply.call_args.args[0])
        save.assert_not_called()
        self.assertEqual(state.narrative_corrections, [])
