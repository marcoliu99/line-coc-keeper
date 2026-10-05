"""docs/specs/enhancement/enhancement-macro-combat-initialization-tool.md.

initialize_combat collapses start_combat + N*add_npc_to_combat into one tool
call, reusing add_combatant's existing per-NPC logic exactly rather than
reimplementing it. Field parity with add_npc_to_combat, partial success with
per-entry status, and no new idempotency guarantee are all decided in the
spec.
"""
import unittest
from unittest.mock import patch

from app import tool_dispatch
from app.keeper_tools import combat as combat_handlers
from app.keeper_tools import support
from app.keeper_tools.registry import ToolCall
from app.models import GroupState
from app.services import mutation_admission
from app.services.turn_delivery import observe_tool


def _call(state: GroupState, enemies: list[dict]) -> ToolCall:
    return ToolCall(
        state=state, input={"enemies": enemies}, private_messages=[], image_requests=[],
        speaker_role="kp_assistant", name="initialize_combat",
    )


class InitializeCombatTests(unittest.TestCase):
    def test_distinct_named_enemies_are_all_added(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        result = combat_handlers.initialize_combat(_call(state, [
            {"name": "柯比特", "dex": 60, "hp": 16},
            {"name": "深潛者", "dex": 40, "hp": 10},
        ]))
        self.assertTrue(result["ok"])
        self.assertTrue(state.combat.active)
        names = {c.display_name for c in state.combat.order if not c.is_pc}
        self.assertEqual(names, {"柯比特", "深潛者"})
        self.assertEqual([e["ok"] for e in result["enemies"]], [True, True])

    def test_two_entries_sharing_one_name_are_both_added_not_collapsed(self):
        # Two Deep Ones both named "魚人" in one array are two distinct
        # individuals, not a duplicate to merge — the same rule PR #55
        # established for two separate add_npc_to_combat calls. Auto-suffix
        # is the last-resort fallback, never a silent drop.
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        result = combat_handlers.initialize_combat(_call(state, [
            {"name": "魚人", "dex": 40, "hp": 8},
            {"name": "魚人", "dex": 45, "hp": 8},
        ]))
        self.assertTrue(result["ok"])
        enemies = [c for c in state.combat.order if not c.is_pc]
        self.assertEqual(len(enemies), 2)
        display_names = {c.display_name for c in enemies}
        self.assertEqual(len(display_names), 2)  # both visible and distinguishable
        self.assertEqual([e["ok"] for e in result["enemies"]], [True, True])

    def test_index_aliases_in_one_batch_still_create_distinct_enemies(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        state.scenario_npc_index = [{"name": "Deep One", "aliases": ["魚人"], "hp": 8}]
        def mutate(current, callback):
            result = callback(current)
            return result.value if isinstance(result, support.ToolStateMutation) else result

        with patch.object(support, "mutate_tool_state", side_effect=mutate):
            result = combat_handlers.initialize_combat(_call(state, [
                {"name": "Deep One", "dex": 40, "hp": 8},
                {"name": "魚人", "dex": 45, "hp": 8},
            ]))
        self.assertEqual([entry["ok"] for entry in result["enemies"]], [True, True])
        self.assertEqual(len([member for member in state.combat.order if member.side == "enemy"]), 2)

    def test_initial_turn_uses_highest_dex_after_full_batch(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        combat_handlers.initialize_combat(_call(state, [
            {"name": "Slow", "dex": 20, "hp": 8},
            {"name": "Fast", "dex": 90, "hp": 8},
        ]))
        self.assertEqual(state.combat.order[state.combat.current_index].name, "Fast")

    def test_empty_and_invalid_batches_do_not_start_combat(self):
        for enemies in (
            [],
            [{"name": "Bad", "dex": "invalid", "hp": 8}],
            [{"name": "Bad", "dex": 40, "hp": 8, "armor": [None]}],
        ):
            state = GroupState(group_id="initialize-combat-" + self._testMethodName)
            result = combat_handlers.initialize_combat(_call(state, enemies))
            self.assertFalse(result["ok"])
            self.assertFalse(state.combat.active)

    def test_reused_existing_enemy_is_reported(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        combat_handlers.initialize_combat(_call(state, [{"name": "Existing", "dex": 40, "hp": 8}]))
        result = combat_handlers.initialize_combat(_call(state, [{"name": "Existing", "dex": 40, "hp": 8}]))
        self.assertTrue(result["enemies"][0]["reused"])
        self.assertEqual(len([member for member in state.combat.order if member.side == "enemy"]), 1)

    def test_armor_attacks_and_abilities_have_field_parity_with_the_single_add_tool(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        combat_handlers.initialize_combat(_call(state, [
            {
                "name": "柯比特", "dex": 60, "hp": 16,
                "armor": [{"id": "flesh_ward", "label": "血肉護盾", "value": 10}],
                "attacks": [{"id": "claw", "label": "利爪", "skill_name": "格鬥", "skill_value": 60, "damage": "1d6"}],
                "abilities": [{"id": "regen", "name": "再生", "priority": 1}],
            },
        ]))
        enemy = next(c for c in state.combat.order if not c.is_pc)
        card = state.combat.enemy_cards[enemy.enemy_card_id]
        self.assertEqual(card.armor[0].label, "血肉護盾")
        self.assertEqual(card.attacks[0].label, "利爪")
        self.assertEqual(card.abilities[0].name, "再生")

    def test_one_bad_entry_does_not_fail_the_whole_batch(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        result = combat_handlers.initialize_combat(_call(state, [
            {"name": "柯比特", "dex": 60, "hp": 16},
            {"name": "深潛者", "dex": "not-a-number", "hp": 10},  # invalid dex
            {"name": "教徒", "dex": 45, "hp": 6},
        ]))
        self.assertTrue(result["ok"])
        names = {c.display_name for c in state.combat.order if not c.is_pc}
        self.assertEqual(names, {"柯比特", "教徒"})  # the two valid entries were still added
        statuses = [e["ok"] for e in result["enemies"]]
        self.assertEqual(statuses, [True, False, True])
        self.assertIn("error", result["enemies"][1])

    def test_indexed_hp_is_authoritative_for_each_enemy(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        state.scenario_npc_index = [{"name": "Walter Corbitt", "aliases": ["柯比特"], "hp": 20}]

        def mutate(current, callback):
            result = callback(current)
            return result.value if isinstance(result, support.ToolStateMutation) else result

        with patch.object(support, "mutate_tool_state", side_effect=mutate):
            result = combat_handlers.initialize_combat(_call(state, [
                {"name": "柯比特", "dex": 60, "hp": 16},
            ]))
        enemy = next(c for c in state.combat.order if not c.is_pc)
        self.assertEqual(enemy.hp, 20)
        self.assertIn("HP 16", result["enemies"][0]["note"])

    def test_an_enemy_already_in_an_active_fight_is_reused_not_duplicated(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        combat_handlers.initialize_combat(_call(state, [{"name": "柯比特", "dex": 60, "hp": 16}]))
        combat_handlers.initialize_combat(_call(state, [
            {"name": "柯比特", "dex": 60, "hp": 16},  # already fighting; not a new individual
            {"name": "深潛者", "dex": 40, "hp": 10},
        ]))
        enemies = [c for c in state.combat.order if not c.is_pc]
        self.assertEqual({c.display_name for c in enemies}, {"柯比特", "深潛者"})

    def test_dispatches_through_the_registry(self):
        state = GroupState(group_id="initialize-combat-" + self._testMethodName)
        with patch.object(mutation_admission, "assert_admitted"):
            result = tool_dispatch.execute_tool(state, "initialize_combat", {
                "enemies": [{"name": "柯比特", "dex": 60, "hp": 16}],
            }, [], [])
        self.assertTrue(result["ok"])
        self.assertIn("柯比特", result["status"])

    def test_public_observation_omits_enemy_cards(self):
        result = observe_tool("initialize_combat", {
            "ok": True,
            "status": "Secret HP 20",
            "enemies": [{"name": "Corbitt", "ok": True}],
        }, 1)
        self.assertEqual(result.audience, "public")
        self.assertNotIn("Secret HP", result.public_text)
