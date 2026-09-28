"""docs/specs/bug/major_wound_con_check_gate_design_spec.md.

With autoroll off, a major-wound hit on an investigator whose player already
has a pending check or an open Luck decision is refused before anything
changes, on every damage path, and says so, instead of dropping the CON
check it owes.
"""
import unittest
from unittest.mock import patch

from app import combat, keeper
from app.models import Character, GroupState

SEARCH_CHECK = {"type": "skill", "skill": "偵查", "skill_value": 50, "check_id": "check-search"}
LUCK_DECISION = {"options": [], "decision_id": "decision-luck"}


def _state(*names: str) -> GroupState:
    state = GroupState(group_id="g")
    for index, name in enumerate(names, start=1):
        char = Character(name=name, owner_id=f"u{index}", character_id=f"char-{index}", dex=50, hp=12, hp_max=12)
        state.characters[char.owner_id] = char
        state.characters_by_id[char.character_id] = char
        state.active_character_id_by_user[char.owner_id] = char.character_id
    return state


def _combat_state(*names: str) -> GroupState:
    state = _state(*names)
    combat.start_combat(state)
    return state


def _pc_combatant(state: GroupState, name: str):
    return next(c for c in state.combat.order if c.name == name)


def _enemy_attack_plan(state: GroupState) -> str:
    combat.add_npc(state, "Attacker", 60, 14, attacks=[
        {"id": "bite", "label": "Bite", "skill_value": 50, "damage": "1D8"},
    ])
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Attacker")
    return combat.plan_enemy_turn(state)["plan_id"]


# (entry point, how to deal a 6-point hit to Mark)
DAMAGE_PATHS = [
    ("apply_combat_damage", lambda state: combat.apply_combat_damage(state, "Mark", 6)),
    ("apply_final_combat_damage", lambda state: combat.apply_final_combat_damage(state, "Mark", 6)),
    ("damage_combatant", lambda state: combat.damage_combatant(state, "Mark", -6)),
    ("resolve_enemy_action", lambda state: combat.resolve_enemy_action(
        state, _enemy_attack_plan(state), outcome={"hit": True, "damage": 6},
    )),
]


class CombatDamagePathTests(unittest.TestCase):
    def _assert_refused(self, blocker_key: str, blocker_value: dict, expected: str) -> None:
        for entry_point, deal in DAMAGE_PATHS:
            with self.subTest(entry_point=entry_point):
                state = _combat_state("Mark")
                getattr(state, blocker_key)["u1"] = dict(blocker_value)
                with patch.object(combat.observability, "event") as event, \
                        patch.object(combat.dice, "skill_check") as roll:
                    result = deal(state)

                self.assertFalse(result["ok"])
                self.assertEqual(result["blocked_by"], expected)
                self.assertIn("本次傷害未套用", result["error"])
                self.assertEqual(state.characters["u1"].hp, 12)
                self.assertEqual(_pc_combatant(state, "Mark").hp, 12)
                self.assertEqual(getattr(state, blocker_key)["u1"], blocker_value)
                self.assertNotIn("u1", state.pending_checks if blocker_key != "pending_checks" else {})
                roll.assert_not_called()
                blocked = [c for c in event.call_args_list if c.args[0] == "combat.major_wound.blocked"]
                self.assertEqual(len(blocked), 1)
                self.assertEqual(blocked[0].kwargs["blocked_by"], expected)
                self.assertEqual(blocked[0].kwargs["entry_point"], entry_point)

    def test_every_path_refuses_a_major_wound_while_a_check_is_pending(self):
        self._assert_refused("pending_checks", SEARCH_CHECK, "pending_check")

    def test_every_path_refuses_a_major_wound_while_a_luck_decision_is_open(self):
        self._assert_refused("pending_luck_decisions", LUCK_DECISION, "pending_luck_decision")

    def test_a_minor_hit_ignores_the_pending_check(self):
        state = _combat_state("Mark")
        state.pending_checks["u1"] = dict(SEARCH_CHECK)
        result = combat.apply_combat_damage(state, "Mark", 5)
        self.assertTrue(result["ok"])
        self.assertFalse(result["major_wound_triggered"])
        self.assertEqual(state.characters["u1"].hp, 7)

    def test_a_hit_to_zero_ignores_the_pending_check(self):
        state = _combat_state("Mark")
        state.pending_checks["u1"] = dict(SEARCH_CHECK)
        result = combat.apply_combat_damage(state, "Mark", 12)
        self.assertTrue(result["ok"])
        self.assertEqual(state.characters["u1"].hp, 0)

    def test_autoroll_resolves_con_immediately_despite_the_pending_check(self):
        state = _combat_state("Mark")
        state.autoroll_checks = True
        state.pending_luck_decisions["u1"] = dict(LUCK_DECISION)
        with patch.object(combat.dice, "skill_check") as roll:
            roll.return_value.success = True
            result = combat.apply_combat_damage(state, "Mark", 6)
        self.assertTrue(result["ok"])
        self.assertTrue(result["major_wound_triggered"])
        roll.assert_called_once()

    def test_without_a_blocker_the_con_check_is_registered_as_before(self):
        state = _combat_state("Mark")
        result = combat.apply_combat_damage(state, "Mark", 6)
        self.assertTrue(result["ok"])
        self.assertTrue(result["major_wound_check"]["pending"])
        self.assertEqual(state.pending_checks["u1"]["skill"], "CON")


