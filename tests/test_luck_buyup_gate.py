"""Tests for docs/specs/enhancement-luck-buyup-always-offered.md: the
proactive Luck buy-up decision (app/keeper.py's skill_check tool handler,
and app/legacy_commands.py's _resolve_check_deterministically) must now be
offered whenever there's at least one affordable tier-improving option,
not just when the cheapest one costs <= 7 Luck.

Patches luck.buyable_options directly (rather than reverse-engineering
real skill/roll numbers that produce a specific cost) to isolate the
gating-logic change from app/luck.py's own cost formula, which is
untouched by this change and already implicitly covered elsewhere.
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault(
    "app.pdf_loader",
    types.SimpleNamespace(
        extract_text=lambda pdf_bytes: ("", [], False, {}, {}),
        guess_title=lambda text, file_name="": file_name or "Untitled",
        extract_preview=lambda pdf_bytes: "",
    ),
)

from app import keeper, legacy_commands
from app.luck import LuckOption
from app.models import Character, GroupState


def clone_state(state: GroupState) -> GroupState:
    return GroupState.from_dict(state.to_dict())


class StateStorePatch:
    def __init__(self, *modules) -> None:
        self.modules = modules
        self.store: dict[str, GroupState] = {}
        self.originals = []

    def __enter__(self):
        def load_state(group_id: str) -> GroupState:
            return clone_state(self.store.get(group_id, GroupState(group_id=group_id)))

        def save_state(state: GroupState, *, reason: str = "command") -> None:
            self.store[state.group_id] = clone_state(state)

        for module in self.modules:
            self.originals.append((module, module.load_state, module.save_state))
            module.load_state = load_state
            module.save_state = save_state
        return self

    def __exit__(self, exc_type, exc, tb):
        for module, load_state, save_state in reversed(self.originals):
            module.load_state = load_state
            module.save_state = save_state

    def put(self, state: GroupState) -> None:
        self.store[state.group_id] = clone_state(state)


def _state_with_investigator() -> GroupState:
    state = GroupState(group_id="g")
    state.characters["u1"] = Character(name="小明", owner_id="u1", skills={"閃避": 45}, luck=50)
    return state


_EXPENSIVE_OPTION = [LuckOption(tier="hard", threshold=10, cost=12)]


class KeeperSkillCheckLuckGateTests(unittest.TestCase):
    """app/keeper.py's skill_check tool handler (LLM tool-calling path)."""

    def test_offers_luck_buyup_even_when_cheapest_option_costs_more_than_7(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        fake_roll = MagicMock(roll=40, tier="fail", required_tier="regular", success=False)
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=fake_roll), \
                 patch("app.keeper.luck.buyable_options", return_value=_EXPENSIVE_OPTION):
                result = keeper._execute_tool(
                    state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("pending_luck"))
        self.assertEqual(result["luck_options"], [{"tier": "hard", "cost": 12}])
        self.assertIn("u1", saved_state.pending_luck_decisions)

    def test_no_offer_when_no_buyable_options(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        fake_roll = MagicMock(roll=5, tier="extreme", required_tier="regular", success=True)
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=fake_roll), \
                 patch("app.keeper.luck.buyable_options", return_value=[]):
                result = keeper._execute_tool(
                    state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertNotIn("pending_luck", result)
        self.assertNotIn("u1", saved_state.pending_luck_decisions)

    def test_pushed_roll_never_offers_luck_buyup_regardless_of_cost(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        fake_roll = MagicMock(roll=40, tier="fail", required_tier="regular", success=False)
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=fake_roll), \
                 patch("app.keeper.luck.buyable_options", return_value=_EXPENSIVE_OPTION) as buyable_mock:
                result = keeper._execute_tool(
                    state, "skill_check", {"investigator": "小明", "skill": "閃避", "pushed": True},
                    [], [], speaker_role="player",
                )
            saved_state = store.store["g"]
        buyable_mock.assert_not_called()
        self.assertNotIn("pending_luck", result)
        self.assertNotIn("u1", saved_state.pending_luck_decisions)


class LegacyCheckResolutionLuckGateTests(unittest.TestCase):
    """app/legacy_commands.py's _resolve_check_deterministically (the
    /coc-check-resolves-a-pending-roll path)."""

    def test_offers_luck_buyup_even_when_cheapest_option_costs_more_than_7(self):
        state = _state_with_investigator()
        state.active = True
        state.pending_checks["u1"] = {"type": "skill", "skill": "閃避", "skill_value": 45, "bonus_dice": 0, "penalty_dice": 0}
        fake_roll = MagicMock(roll=40, tier="fail", required_tier="regular", success=False)
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            with patch("app.legacy_commands.dice.skill_check", return_value=fake_roll), \
                 patch("app.legacy_commands.luck.buyable_options", return_value=_EXPENSIVE_OPTION):
                resolution = legacy_commands._resolve_check_deterministically("g", "u1", "/coc check 閃避")
            saved_state = store.store["g"]
        self.assertFalse(resolution.should_finalize)
        self.assertIn("12", resolution.reply_text)
        self.assertIn("u1", saved_state.pending_luck_decisions)

    def test_no_offer_when_no_buyable_options(self):
        state = _state_with_investigator()
        state.active = True
        state.pending_checks["u1"] = {"type": "skill", "skill": "閃避", "skill_value": 45, "bonus_dice": 0, "penalty_dice": 0}
        fake_roll = MagicMock(roll=5, tier="extreme", required_tier="regular", success=True)
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            with patch("app.legacy_commands.dice.skill_check", return_value=fake_roll), \
                 patch("app.legacy_commands.luck.buyable_options", return_value=[]):
                resolution = legacy_commands._resolve_check_deterministically("g", "u1", "/coc check 閃避")
            saved_state = store.store["g"]
        self.assertTrue(resolution.should_finalize)
        self.assertNotIn("u1", saved_state.pending_luck_decisions)

    def test_pushed_roll_never_offers_luck_buyup_regardless_of_cost(self):
        state = _state_with_investigator()
        state.active = True
        state.pending_checks["u1"] = {
            "type": "skill", "skill": "閃避", "skill_value": 45, "bonus_dice": 0, "penalty_dice": 0, "pushed": True,
        }
        fake_roll = MagicMock(roll=40, tier="fail", required_tier="regular", success=False)
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            with patch("app.legacy_commands.dice.skill_check", return_value=fake_roll), \
                 patch("app.legacy_commands.luck.buyable_options", return_value=_EXPENSIVE_OPTION) as buyable_mock:
                resolution = legacy_commands._resolve_check_deterministically("g", "u1", "/coc check 閃避")
            saved_state = store.store["g"]
        buyable_mock.assert_not_called()
        self.assertTrue(resolution.should_finalize)
        self.assertNotIn("u1", saved_state.pending_luck_decisions)


if __name__ == "__main__":
    unittest.main()
