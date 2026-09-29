"""KP generic dice prompt retains the two context contracts."""

from app import keeper


def test_kp_roll_dice_policy_is_concise_and_preserves_canon_split() -> None:
    prompt = keeper._KP_ASSISTANT_PROMPT
    policy = prompt.split("Generic deterministic dice:", 1)[1].split(
        "正式遊戲事件已確定需要擲普通武器傷害", 1
    )[0]

    assert "only when no specific rules tool applies" in policy
    assert "human-readable `purpose`" in policy
    assert "exactly `game_resolution` or `ooc_randomizer`" in policy
    assert policy.count("`roll_dice(expression=") == 2
    assert "makes the triggering KP instruction game canon" in policy
    assert "stays in OOC history" in policy
    assert "碎玻璃割傷 Marco 的傷害" in policy
    assert "幕後決定下一幕使用哪個 NPC" in policy
