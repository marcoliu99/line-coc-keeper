"""Exercise actual Executor, Narrator, game gateway and check persistence."""
import json
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app import config, keeper, turn_commit
from app.agents import supervisor
from app.commands.handlers import checks as check_commands
from app.models import Character, GroupState
from app.providers import codex_provider
from app.repositories import group_state
from app.repositories.group_state import load_state
from app.services.turn_context import character_id


class CodexPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_player_check_then_resolved_narration_uses_python_state(self):
        state = GroupState(group_id='codex-pipeline-' + uuid.uuid4().hex, active=True)
        state.characters['u'] = Character(name='Marco', owner_id='u', skills={'偵查': 70}, luck=0)
        state.scenario_text = '書桌的文件藏有日期 1925；偵查成功才能辨認。'
        group_state.save_state(state)  # storage assigns the timeline on the first save
        turn_commit.ensure_turn_timeline(state)
        tool_decisions = []
        stages = []

        async def request(prompt, _schema, **_kwargs):
            payload = json.loads(prompt)
            stages.append(payload['response_stage'])
            if payload['response_stage'] == 'executor':
                if not payload['current_conversation']:
                    tool_decisions.append('skill_check')
                    return json.dumps({'decision': {'type': 'tool_call', 'name': 'skill_check',
                        'arguments_json': json.dumps({'investigator': 'Marco', 'skill': '偵查',
                                                     'action_context': '檢查桌上的文件'})}})
                receipt = payload['current_conversation'][0]['result']
                self.assertTrue(receipt['pending'])
                self.assertIn('evidence_ref', receipt)
                decision = {'disposition': 'await_check', 'actor_character_id': character_id(state, 'u'),
                    'waiting_for': character_id(state, 'u'), 'check_id': state.pending_checks['u']['check_id'],
                    'reason': '辨認文件', 'evidence_refs': ['tool:1']}
                content = json.dumps(decision)
            else:
                content = '請使用 /coc check 完成偵查檢定。' if state.pending_checks else '你辨認出文件日期：1925 年。'
            return json.dumps({'decision': {'type': 'final', 'content': content}})

        transport = AsyncMock()
        transport.request.side_effect = request
        with patch.object(config, 'CODEX_TRANSPORT', 'exec'), \
             patch.object(codex_provider, 'ExecTransport', return_value=transport), \
             patch.object(config, 'LLM_PROVIDER', 'codex'), \
             patch('app.dice.roll_percentile_with_dice_pool', return_value=20) as dice:
            reply, _, _ = await supervisor.run_turn(state, 'u', 'Marco', '我要檢查桌上的文件', None, 'player', state.group_id)
            self.assertIn('/coc check', reply)
            self.assertEqual(dice.call_count, 0)
            self.assertEqual(tool_decisions, ['skill_check'])
            self.assertIn('u', load_state(state.group_id).pending_checks)
            resolved = check_commands.resolve_check(state.group_id, 'u', '/coc check')
            self.assertTrue(resolved.should_finalize)
            state = load_state(state.group_id)
            self.assertFalse(state.pending_checks)
            reply, _, _ = await supervisor.run_turn(state, 'u', 'Marco', resolved.keeper_message,
                None, 'player', state.group_id, turn_kind='resolved_check_followup',
                resolved_check_context=resolved.resolved_event)
            self.assertIn('1925', reply)
            self.assertNotIn('/coc check', reply)
            self.assertEqual(dice.call_count, 1)
            self.assertEqual(tool_decisions, ['skill_check'])
            self.assertIn('executor', stages)
            self.assertIn('narrator', stages)

    async def test_pending_pickup_repairs_missing_tool_or_wrong_final_without_replay(self):
        for missing_tool in (True, False):
            with self.subTest(missing_tool=missing_tool):
                state = GroupState(group_id='codex-pickup-' + uuid.uuid4().hex, active=True)
                state.characters['u'] = Character(name='Marco', owner_id='u', skills={'偵查': 70})
                state.scenario_text = '桌上黃銅鑰匙可以直接拾取，文件需偵查檢定。'
                group_state.save_state(state)
                turn_commit.ensure_turn_timeline(state)
                keeper._execute_tool(state, 'skill_check', {'investigator': 'Marco', 'skill': '偵查',
                    'action_context': '辨認文件'}, [], [], speaker_role='player')
                old_pending = dict(state.pending_checks['u'])
                dispatched = []
                rejected = []

                async def request(prompt, _schema, missing_tool=missing_tool, state=state,
                                  dispatched=dispatched, rejected=rejected, **_kwargs):
                    payload = json.loads(prompt)
                    if payload['response_stage'] != 'executor':
                        return json.dumps({'decision': {'type': 'final',
                            'content': '黃銅鑰匙已收進背包；文件偵查仍待擲。'}})
                    transcript = payload['current_conversation']
                    feedback = [x for x in transcript if 'validation_feedback' in x]
                    receipts = [x for x in transcript if x.get('name') == 'add_carried_item']
                    if (not missing_tool and not transcript) or (missing_tool and feedback and not receipts):
                        dispatched.append('add_carried_item')
                        return json.dumps({'decision': {'type': 'tool_call', 'name': 'add_carried_item',
                            'arguments_json': json.dumps({'investigator': 'Marco', 'item': '黃銅鑰匙'})}})
                    if not feedback:
                        decision = {'disposition': 'resolved_without_check',
                            'actor_character_id': character_id(state, 'u'), 'evidence_refs': ['state', 'scenario_context']}
                        if receipts:
                            decision['evidence_refs'].append('tool:1')
                    else:
                        rejected.append(feedback[0]['validation_feedback']['validation_code'])
                        decision = dict(payload['decision_context']['waiting_resolution_candidate'])
                        decision['evidence_refs'] = ['state', 'tool:1']
                    return json.dumps({'decision': {'type': 'final', 'content': json.dumps(decision)}})

                transport = AsyncMock()
                transport.request.side_effect = request
                with patch.object(config, 'CODEX_TRANSPORT', 'exec'), \
                     patch.object(codex_provider, 'ExecTransport', return_value=transport), \
                     patch.object(config, 'LLM_PROVIDER', 'codex'), \
                     patch('app.dice.roll_percentile_with_dice_pool') as dice:
                    reply, _, _ = await supervisor.run_turn(state, 'u', 'Marco',
                        '保留文件檢定，拿起黃銅鑰匙', None, 'player', state.group_id)
                self.assertEqual(dispatched, ['add_carried_item'])
                self.assertEqual(rejected, ['unfinished_check_or_luck'])
                self.assertNotIn('尚未完整處理', reply)
                actual = load_state(state.group_id)
                self.assertEqual(actual.pending_checks['u'], old_pending)
                self.assertEqual(actual.get_active_character('u').carried_items.count('黃銅鑰匙'), 1)
                dice.assert_not_called()
