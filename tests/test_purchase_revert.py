"""The retired purchase workflow stays unavailable while old saves remain readable."""

from app import help_actions, keeper
from app.commands.router import is_known_coc_command
from app.models import Character, GroupState


def test_purchase_tool_and_commands_are_no_longer_exposed():
    assert 'purchase_items' not in {tool['name'] for tool in keeper.TOOLS}
    assert not any(action.command in {'purchase', 'purchases', 'funds'} for action in help_actions.ACTIONS)
    for command in ('purchase', 'purchases', 'funds'):
        assert not is_known_coc_command(command)


def test_legacy_cash_and_receipts_survive_state_roundtrip_without_active_tool():
    state = GroupState(group_id='purchase-revert-test')
    state.characters['u1'] = Character(name='Marco', owner_id='u1', cash_balances={'USD': 4000})
    state.commerce = {'transactions': {'legacy-quote': {'status': 'quoted', 'owner_id': 'u1'}}}
    restored = GroupState.from_dict(state.to_dict())

    assert restored.characters['u1'].cash_balances == {'USD': 4000}
    assert restored.commerce == state.commerce
    assert '現金：' not in restored.characters['u1'].sheet_text()
