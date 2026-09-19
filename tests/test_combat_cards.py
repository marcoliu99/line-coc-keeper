import unittest
from unittest.mock import patch

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

    def _state_with_two_pcs(self) -> GroupState:
        state = GroupState(group_id="g")
        first = Character(
            name="First",
            owner_id="u1",
            character_id="char-first",
            dex=70,
            hp=12,
            hp_max=12,
        )
        second = Character(
            name="Second",
            owner_id="u2",
            character_id="char-second",
            dex=60,
            hp=12,
            hp_max=12,
        )
        state.characters = {"u1": first, "u2": second}
        state.characters_by_id = {
            first.character_id: first,
            second.character_id: second,
        }
        state.active_character_id_by_user = {
            "u1": first.character_id,
            "u2": second.character_id,
        }
        return state

    def _enemy_turn_with_two_pcs(self) -> tuple[GroupState, str]:
        state = self._state_with_two_pcs()
        combat.start_combat(state)
        combat.add_npc(state, "Hunter", 50, 14)
        enemy = next(c for c in state.combat.order if c.side == "enemy")
        state.combat.current_index = state.combat.order.index(enemy)
        card = state.combat.enemy_cards[enemy.enemy_card_id]
        return state, card.id

    def test_group_state_reuses_character_objects_across_legacy_and_id_indexes(self):
        legacy = Character(name="Mark", owner_id="u1", character_id="char-mark", hp=10).to_dict()
        indexed = Character(name="Mark", owner_id="u1", character_id="char-mark", hp=8).to_dict()
        new_legacy = Character(name="Partner", owner_id="u2", hp=9).to_dict()

        state = GroupState.from_dict({
            "group_id": "g",
            "characters": {"u1": legacy, "u2": new_legacy},
            "characters_by_id": {"char-mark": indexed},
        })

        self.assertIs(state.characters["u1"], state.characters_by_id["char-mark"])
        self.assertIn("legacy-user:u2", state.characters_by_id)
        self.assertIs(state.characters["u2"], state.characters_by_id["legacy-user:u2"])
        state.characters["u1"].hp = 2
        self.assertEqual(state.characters_by_id["char-mark"].hp, 2)
        self.assertEqual(state.active_character_id_by_user["u2"], "legacy-user:u2")

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

    def test_successful_ability_materializes_declared_effect(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            abilities=[{
                "id": "song",
                "name": "Song of Lost Dreams",
                "priority": 10,
                "trigger": {"type": "first_available"},
                "effect": {
                    "on_success": "apply_effect",
                    "effect_id": "lost-dreams-trance",
                    "label": "失夢恍惚",
                    "timing": "turn_start",
                    "damage": "1",
                    "damage_type": "mental",
                    "remaining_rounds": 2,
                },
                "usage": {"per_combat": 1},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")

        plan = combat.plan_enemy_turn(state)
        result = combat.resolve_enemy_action(state, plan["plan_id"], outcome={"success": True})

        self.assertTrue(result["ok"])
        self.assertTrue(result["effect"]["applied"])
        self.assertEqual(state.combat.effects[0].id, "lost-dreams-trance")
        self.assertEqual(state.combat.effects[0].target_id, "pc:char-mark")

    def test_ability_effect_requires_outcome_before_consuming_usage(self):
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
                "trigger": {"type": "first_available"},
                "effect": {"on_success": "apply_effect", "effect_id": "trance"},
                "usage": {"per_combat": 1},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")
        plan = combat.plan_enemy_turn(state)

        result = combat.resolve_enemy_action(state, plan["plan_id"])

        self.assertFalse(result["ok"])
        card = state.combat.enemy_cards[plan["enemy_card_id"]]
        self.assertEqual(card.abilities[0].usage.get("used_total", 0), 0)
        self.assertFalse(plan.get("resolved", False))

    def test_plan_enemy_turn_does_not_reapply_turn_start_effects(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(state, "Burning Thing", 60, 10)
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Burning Thing")
        combat.add_combat_effect(
            state,
            "Burning Thing",
            "Burning",
            timing="turn_start",
            damage="1",
            damage_type="fire",
            remaining_rounds=2,
            tags=["fire"],
        )

        first = combat.plan_enemy_turn(state)
        second = combat.plan_enemy_turn(state)
        enemy = next(c for c in state.combat.order if c.name == "Burning Thing")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(enemy.hp, 9)
        self.assertEqual(state.combat.effects[0].remaining_rounds, 1)

    def test_turn_start_lethal_effect_prevents_enemy_action(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(state, "Burning Thing", 60, 1)
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Burning Thing")
        combat.add_combat_effect(
            state, "Burning Thing", "Fatal fire", timing="turn_start", damage="1", remaining_rounds=1
        )

        plan = combat.plan_enemy_turn(state)

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["selected_action"], "none")
        self.assertTrue(next(c for c in state.combat.order if c.name == "Burning Thing").defeated)

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

    def test_round_start_trigger_becomes_available_on_new_round(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            abilities=[{
                "id": "round_song",
                "name": "Round Song",
                "priority": 10,
                "trigger": {"type": "round_start"},
                "usage": {"per_round": 1},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")

        first_round = combat.plan_enemy_turn(state)
        combat.advance_turn(state)
        combat.advance_turn(state)
        next_round = combat.plan_enemy_turn(state)

        self.assertEqual(first_round["selected_action"], "attack")
        self.assertEqual(next_round["selected_action"], "special_ability")
        self.assertEqual(next_round["selected_id"], "round_song")

    def test_on_damage_taken_trigger_fires_after_enemy_damage(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Spiteful Thing",
            60,
            10,
            abilities=[{
                "id": "retaliate",
                "name": "Retaliate",
                "priority": 10,
                "trigger": {"type": "on_damage_taken"},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Spiteful Thing")

        before_damage = combat.plan_enemy_turn(state)
        combat.apply_combat_damage(state, "Spiteful Thing", 1)
        after_damage = combat.plan_enemy_turn(state)
        combat.resolve_enemy_action(state, after_damage["plan_id"])
        after_resolve = combat.plan_enemy_turn(state)

        self.assertEqual(before_damage["selected_action"], "attack")
        self.assertEqual(after_damage["selected_action"], "special_ability")
        self.assertEqual(after_damage["selected_id"], "retaliate")
        self.assertEqual(after_resolve["selected_action"], "attack")

    def test_target_in_range_trigger_uses_abstract_range_band(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(
            state,
            "Dream Singer",
            60,
            14,
            abilities=[{
                "id": "near_song",
                "name": "Near Song",
                "priority": 10,
                "trigger": {"type": "target_in_range", "range_band": "near"},
            }],
        )
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Dream Singer")
        card = next(iter(state.combat.enemy_cards.values()))
        target_id = next(c.combatant_id for c in state.combat.order if c.side == "pc")

        state.combat.range_bands[f"{card.id}:{target_id}"] = "far"
        far_plan = combat.plan_enemy_turn(state)
        state.combat.range_bands[f"{card.id}:{target_id}"] = "near"
        near_plan = combat.plan_enemy_turn(state)

        self.assertEqual(far_plan["selected_action"], "move")
        self.assertEqual(near_plan["selected_action"], "special_ability")
        self.assertEqual(near_plan["selected_id"], "near_song")

    def test_enemy_target_prefers_engaged_pc_over_near_pc(self):
        state, enemy_card_id = self._enemy_turn_with_two_pcs()
        state.combat.range_bands[f"{enemy_card_id}:pc:char-first"] = "near"
        state.combat.range_bands[f"{enemy_card_id}:pc:char-second"] = "engaged"

        plan = combat.plan_enemy_turn(state)

        self.assertEqual(plan["target_ids"], ["pc:char-second"])

    def test_enemy_target_randomizes_valid_pcs_when_no_preferred_distance(self):
        state, enemy_card_id = self._enemy_turn_with_two_pcs()
        state.combat.range_bands[f"{enemy_card_id}:pc:char-first"] = "far"
        state.combat.range_bands[f"{enemy_card_id}:pc:char-second"] = "far"

        with patch("app.combat.random.choice", return_value="pc:char-second") as choose:
            plan = combat.plan_enemy_turn(state)

        self.assertEqual(plan["target_ids"], ["pc:char-second"])
        choose.assert_called_once_with(["pc:char-first", "pc:char-second"])

    def test_enemy_moves_when_selected_target_is_out_of_attack_range(self):
        state, enemy_card_id = self._enemy_turn_with_two_pcs()
        target_id = "pc:char-first"
        next(c for c in state.combat.order if c.combatant_id == "pc:char-second").defeated = True
        state.combat.range_bands[f"{enemy_card_id}:{target_id}"] = "far"

        plan = combat.plan_enemy_turn(state)

        self.assertEqual(plan["target_ids"], [target_id])
        self.assertEqual(plan["selected_action"], "move")

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

    def test_pc_turn_start_effect_triggers_when_advance_turn_reaches_pc(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(state, "Fast Enemy", 80, 10)
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Fast Enemy")
        combat.add_combat_effect(
            state,
            "Mark",
            "Burning",
            timing="turn_start",
            damage="1",
            damage_type="fire",
            remaining_rounds=1,
            tags=["fire"],
        )

        result = combat.advance_turn(state)

        self.assertTrue(result["ok"])
        self.assertEqual(result["current_turn"], "Mark")
        self.assertEqual(state.combat.order[state.combat.current_index].hp, 11)
        self.assertEqual(state.characters_by_id["char-mark"].hp, 11)
        self.assertEqual(state.combat.effects, [])

    def test_round_end_effect_triggers_before_new_round(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(state, "Fast Enemy", 80, 10)
        # Advance from the last initiative slot so this transition crosses
        # the round boundary and exercises round_end before round_start.
        state.combat.current_index = len(state.combat.order) - 1
        combat.add_combat_effect(
            state,
            "Mark",
            "Round-end fire",
            timing="round_end",
            damage="1",
            damage_type="fire",
            remaining_rounds=1,
            tags=["fire"],
        )

        result = combat.advance_turn(state)

        self.assertTrue(result["ok"])
        self.assertEqual(state.combat.round_number, 2)
        self.assertEqual(state.characters_by_id["char-mark"].hp, 11)
        self.assertEqual(state.combat.effects, [])

    def test_round_end_effect_does_not_trigger_before_round_boundary(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        combat.add_npc(state, "Fast Enemy", 80, 10)
        state.combat.current_index = 0
        combat.add_combat_effect(
            state,
            "Mark",
            "Round-end fire",
            timing="round_end",
            damage="1",
            remaining_rounds=2,
        )

        result = combat.advance_turn(state)

        self.assertTrue(result["ok"])
        self.assertEqual(state.combat.round_number, 1)
        self.assertEqual(state.characters_by_id["char-mark"].hp, 12)
        self.assertEqual(state.combat.effects[0].remaining_rounds, 2)

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

    def test_failed_effect_timing_can_retry_after_correction(self):
        state = self._state_with_pc()
        combat.start_combat(state)
        target_id = state.combat.order[0].combatant_id
        state.combat.effects.append(EffectState(
            id="effect-retry",
            label="Retry Fire",
            target_id=target_id,
            timing="turn_start",
            remaining_rounds=1,
            damage="1d1",
        ))

        first = combat.process_timing(state, "turn_start", target_id)
        state.combat.effects[0].damage = "1"
        second = combat.process_timing(state, "turn_start", target_id)

        self.assertFalse(first[0]["ok"])
        self.assertTrue(second[0]["ok"])
        self.assertEqual(state.combat.order[0].hp, 11)
        self.assertEqual(state.combat.effects, [])

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

    def test_same_named_characters_sync_hp_by_character_id(self):
        state = GroupState(group_id="same-name")
        first = Character("Alex", "u1", character_id="char-1", hp=10, hp_max=10, active=True)
        second = Character("Alex", "u2", character_id="char-2", hp=10, hp_max=10, active=True)
        state.characters = {"u1": first, "u2": second}
        state.characters_by_id = {first.character_id: first, second.character_id: second}
        state.active_character_id_by_user = {"u1": first.character_id, "u2": second.character_id}
        combat.start_combat(state)

        combat.apply_combat_damage(state, "pc:char-2", 3)

        self.assertEqual(first.hp, 10)
        self.assertEqual(second.hp, 7)

    def test_switch_active_character_updates_legacy_and_id_views(self):
        state = GroupState("g")
        mark = Character("Mark", "u1", character_id="char-mark", slot="primary", hp=10)
        partner = Character("Partner", "u1", character_id="char-partner", slot="partner", hp=6, active=False)
        state.characters = {"u1": mark}
        state.characters_by_id = {mark.character_id: mark, partner.character_id: partner}
        state.active_character_id_by_user = {"u1": mark.character_id}

        selected = state.set_active_character("u1", "char-partner")

        self.assertIs(selected, partner)
        self.assertIs(state.get_active_character("u1"), partner)
        self.assertIs(state.characters["u1"], partner)
        self.assertFalse(mark.active)
        self.assertTrue(partner.active)


if __name__ == "__main__":
    unittest.main()
