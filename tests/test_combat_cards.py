import unittest

from app import combat
from app.models import Character, EffectState, GroupState


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

    def test_resolve_enemy_action_is_idempotent_for_same_plan(self):
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
                "usage": {"per_combat": 2},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")
        plan = combat.plan_enemy_turn(state)
        card = state.combat.enemy_cards[plan["enemy_card_id"]]

        first = combat.resolve_enemy_action(state, plan["plan_id"])
        second = combat.resolve_enemy_action(state, plan["plan_id"])

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["already_resolved"])
        self.assertEqual(card.abilities[0].usage["used_total"], 1)
        self.assertEqual(card.abilities[0].usage["used_this_round"], 1)

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
        self.assertNotIn("3", result["public_summary"])
        self.assertIn("部分傷害被擋下", result["public_summary"])

    def test_public_combat_status_hides_enemy_hp(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            armor=[{"id": "hide", "label": "Hide", "value": 2, "applies_to": "physical"}],
        )

        public = combat.status_text(state)
        private = combat.status_text(state, include_private=True)

        self.assertIn("Mark [我方] DEX 55 HP 12/12", public)
        self.assertIn("Dream Singer [敵方] DEX 60 HP 未公開", public)
        self.assertNotIn("Dream Singer [敵方] DEX 60 HP 14/14", public)
        self.assertIn("Dream Singer [敵方] DEX 60 HP 14/14", private)
        self.assertIn("護甲:Hide 2", private)

    def test_apply_combat_damage_registers_major_wound_con_check_for_pc(self):
        state = self._state_with_pc()
        combat.start_combat(state)

        result = combat.apply_combat_damage(state, "Mark", 6)

        self.assertTrue(result["ok"])
        self.assertTrue(result["major_wound_triggered"])
        self.assertEqual(state.pending_checks["u1"], {
            "type": "skill",
            "skill": "CON",
            "skill_value": 50,
            "bonus_dice": 0,
            "penalty_dice": 0,
            "difficulty": "regular",
            "major_wound_trigger": True,
        })

    def test_add_combat_effect_applies_fixed_damage_at_turn_start(self):
        state = self._state_with_pc()
        combat.start_combat(state)

        added = combat.add_combat_effect(
            state,
            "Mark",
            "Burning Curtains",
            timing="turn_start",
            damage="1",
            damage_type="fire",
            remaining_rounds=1,
            tags=["fire"],
            source_id="scene:curtains",
        )
        results = combat.process_timing(state, "turn_start", state.combat.order[0].combatant_id)

        self.assertTrue(added["ok"])
        self.assertEqual(added["damage"], "1")
        self.assertEqual(added["damage_type"], "fire")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["raw_damage"], 1)
        self.assertEqual(results[0]["damage_type"], "fire")
        self.assertEqual(state.combat.order[0].hp, 11)
        self.assertEqual(state.characters_by_id["char-mark"].hp, 11)
        self.assertEqual(state.combat.effects, [])

    def test_invalid_effect_damage_reports_error_without_consuming_duration(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        state.combat.effects.append(EffectState(
            id="effect-bad",
            label="Bad Fire",
            target_id=state.combat.order[0].combatant_id,
            timing="turn_start",
            remaining_rounds=1,
            damage="1d1",
        ))

        results = combat.process_timing(state, "turn_start", state.combat.order[0].combatant_id)

        self.assertEqual(state.combat.order[0].hp, 12)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertIn("無法解析效果傷害", results[0]["error"])
        self.assertEqual(len(state.combat.effects), 1)
        self.assertEqual(state.combat.effects[0].remaining_rounds, 1)

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
