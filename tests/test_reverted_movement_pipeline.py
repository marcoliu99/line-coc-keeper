"""The restored player turn uses the legacy map hint and no arrival tool."""

from app import tool_dispatch
from app.agents import intent_router
from app.domain.models import AgentMessage
from app.models import Character, GroupState
from app.services import map_service


def test_executor_no_longer_offers_the_s2_arrival_tool():
    offered = {tool['name'] for tool in tool_dispatch.tools_for_speaker_role('player')}
    assert 'commit_movement' not in offered


def test_mapless_travel_falls_through_to_keeper_narration():
    state = GroupState(group_id='revert-movement', active=True, game_started=True)
    state.characters['u'] = Character(name='Marco', owner_id='u')
    before = state.to_dict()

    resolved = map_service._resolve_map_action_core(state, 'u', '我去圖書館')

    assert resolved.context is None
    assert state.to_dict() == before
    assert intent_router.classify_intent(AgentMessage({
        'speaker_role': 'player', 'text': '我去圖書館',
    })) == 'GAMEPLAY_ACTION'
