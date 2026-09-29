"""The shortened equipment policy keeps acquisition and state boundaries."""

from app import keeper
from app.models import GroupState


def test_equipment_policy_has_three_rules_and_no_implicit_payment() -> None:
    prompt = keeper._build_static_prompt(GroupState(group_id="equipment-prompt"))
    policy = prompt.split("# Equipment Consistency\n", 1)[1].split("\n# ", 1)[0]

    assert sum(line.startswith("- ") for line in policy.splitlines()) == 3
    assert "Ordinary personal items" in policy
    assert "period/technology" in policy
    assert "occupation/background or established events" in policy
    assert "legal/regional availability" in policy
    assert "A recorded item is already owned" in policy
    assert "unrecorded scrutinized item needs acquisition in play" in policy
    assert "first establish an actual visit and available supply" in policy
    assert "Do not decide availability by Credit Rating, lifestyle, price, or cash" in policy
    assert "there is no purchase/affordability tool" in policy
    assert "`add_carried_item`" in policy and "`remove_carried_item`" in policy
