"""What the rerun8 Haunting run showed players: a refusal given up on, a combat opening replaced by a wait line,
a Luck offer read three times, and a refusal after the fight was already over."""
from __future__ import annotations

import unittest

from app import presentation
from app.agents import executor
from app.domain.models import MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.services import prompt_config, turn_delivery


def _event(name: str, ok: bool, *, changed: bool = False) -> dict:
    return {"name": name, "arguments": {}, "result": {"ok": ok},
            "gameplay_before": {"v": 1}, "gameplay_after": {"v": 2 if changed else 1}}


def _result(disposition: str, *, tool_calls: tuple = (), **status) -> MechanicResult:
    return MechanicResult(
        success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
        check_status={"pending": None, "pending_luck": None, **status},
        turn_resolution=TurnResolution(disposition=disposition, validation_code="validated"),
        tool_calls=tool_calls, fallback_reason="unsupported_action" if disposition == "blocked" else None,
    )


class RefusalRetryTests(unittest.TestCase):
    def test_a_refusal_after_look_ups_gets_one_more_try(self):
        events = [_event("search_scenario", True), _event("roll_dice", False)]
        self.assertTrue(executor._gave_up_after_refusal(events))

    def test_no_refusal_or_a_change_made_is_not_this_case(self):
        self.assertFalse(executor._gave_up_after_refusal([_event("search_scenario", True)]))
        self.assertFalse(executor._gave_up_after_refusal([_event("add_carried_item", True), _event("roll_dice", False)]))
        # A combat action paused on a ruling answers ok: False but did write the action.
        self.assertFalse(executor._gave_up_after_refusal([_event("declare_combat_action", False, changed=True)]))


class CombatOpeningTests(unittest.TestCase):
    def test_the_line_that_started_the_fight_keeps_its_scene(self):
        reply = prompt_config.enforce_mechanic_check_consistency(
            "乾屍猛然睜眼，浮空匕首朝你們刺來。", _result("deferred", state_changed=True, waiting_for_name="George Finch"))
        self.assertTrue(reply.startswith("乾屍猛然睜眼"))
        self.assertIn("請先等待George Finch", reply)

    def test_a_plain_wait_is_still_only_the_wait(self):
        reply = prompt_config.enforce_mechanic_check_consistency(
            "Clara 開了一槍。", _result("deferred", waiting_for_name="George Finch"))
        self.assertEqual(reply, "你的這次行動尚未執行，請先等待George Finch完成目前的行動；輪到你時再宣告。")


class LuckPromptTests(unittest.TestCase):
    def test_the_luck_line_is_left_out_when_the_reply_already_gives_it(self):
        ref = turn_delivery.InteractionRef("luck", "p1", "d1", "t1", roll=86)
        told = turn_delivery.DeliveryEnvelope("o", "public", "", "擲出 86。請輸入 /coc luck skip 保留。", interactions=[ref])
        self.assertEqual(told.projected_text(), "")
        untold = turn_delivery.DeliveryEnvelope("o", "public", "", "James 的拳頭停在半空。", interactions=[ref])
        self.assertIn("骰值 86 還在等 Luck 決定", untold.projected_text())
        self.assertNotIn("不要重擲", untold.projected_text())
        six = turn_delivery.InteractionRef("luck", "p1", "d1", "t1", roll=6)
        costs_only = turn_delivery.DeliveryEnvelope("o", "public", "", "或 /coc luck regular（26 點）。", interactions=[six])
        self.assertIn("骰值 6 還在等", costs_only.projected_text())

    def test_a_roll_held_for_luck_is_not_called_settled(self):
        held = turn_delivery.observe_tool(
            "skill_check", {"ok": True, "resolved": True, "pending_luck": True, "investigator": "George", "roll": 93,
                            "tier": "fail"}, 1)
        self.assertEqual(held.public_text, "")
        settled = turn_delivery.observe_tool(
            "skill_check", {"ok": True, "resolved": True, "investigator": "George", "roll": 93, "tier": "fail"}, 1)
        self.assertIn("已結算", settled.public_text)

    def test_a_luck_decision_id_does_not_leak(self):
        text = presentation.player_text("這筆決定仍待你作答：`check-0123456789abcdef0123456789abcdef:luck`。")
        self.assertEqual(text, "這筆決定仍待你作答。")


class FightOverTests(unittest.TestCase):
    def test_attacking_after_the_keeper_closed_the_fight_says_so(self):
        state = GroupState(group_id="g")
        state.characters["p1"] = Character(name="Clara", owner_id="p1", hp=6)
        closed = _result("blocked", tool_calls=(("preview_combat_settlement", True), ("confirm_combat_settlement", True)),
                         state_changed=True)
        reply = prompt_config.enforce_mechanic_check_consistency("x", closed, state=state)
        self.assertEqual(reply, "戰鬥已經結束，這一擊不必再出手了。接下來想做什麼？")
        failed_too = _result("blocked", tool_calls=(("confirm_combat_settlement", True), ("transfer_item", False)),
                             state_changed=True)
        self.assertNotIn("接下來想做什麼", prompt_config.enforce_mechanic_check_consistency("x", failed_too, state=state))
        state.characters["p1"].hp = 0  # the party fell: not "what next"
        self.assertNotIn("接下來想做什麼", prompt_config.enforce_mechanic_check_consistency("x", closed, state=state))


if __name__ == "__main__":
    unittest.main()
