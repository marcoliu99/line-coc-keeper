"""Four combat replies from the 2026-10-11/12 soak runs: an unconscious investigator who kept fighting, an enemy
whose death went unsaid, a counter that hit and was never shown, and Luck offered where it could change nothing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import combat_resources, dice
from app.services import managed_checks
from tests import combat_calls as calls
from tests.test_combat_flow import battle, check


def test_an_autorolled_failed_con_check_puts_the_investigator_down():
    state, pc, _ = battle(autoroll=True)
    with patch('app.dice.skill_check', return_value=check('fail')):
        assert calls.managed_single_hit(state, pc, 6, event_id='incident', reason='Authorized')['ok']
    assert combat_resources.effective_character(state, pc).injury['unconscious']
    assert next(c for c in state.combat.order if c.is_pc).defeated  # cannot take another turn


def test_an_enemy_that_falls_is_said_to_fall():
    damage = {"target": "深潛者混血", "final_damage": 20, "defeated": True}
    assert managed_checks._wound_tail(SimpleNamespace(), {}, damage) == "，倒下了"
    assert managed_checks._wound_tail(SimpleNamespace(), {}, {**damage, "defeated": False}) == ""


def _opt(tier):
    return SimpleNamespace(tier=tier, cost=5)


def test_defence_luck_offers_only_what_can_change_the_blow():
    every = [_opt("regular"), _opt("hard"), _opt("extreme")]
    assert dice.useful_defence_luck("dodge", "fail", "fail", every) == []  # the attack misses anyway
    assert dice.useful_defence_luck("dodge", "hard", "regular", every)[0].tier == "hard"  # Hard is needed
    assert [o.tier for o in dice.useful_defence_luck("dodge", "hard", "fail", every)] == ["hard", "extreme"]
    assert dice.useful_defence_luck("counter", "critical", "fail", every) == []  # nothing beats a Critical
    assert dice.useful_defence_luck("dodge", "regular", "regular", every) == []  # already keeps it off
    assert dice.useful_defence_luck(None, "hard", "fail", every) == every  # not a defence: unchanged


def test_a_choice_the_engine_settled_with_its_own_roll_shows_the_blow():
    from tests.test_combat_engine import _battle, _enemy_turn, _load, _player, _save
    _battle()
    state = _load()
    state.autoroll_checks = True
    _save(state)
    _enemy_turn([90])  # the claw fails
    outcome, _ = _player("/coc check 反擊", [10], damage=3)  # the engine rolls the counter: Hard, it lands
    assert "已依系統紀錄處理" not in (outcome.reply_text or "")
    assert "⚔️" in outcome.roll_feedback_text and "反擊得手" in outcome.roll_feedback_text, outcome
