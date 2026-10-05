"""Isolated, real Supervisor fixtures used by opt-in OAuth evaluation."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

from app import tool_dispatch, turn_commit
from app.repositories.group_state import load_state


async def run_pipeline(kind='check_success'):
    from app.agents import supervisor
    from app.commands.handlers import checks as check_commands
    from app.models import Character, GroupState
    from app.repositories import group_state

    state = GroupState(group_id='codex-eval-' + uuid.uuid4().hex, active=True)
    state.characters['player'] = Character(name='Marco', owner_id='player',
        skills={'偵查': 70}, luck=0, carried_items=['筆記本'])
    state.scenario_text = (
        '測試場景：調查員 Marco 已在書房，桌上有一份字跡模糊的文件及一把普通黃銅鑰匙。'
        '文件必须先通過一般難度偵查檢定，才能辨認唯一線索：日期 1925 年；失敗則無法辨認日期。'
        '桌上的黃銅鑰匙可以直接拾取，不需檢定。房內沒有敵人、危險或其他線索。'
    )
    state.narrative_locations['player'] = '書房'
    group_state.save_state(state)  # the first save assigns the timeline
    turn_commit.ensure_turn_timeline(state)
    rolls = []

    def roll(*_args, **_kwargs):
        value = 95 if kind == 'check_failure' else 20
        rolls.append(value)
        return value

    actions = {
        'check_success': '我要檢查桌上字跡模糊的文件，辨認日期。',
        'check_failure': '我要檢查桌上字跡模糊的文件，辨認日期。',
        'pickup': '拿起桌上的黃銅鑰匙，收進背包。',
        'pending_pickup': '文件的偵查檢定先保留；拿起桌上的黃銅鑰匙，收進背包。',
        'ooc': '場外：我該怎麼擲骰完成待處理的檢定？',
        'pending': '我要檢查桌上字跡模糊的文件，辨認日期。',
    }
    if kind in {'pending', 'pending_pickup'}:
        receipt = tool_dispatch.execute_tool(state, 'skill_check', {'investigator': 'Marco',
            'skill': '偵查', 'action_context': actions['pending']}, [], [], speaker_role='player')
        assert receipt.get('pending'), 'Fixture setup did not create a pending check'
    old_check = dict(state.pending_checks.get('player') or {})
    with patch('app.dice.roll_percentile_with_dice_pool', side_effect=roll):
        reply, private, images = await supervisor.run_turn(state, 'player', 'Marco', actions[kind],
            None, 'player', state.group_id)
        state = load_state(state.group_id)
        failures = []
        first_reply = reply
        if rolls:
            failures.append('unsolicited_player_roll')
        if kind.startswith('check_'):
            if not state.pending_checks.get('player'):
                failures.append('missing_pending_check')
            elif '1925' in reply:
                failures.append('premature_clue_disclosure')
            if not failures:
                resolved = await asyncio.to_thread(check_commands.resolve_check,
                    state.group_id, 'player', '/coc check')
                state = load_state(state.group_id)
                if not resolved.should_finalize or not resolved.resolved_event:
                    failures.append('resolution_not_final')
                else:
                    reply, private, images = await supervisor.run_turn(state, 'player', 'Marco',
                        resolved.keeper_message, None, 'player', state.group_id,
                        turn_kind='resolved_check_followup', resolved_check_context=resolved.resolved_event)
                    state = load_state(state.group_id)
                if len(rolls) != 1:
                    failures.append('wrong_roll_count')
                if state.pending_checks or state.pending_luck_decisions:
                    failures.append('pending_not_cleared')
                if kind == 'check_success' and '1925' not in reply:
                    failures.append('missing_verified_clue')
                if kind == 'check_failure' and '1925' in reply:
                    failures.append('failed_check_disclosed_clue')
                if '/coc check' in reply:
                    failures.append('reroll_instruction')
        elif kind in {'pickup', 'pending_pickup'}:
            if not any('鑰匙' in item for item in state.get_active_character('player').carried_items):
                failures.append('item_not_acquired')
            if kind == 'pending_pickup':
                if state.pending_checks.get('player') != old_check:
                    failures.append('pending_check_replaced')
            elif state.pending_checks:
                failures.append('unnecessary_check')
        elif kind == 'pending':
            if state.pending_checks.get('player') != old_check:
                failures.append('pending_check_replaced')
            if '1925' in reply:
                failures.append('premature_clue_disclosure')
        elif kind == 'ooc':
            if state.pending_checks or len(state.get_active_character('player').carried_items) != 1:
                failures.append('ooc_changed_state')
            if '1925' in reply:
                failures.append('ooc_spoiler')
        if not reply.strip():
            failures.append('empty_reply')
        if any(term in reply for term in ('尚未完整處理', '目前無法繼續', '一時語塞')):
            failures.append('fallback_reply')
        return {'ok': not failures, 'kind': kind, 'failures': failures,
                'python_rolls': len(rolls), 'first_reply': first_reply, 'reply': reply,
                'private_count': len(private), 'image_count': len(images)}
