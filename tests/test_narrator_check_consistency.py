from __future__ import annotations

import unittest

from app.agents.tool_gateway import _record_check_status
from app.domain.models import MechanicResult, StateDelta
from app.services.prompt_config import (
    build_mechanic_facts_block,
    enforce_mechanic_check_consistency,
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

    def test_fact_block_labels_pending_check_as_authoritative(self):
        block = build_mechanic_facts_block(mechanic_result({
            "tool_called": True,
            "pending": {"investigator": "Marco", "skill": "STR"},
        }))

        self.assertIn("待處理檢定狀態：已建立", block)
        self.assertIn("禁止說尚未建立", block)

    def test_fact_block_forbids_roll_instruction_when_no_check_was_created(self):
        block = build_mechanic_facts_block(mechanic_result({"tool_called": False, "pending": None}))

        self.assertIn("本回合沒有建立", block)
        self.assertIn("不得指示玩家擲骰", block)

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
