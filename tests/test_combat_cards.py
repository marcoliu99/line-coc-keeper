import unittest

from app import combat
from app.models import Character, GroupState


class CombatCardTests(unittest.TestCase):
    def _state_with_pc(self) -> GroupState:
        state = GroupState(group_id="g")
        char = Character(
            name="Mark",
            owner_id="u1",
            character_id="char-mark",
            dex=55,
            hp=12,
            hp_max=12,
            pow_=50,
        )
        state.characters = {"u1": char}
        state.characters_by_id = {"char-mark": char}
        state.active_character_id_by_user = {"u1": "char-mark"}
        return state

    def test_enemy_special_ability_is_planned_before_default_attack(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            attacks=[{"id": "claw", "label": "Claw", "skill_value": 45, "damage": "1D4"}],
            abilities=[{
                "id": "song_of_lost_dreams",
                "name": "Song of Lost Dreams",
                "priority": 100,
                "trigger": {"type": "first_available"},
                "check": {"type": "opposed", "attacker_stat": "POW", "defender_stat": "POW"},
                "usage": {"per_combat": 1},
                "reveal_policy": {"player_facing_name": "", "hide_numbers": True, "hide_weakness": True},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")

        plan = combat.plan_enemy_turn(state)

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["selected_action"], "special_ability")
        self.assertEqual(plan["selected_id"], "song_of_lost_dreams")
        self.assertIn({"type": "opposed", "attacker_stat": "POW", "defender_stat": "POW"}, plan["required_rolls"])
        self.assertNotIn("Song of Lost Dreams", plan["public_hint"])
        self.assertNotIn("POW", plan["public_hint"])

    def test_resolve_enemy_action_consumes_usage(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            abilities=[{
                "id": "song",
                "name": "Song",
                "priority": 10,
                "trigger": {"type": "first_available"},
                "usage": {"per_combat": 1},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")
        plan = combat.plan_enemy_turn(state)

        result = combat.resolve_enemy_action(state, plan["plan_id"])
        second_plan = combat.plan_enemy_turn(state)

        self.assertTrue(result["ok"])
        self.assertEqual(second_plan["selected_action"], "attack")

    def test_per_round_usage_resets_and_cooldown_ticks_on_new_round(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            abilities=[{
                "id": "song",
                "name": "Song",
                "priority": 10,
                "trigger": {"type": "first_available"},
                "usage": {"per_round": 1},
                "cooldown_rounds": 1,
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")
        first = combat.plan_enemy_turn(state)
        combat.resolve_enemy_action(state, first["plan_id"])
        same_round = combat.plan_enemy_turn(state)

        self.assertEqual(same_round["selected_action"], "attack")

        combat.advance_turn(state)
        combat.advance_turn(state)
        next_round = combat.plan_enemy_turn(state, "Dream Singer")

        self.assertEqual(next_round["selected_action"], "special_ability")

    def test_apply_combat_damage_tracks_armor_breakdown(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Armored Thing",
            40,
            10,
            armor=[{"id": "hide", "label": "Thick Hide", "value": 3, "applies_to": "physical"}],
        )

        result = combat.apply_combat_damage(state, "Armored Thing", 8, damage_type="physical")

        self.assertTrue(result["ok"])
        self.assertEqual(result["raw_damage"], 8)
        self.assertEqual(result["armor_reduction"], 3)
        self.assertEqual(result["final_damage"], 5)
        self.assertEqual(result["hp_after"], 5)
        self.assertIn("raw=8", result["private_notes"])

    def test_enemy_card_survives_group_state_round_trip(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            armor=[{"id": "hide", "label": "Hide", "value": 2, "applies_to": "physical"}],
            abilities=[{
                "id": "song",
                "name": "Song of Lost Dreams",
                "priority": 10,
                "trigger": {"type": "first_available"},
                "usage": {"per_combat": 1},
            }],
        )

        restored = GroupState.from_dict(state.to_dict())
        plan = combat.plan_enemy_turn(restored, "Dream Singer")
        damage = combat.apply_combat_damage(restored, "Dream Singer", 5, damage_type="physical")

        self.assertEqual(plan["selected_action"], "special_ability")
        self.assertEqual(damage["armor_reduction"], 2)
        self.assertEqual(damage["hp_after"], 11)

    def test_active_character_identity_keeps_partner_state_separate(self):
        state = GroupState(group_id="g")
        mark = Character(name="Mark", owner_id="u1", character_id="char-mark", slot="primary", dex=50)
        partner = Character(name="Partner", owner_id="u1", character_id="char-partner", slot="partner", dex=70)
        state.characters = {"u1": mark}
        state.characters_by_id = {"char-mark": mark, "char-partner": partner}
        state.active_character_id_by_user = {"u1": "char-partner"}

        combat.start_combat(state)

        self.assertEqual([c.name for c in state.combat.order], ["Partner"])
        self.assertEqual(state.combat.order[0].character_id, "char-partner")


if __name__ == "__main__":
    unittest.main()
