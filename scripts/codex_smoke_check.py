"""Synthetic CoC fixture using the real game tool gateway and check resolver."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch


async def run_check(provider):
    from app import keeper, legacy_commands
    from app.agents.tool_gateway import make_tool_executor
    from app.models import Character, GroupState
    from app.repositories import group_state
    from app.services import turn_context

    state = GroupState(group_id='codex-smoke-check', active=True)
    state.characters['player'] = Character(name='Marco', owner_id='player',
                                          skills={'偵查': 70}, luck=0)
    state.scenario_text = 'A sealed desk contains a faded document. A successful Spot Hidden check reveals the date 1925.'
    group_state.save_state(state)  # the first save assigns the timeline
    keeper._ensure_turn_timeline(state)
    check_tool = next(t for t in keeper.TOOLS if t['name'] == 'skill_check')
    receipts = []
    gateway = make_tool_executor(state, [], [], 'player', [])

    async def execute(name, arguments):
        result = await gateway(name, {**arguments, '_player_action': '我要檢查桌上的文件'})
        receipts.append({'name': name, 'result': result})
        return {**result, 'current_turn_state': turn_context.current_state(state)}

    pending_text = await provider.run_conversation(
        'You are the Keeper. This fixture requires one regular 偵查 skill_check for investigator Marco '
        'to examine a faded desk document. Do not disclose its date before success. '
        'After a pending tool receipt, final.content must tell the player in Traditional Chinese to use /coc check. '
        'Never roll the player dice yourself.',
        json.dumps(turn_context.current_state(state), ensure_ascii=False),
        [check_tool], [], '我要檢查桌上的文件', execute, 4,
    )
    assert len(receipts) == 1 and receipts[0]['name'] == 'skill_check', 'Expected exactly one check tool'
    assert receipts[0]['result'].get('pending'), 'Check was not registered'
    persisted = keeper.load_state(state.group_id)
    assert 'player' in persisted.pending_checks, 'Pending check was not persisted'
    assert '/coc check' in pending_text, 'Missing player check instruction'
    assert '1925' not in pending_text, 'Premature clue disclosure'
    # Only the RNG is fixed for reproducibility. Resolution/persistence are real.
    with patch('app.dice.roll_percentile_with_dice_pool', return_value=20) as dice:
        resolved = await asyncio.to_thread(legacy_commands._resolve_check_deterministically,
                                          state.group_id, 'player', '/coc check')
    assert dice.call_count == 1, 'Player dice were rerolled'
    assert resolved.should_finalize and resolved.resolved_event, 'Resolution did not complete'
    persisted = keeper.load_state(state.group_id)
    assert not persisted.pending_checks and not persisted.pending_luck_decisions, 'Uncleared pending state'

    async def no_more_tools(*_args):
        raise AssertionError('Narration must not roll again')

    narration = await provider.run_conversation(
        'Narrate the resolved result in Traditional Chinese. The successful check reveals the document date 1925. '
        'Dice are already resolved; never request another roll. Do not invent other clues.',
        json.dumps({'current_state': turn_context.current_state(persisted),
                    'resolved': resolved.resolved_event}, ensure_ascii=False),
        [], [], '請接續敘事。', no_more_tools, 1,
    )
    assert '1925' in narration, 'Missing verified clue'
    assert '/coc check' not in narration, 'Narration requested a reroll'
    return {'ok': True, 'scenario': 'check', 'tools': len(receipts), 'python_dice_rolls': dice.call_count,
            'pending_cleared': True, 'pending_text': pending_text, 'narration': narration}