class MultiTargetEffectTests(unittest.TestCase):
    def test_one_blocked_target_holds_back_the_whole_effect_until_it_clears(self):
        state = _combat_state("First", "Second")
        state.pending_checks["u2"] = dict(SEARCH_CHECK)
        combat.add_combat_effect(state, "all", "Collapsing Ceiling", timing="round_end", damage="6")

        results = combat.process_timing(state, "round_end")

        self.assertEqual([r.get("blocked_by") for r in results], ["pending_check"])
        self.assertEqual(state.characters["u1"].hp, 12)
        self.assertEqual(state.characters["u2"].hp, 12)
        self.assertFalse([key for key in state.combat.processed_timings if "round_end" in key])

        del state.pending_checks["u2"]
        results = combat.process_timing(state, "round_end")

        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(state.characters["u1"].hp, 6)
        self.assertEqual(state.characters["u2"].hp, 6)
        self.assertEqual(combat.process_timing(state, "round_end"), [])
        self.assertEqual(state.characters["u1"].hp, 6)


class KeeperToolTests(unittest.TestCase):
    """Keeper tools run against the reloaded state inside _mutate_and_save_state."""

    def _run(self, stored: GroupState, tool: str, tool_input: dict, *, caller: GroupState | None = None):
        saves: list[GroupState] = []
        with patch.object(keeper, "load_state", lambda group_id: GroupState.from_dict(stored.to_dict())), \
                patch.object(keeper, "_save_state_checked", lambda state, reason: saves.append(state)), \
                patch.object(keeper.mutation_admission, "assert_admitted"):
            result = keeper._execute_tool(caller or GroupState.from_dict(stored.to_dict()), tool, tool_input, [], [])
        return result, saves

    def test_adjust_character_checks_the_reloaded_state_not_the_callers(self):
        """The turn loaded before another path registered a check; the stale
        copy shows nothing pending, the stored state does."""
        stored = _state("Mark")
        caller = GroupState.from_dict(stored.to_dict())
        stored.pending_checks["u1"] = dict(SEARCH_CHECK)

        with patch.object(combat.observability, "event") as event:
            result, saves = self._run(
                stored, "adjust_character", {"investigator": "Mark", "field": "hp", "delta": -6}, caller=caller,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["blocked_by"], "pending_check")
        self.assertEqual(saves, [])
        self.assertIn(("combat.major_wound.blocked",), [c.args for c in event.call_args_list])

    def test_adjust_character_refuses_during_a_luck_decision(self):
        stored = _state("Mark")
        stored.pending_luck_decisions["u1"] = dict(LUCK_DECISION)
        result, saves = self._run(stored, "adjust_character", {"investigator": "Mark", "field": "hp", "delta": -6})
        self.assertEqual(result["blocked_by"], "pending_luck_decision")
        self.assertEqual(saves, [])

    def test_combat_damage_tool_skips_the_save_when_blocked(self):
        stored = _combat_state("Mark")
        stored.pending_checks["u1"] = dict(SEARCH_CHECK)
        result, saves = self._run(stored, "apply_combat_damage", {"target": "Mark", "raw_damage": 6})
        self.assertEqual(result["blocked_by"], "pending_check")
        self.assertEqual(saves, [])

    def test_combat_damage_tool_still_saves_an_applied_hit(self):
        stored = _combat_state("Mark")
        result, saves = self._run(stored, "apply_combat_damage", {"target": "Mark", "raw_damage": 6})
        self.assertTrue(result["ok"])
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0].characters["u1"].hp, 6)
        self.assertEqual(saves[0].pending_checks["u1"]["skill"], "CON")


if __name__ == "__main__":
    unittest.main()
