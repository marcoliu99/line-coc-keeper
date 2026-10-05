"""The shortened equipment policy keeps acquisition and state boundaries."""

from app import prompt_builder
from app.models import GroupState


def test_equipment_policy_has_three_rules_without_purchase_instructions() -> None:
    prompt = prompt_builder.build_static_prompt(GroupState(group_id="equipment-prompt"))
    policy = prompt.split("# Equipment Consistency\n", 1)[1].split("\n# ", 1)[0]

    assert sum(line.startswith("- ") for line in policy.splitlines()) == 3
    assert "Ordinary personal items" in policy
    assert "period/technology" in policy
    assert "occupation/background or established events" in policy
    assert "legal/regional availability" in policy
    assert "A recorded item is already owned" in policy
    assert "unrecorded scrutinized item needs acquisition in play" in policy
    for purchase_term in ("purchase", "shop", "supply", "price", "payment", "Credit Rating", "cash", "Luck"):
        assert purchase_term.lower() not in policy.lower()
    assert "`add_carried_item`" in policy and "`remove_carried_item`" in policy
