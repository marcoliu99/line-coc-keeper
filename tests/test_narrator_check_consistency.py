from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.agents.tool_gateway import _record_check_status
from app.domain.models import MechanicResult, StateDelta
from app.services.prompt_config import (
    build_mechanic_facts_block,
    build_resolved_check_outcome_block,
    enforce_mechanic_check_consistency,
    enforce_resolved_check_consistency,
)


def mechanic_result(check_status: dict) -> MechanicResult:
    return MechanicResult(
        success=True,
        action_type="tool_calls",
        narrative_facts=["skill_check 成功：pending=True"],
        state_delta=StateDelta(),
        check_status=check_status,
    )


class NarratorCheckConsistencyTests(unittest.TestCase):
    def test_tool_result_records_pending_check_as_structured_status(self):
        status = {"tool_called": False, "pending": None}
        _record_check_status(status, "skill_check", {
            "ok": True,
            "pending": True,
            "investigator": "Marco",
            "skill": "STR",
            "skill_value": 80,
            "difficulty": "hard",
        })

        self.assertEqual(status["tool_called"], True)
        self.assertEqual(status["pending"]["skill"], "STR")
        self.assertEqual(status["pending"]["skill_value"], 80)

    def test_tool_failure_does_not_mark_a_check_pending(self):
        status = {"tool_called": False, "pending": None}
        _record_check_status(status, "skill_check", {"ok": False, "error": "already pending"})

        self.assertTrue(status["tool_called"])
        self.assertIsNone(status["pending"])

    def test_tool_result_records_roll_waiting_for_luck_as_not_final(self):
        status = {"tool_called": False, "pending": None, "pending_luck": None, "resolved": None}
        _record_check_status(status, "skill_check", {
            "ok": True,
            "resolved": True,
            "pending_luck": True,
            "investigator": "Mick",
            "skill": "STR",
            "skill_value": 40,
            "roll": 69,
            "tier": "failure",
            "difficulty": "regular",
            "luck_options": [{"tier": "regular", "cost": 29}],
        })

        self.assertIsNone(status["pending"])
        self.assertEqual(status["pending_luck"]["roll"], 69)
        self.assertIsNone(status["resolved"])

    def test_fact_block_labels_pending_check_as_authoritative(self):
        block = build_mechanic_facts_block(mechanic_result({
            "tool_called": True,
            "pending": {"investigator": "Marco", "skill": "STR"},
        }))

        self.assertIn("待處理檢定狀態：已建立", block)
        self.assertIn("禁止說尚未建立", block)

    def test_fact_block_forbids_roll_instruction_when_no_check_was_created(self):
        block = build_mechanic_facts_block(mechanic_result({"tool_called": False, "pending": None}))

        self.assertIn("沒有待處理／新建立檢定", block)
        self.assertIn("不得指示玩家擲骰", block)

    def test_fact_block_distinguishes_pending_luck_from_final_result(self):
        block = build_mechanic_facts_block(mechanic_result({
            "tool_called": True,
            "pending": None,
            "pending_luck": {
                "investigator": "Mick", "skill_name": "STR", "roll": 69,
                "original_tier": "failure", "options": [{"tier": "regular", "cost": 29}],
            },
            "resolved": None,
        }))

        self.assertIn("骰已擲出，最終結果尚未定案", block)
        self.assertIn("/coc luck regular", block)
        self.assertNotIn("沒有待處理／新建立檢定", block)

    def test_fact_block_includes_structured_resolved_result(self):
        block = build_mechanic_facts_block(mechanic_result({
            "tool_called": True,
            "pending": None,
            "pending_luck": None,
            "resolved": {
                "investigator": "Mick", "skill": "STR", "skill_value": 40,
                "roll": 69, "difficulty": "regular", "tier": "failure", "success": False,
            },
        }))

        self.assertIn("已結算檢定", block)
        self.assertIn("擲出 69", block)
        self.assertIn("不得重擲", block)
        self.assertNotIn("沒有待處理／新建立檢定", block)

    def test_consistent_pending_check_narration_is_preserved(self):
        text = "Marco 的 STR 檢定已建立，請輸入 /coc check 擲骰。"
        result = mechanic_result({"tool_called": True, "pending": {"investigator": "Marco", "skill": "STR"}})

        self.assertEqual(enforce_mechanic_check_consistency(text, result), text)

    def test_denial_of_registered_pending_check_is_replaced(self):
        result = mechanic_result({"tool_called": True, "pending": {"investigator": "Marco", "skill": "STR"}})

        corrected = enforce_mechanic_check_consistency("STR 檢定尚未建立，請守密人重新建立。", result)

        self.assertIn("Marco", corrected)
        self.assertIn("已建立", corrected)
        self.assertIn("/coc check", corrected)

    def test_pending_check_without_next_step_gets_roll_instruction(self):
        result = mechanic_result({"pending": {"investigator": "Mick", "skill": "STR"}})

        corrected = enforce_mechanic_check_consistency("木板仍擋在面前，檢定結果未知。", result)

        self.assertIn("已建立", corrected)
        self.assertIn("/coc check", corrected)

    def test_pending_luck_gets_luck_choice_not_second_check(self):
        result = mechanic_result({
            "pending": None,
            "pending_luck": {
                "investigator": "Mick", "skill_name": "STR", "roll": 69,
                "options": [{"tier": "regular", "cost": 29}],
            },
            "resolved": None,
        })

        corrected = enforce_mechanic_check_consistency("Mick 揮下大槌，木板發出悶響。", result)

        self.assertIn("Luck 按鈕", corrected)
        self.assertIn("/coc luck skip", corrected)
        self.assertIn("/coc luck regular", corrected)
        self.assertNotIn("/coc check", corrected)

    def test_resolved_check_does_not_allow_unsettled_combat_reply(self):
        result = {
            "investigator": "Mick", "skill": "STR", "skill_value": 40,
            "roll": 69, "difficulty": "regular", "outcome": "failure 失敗",
        }

        block = build_resolved_check_outcome_block(result)
        corrected = enforce_resolved_check_consistency("你的行動尚未結算，等輪到你再敲。", result)

        self.assertIn("不得重擲", block)
        self.assertIn("已結算", corrected)
        self.assertIn("擲出 69", corrected)
        self.assertIn("結果為「failure 失敗」", corrected)


class ResolvedCheckKeeperFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolved_check_followup_exposes_combat_tools_without_new_roll(self):
        from app import keeper
        from app.models import GroupState

        class FakeProvider:
            OPENAI_MODEL = "fake"

            def __init__(self):
                self.args = None

            async def run_conversation(self, *args, **kwargs):
                self.args = (args, kwargs)
                return "你的行動尚未結算，等輪到你再敲。"

        provider = FakeProvider()
        context = {
            "investigator": "Mick", "skill": "STR", "skill_value": 40,
            "roll": 69, "difficulty": "regular", "outcome": "failure 失敗",
            "action_context": "用大槌敲木板牆",
        }
        state = GroupState(group_id="g")

        with patch.object(keeper, "LLM_PROVIDER", "openai"), \
                patch.object(keeper, "_PROVIDERS", {"openai": provider}), \
                patch.object(keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
                patch.object(keeper, "_build_static_prompt", return_value="static"), \
                patch.object(keeper, "_build_dynamic_prompt", return_value="dynamic"), \
                patch.object(keeper, "_commit_turn_result", return_value=True), \
                patch.object(keeper.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            reply, _, _ = await keeper.run_turn(
                state, "u1", "Mick", "STR 檢定結果", resolved_check_context=context
            )

        args, _kwargs = provider.args
        offered_tools = args[2]
        offered_names = {tool["name"] for tool in offered_tools}
        self.assertTrue(offered_names <= keeper.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES)
        self.assertTrue({"apply_combat_damage", "apply_final_combat_damage", "advance_combat_turn"} <= offered_names)
        self.assertNotIn("skill_check", offered_names)
        self.assertIn("擲出 69", args[1])
        self.assertIn("不得重擲", args[1])
        self.assertIn("已結算", reply)
        self.assertIn("結果為「failure 失敗」", reply)

    async def test_resolved_check_can_execute_damage_followup(self):
        from app import keeper
        from app.models import GroupState

        class FakeProvider:
            OPENAI_MODEL = "fake"

            async def run_conversation(self, _static, _dynamic, tools, _history, _message,
                                       execute_tool, _iterations, **_kwargs):
                self.assert_names = {tool["name"] for tool in tools}
                result = await execute_tool("apply_combat_damage", {"target": "Enemy", "raw_damage": 3})
                self.result = result
                return "敵人受到傷害。"

        provider = FakeProvider()
        state = GroupState(group_id="g")
        calls = []

        def execute(_state, name, payload, *_args):
            calls.append((name, payload))
            return {"ok": True, "damage": 3}

        with patch.object(keeper, "LLM_PROVIDER", "openai"), \
                patch.object(keeper, "_PROVIDERS", {"openai": provider}), \
                patch.object(keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
                patch.object(keeper, "_build_static_prompt", return_value="static"), \
                patch.object(keeper, "_build_dynamic_prompt", return_value="dynamic"), \
                patch.object(keeper, "_execute_tool", side_effect=execute), \
                patch.object(keeper, "_commit_turn_result", return_value=True), \
                patch.object(keeper.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            await keeper.run_turn(
                state, "u1", "Mick", "防守檢定結果",
                resolved_check_context={"investigator": "Mick", "skill": "閃避", "roll": 70,
                                        "difficulty": "regular", "outcome": "failure 失敗"},
            )
        self.assertIn("apply_combat_damage", provider.assert_names)
        self.assertEqual(calls, [("apply_combat_damage", {"target": "Enemy", "raw_damage": 3})])
        self.assertEqual(provider.result["damage"], 3)

    def test_roll_instruction_without_pending_check_is_replaced(self):
        result = mechanic_result({"tool_called": False, "pending": None})

        corrected = enforce_mechanic_check_consistency(
            "目前尚未建立檢定，請以 /coc check 擲骰。", result
        )

        self.assertIn("沒有建立待處理檢定", corrected)
        self.assertNotIn("請以 /coc check", corrected)

    def test_non_check_narration_without_pending_check_is_preserved(self):
        text = "斧刃卡在門縫，木框發出一聲乾澀的裂響。"
        result = mechanic_result({"tool_called": False, "pending": None})

        self.assertEqual(enforce_mechanic_check_consistency(text, result), text)

    def test_explicit_warning_not_to_roll_is_preserved(self):
        text = "這回合沒有待處理檢定，不要使用 /coc check。"
        result = mechanic_result({"tool_called": False, "pending": None})

        self.assertEqual(enforce_mechanic_check_consistency(text, result), text)

    def test_unrelated_negation_does_not_hide_roll_instruction(self):
        text = "你不需要鑰匙；請擲骰決定是否撬開門。"
        result = mechanic_result({"tool_called": False, "pending": None})

        corrected = enforce_mechanic_check_consistency(text, result)

        self.assertIn("沒有建立待處理檢定", corrected)
        self.assertNotEqual(corrected, text)

if __name__ == "__main__":
    unittest.main()
