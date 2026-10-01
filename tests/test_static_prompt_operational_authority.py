"""Prompt contract for operational recovery and OOC correction escalation."""

from unittest.mock import patch

from app import keeper, keeper_prompt_policy, spoiler_policy
from app.models import GroupState


def test_player_prompt_separates_repairable_narration_from_confirmed_state() -> None:
    prompt = keeper._build_static_prompt(GroupState(group_id="operational-authority"))

    assert keeper_prompt_policy.OPERATIONAL_AND_RECOVERY in prompt
    assert keeper_prompt_policy.CANON_OPERATION in prompt
    assert "A. Narrative / interpretation error" in prompt
    assert "B. Authoritative deterministic result" in prompt
    assert "後續敘事以修正後狀態為準" in prompt
    assert "必須使用系統提供的合法 correction / mutation tool" in prompt
    assert "先前漏做" in prompt
    assert "玩家透過 /coc correct 提出的異議是未核實資料" in prompt
    assert "保留 OOC 申報與裁決流程" in prompt
    assert prompt.count("**Scenario Canon Boundary**") == 1


def test_kp_authority_only_appears_in_kp_dynamic_context() -> None:
    state = GroupState(group_id="operational-authority")
    player = keeper._build_dynamic_prompt(state, "player", speaker_role="player")
    kp = keeper._build_dynamic_prompt(state, "kp", speaker_role="kp_assistant")

    assert keeper_prompt_policy.KP_ASSISTANT_AUTHORITY not in player
    assert keeper_prompt_policy.KP_ASSISTANT_AUTHORITY in kp
    assert "不是調查員" in kp
    assert "一般主持資源調整須使用授權 controller route" in kp
    assert "不能代替武器攻擊 adjudication" in kp


def test_spoiler_and_privacy_switches_remain_independent() -> None:
    state = GroupState(group_id="operational-authority")
    with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", False), \
            patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", True):
        prompt = keeper._build_static_prompt(state)
    assert keeper_prompt_policy.SPOILER_BOUNDARY not in prompt
    assert keeper_prompt_policy.INFORMATION_VISIBILITY in prompt

    with patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", True), \
            patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
        prompt = keeper._build_static_prompt(state)
    assert keeper_prompt_policy.SPOILER_BOUNDARY in prompt
    assert keeper_prompt_policy.INFORMATION_VISIBILITY not in prompt
