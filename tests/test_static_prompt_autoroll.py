"""Lock the player-owned check policy after static prompt consolidation."""

from app import keeper
from app.models import GroupState


def test_autoroll_off_has_one_canonical_pending_policy_and_local_reminders() -> None:
    prompt = keeper._build_static_prompt(GroupState(group_id="prompt-autoroll-off"))

    assert "# Investigator checks: player-owned unless autoroll is enabled" in prompt
    assert "Current group mode: autoroll off (default)" in prompt
    assert prompt.count("With autoroll off,") == 1
    assert "With autoroll on, the deterministic engine resolves newly created checks immediately" in prompt
    assert "the Keeper must never change it for them" in prompt
    assert "`pending=true` means ask for the button or `/coc check`" in prompt
    assert "`pending_luck=true` means the roll is complete" in prompt
    assert "always wait for the player's mutually exclusive choice" in prompt
    assert "`pushed` 參數設成 true" in prompt
    assert "CON 檢定依上面的群組模式處理" in prompt
    assert "`skill_check`／`sanity_check` 在 autoroll 關閉（預設）時" not in prompt


def test_autoroll_on_reports_current_mode_without_changing_policy() -> None:
    state = GroupState(group_id="prompt-autoroll-on")
    state.autoroll_checks = True

    prompt = keeper._build_static_prompt(state)

    assert "Current group mode: autoroll on" in prompt
    assert "With autoroll off," in prompt
    assert "With autoroll on," in prompt
