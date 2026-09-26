"""Prompt contracts for the scenario canon boundary in every Keeper path.

These check the instructions delivered to the model. They cannot prove that a
live model will obey them; the scenario cases still need end-to-end evaluation.
"""

import unittest
from unittest.mock import patch

from app import keeper
from app.models import GroupState
from app.services import prompt_config


class NarrativeBoundaryPromptTests(unittest.TestCase):
    def assertPolicy(self, prompt: str, phrase: str) -> None:
        self.assertTrue(phrase in prompt, f"missing policy phrase: {phrase}")

    def _prompts(self, *, rag_enabled: bool = False) -> dict[str, str]:
        state = GroupState(group_id="canon-boundary")
        state.scenario_text = "只有一樓書房；書房裡沒有通往地下的樓梯。"
        state.campaign_summary = "上回合 Keeper 自行說了地下室有骷髏。"
        with patch.object(keeper, "SCENARIO_RAG_ENABLED", rag_enabled):
            legacy = keeper._build_static_prompt(state)
        return {
            "legacy": legacy,
            "executor": prompt_config.build_executor_static_prompt(legacy),
            "narrator": prompt_config.build_narrator_static_prompt(legacy),
        }

    def test_every_keeper_path_declares_scenario_as_authority(self):
        for path, prompt in self._prompts().items():
            with self.subTest(path=path):
                self.assertPolicy(prompt, "劇本是世界事實的權威來源")
                self.assertPolicy(prompt, "不是新劇本內容的共同作者")
                self.assertEqual(prompt.count("# 劇本正典邊界｜最高優先"), 1)

    def test_player_hypotheses_and_failed_rolls_cannot_create_world_elements(self):
        for path, prompt in self._prompts().items():
            with self.subTest(path=path):
                self.assertPolicy(prompt, "不證明地下室或骷髏存在")
                self.assertPolicy(prompt, "失敗骰不會生出敵人")
                self.assertPolicy(prompt, "只有劇本條件或已成立的正式事件確實使攻擊")
                self.assertNotIn("看到戰鬥發生就立刻呼叫", prompt)

    def test_rag_miss_is_unknown_and_followup_search_is_conditional(self):
        for path, prompt in self._prompts(rag_enabled=True).items():
            with self.subTest(path=path):
                self.assertPolicy(prompt, "目前無法確認")
                self.assertPolicy(prompt, "必要時")
                self.assertPolicy(prompt, "search_scenario")

        executor_context = prompt_config.build_executor_dynamic_prompt_with_context(
            "目前場景", "書房只有一樓", ""
        )
        self.assertIn("不要為了確認或改寫查詢而再次呼叫 search_scenario", executor_context)
        self.assertIn("只有在缺少一項會影響本次判定", executor_context)

    def test_everyday_items_remain_allowed_but_cannot_become_plot_resources(self):
        for path, prompt in self._prompts().items():
            with self.subTest(path=path):
                self.assertPolicy(prompt, "日常隨身小物")
                self.assertPolicy(prompt, "不能變成關鍵證據或資源")

    def test_prior_ai_text_alone_does_not_establish_canon(self):
        for path, prompt in self._prompts().items():
            with self.subTest(path=path):
                self.assertPolicy(prompt, "上回合")
                self.assertPolicy(prompt, "不能僅因")
                self.assertPolicy(prompt, "正典")

    def test_approved_correction_overrides_old_summary_and_pending_claim_stays_unverified(self):
        state = GroupState(group_id="canon-boundary")
        state.campaign_summary = "Keeper 曾說地下室有骷髏。"
        state.narrative_corrections = [
            {"id": "approved", "status": "approved", "target_message_id": "12345",
             "issue": "地下室有骷髏", "resolution": "劇本沒有地下室"},
            {"id": "pending", "status": "pending", "target_message_id": "67890",
             "issue": "暗門後有鑰匙"},
        ]

        prompt = keeper._build_static_prompt(state)

        self.assertIn("劇本沒有地下室", prompt)
        self.assertIn("舊敘事、摘要或 Memory RAG 若衝突，以此更正為準", prompt)
        self.assertIn("待 KP 核對", prompt)
        self.assertIn("不得把爭議內容當成已確立事實", prompt)
