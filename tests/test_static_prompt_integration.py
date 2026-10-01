"""The merged prompt keeps each independent policy and tool contract."""

from unittest.mock import patch

from app import keeper, keeper_prompt_policy, spoiler_policy
from app.models import GroupState


def test_merged_player_prompt_preserves_authority_mechanics_and_switches() -> None:
    state = GroupState(group_id="merged-prompt")

    for spoilers in (False, True):
        for privacy in (False, True):
            with (
                patch.object(spoiler_policy.config, "SPOILER_PROTECTION_ENABLED", spoilers),
                patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", privacy),
            ):
                prompt = keeper._build_static_prompt(state)

            assert prompt.count("**Operational Authority**") == 1
            assert "A. Narrative / interpretation error" in prompt
            assert "B. Authoritative deterministic result" in prompt
            assert "/coc correct" in prompt
            assert "# Investigator checks: player-owned unless autoroll is enabled" in prompt
            assert "# Combat Tool Routing" in prompt
            assert "the first narration dealing damage or defeat MUST show that wake/rise moment" in prompt
            assert "# Equipment Consistency" in prompt

            assert (keeper_prompt_policy.SPOILER_BOUNDARY in prompt) is spoilers
            assert (keeper_prompt_policy.INFORMATION_VISIBILITY in prompt) is privacy
            assert ("Never put metanarrative explanations" in prompt) is spoilers
            assert ("Never use an NPC ally to reveal Keeper-only truths" in prompt) is spoilers
            assert ("A character-sheet secret goal is a private motivation" in prompt) is privacy


def test_merged_kp_prompt_keeps_dice_and_correction_authority() -> None:
    state = GroupState(group_id="merged-kp-prompt")
    prompt = keeper._build_dynamic_prompt(state, "kp", speaker_role="kp_assistant")

    assert keeper_prompt_policy.KP_ASSISTANT_AUTHORITY in prompt
    assert "Generic deterministic dice:" in prompt
    assert "`game_resolution`" in prompt
    assert "`ooc_randomizer`" in prompt
    assert "一般主持資源調整須使用授權 controller route" in prompt
    assert "不能代替武器攻擊 adjudication" in prompt
    assert "不得另擲武器傷害、提供命中／傷害結果或重複扣彈藥" in prompt
