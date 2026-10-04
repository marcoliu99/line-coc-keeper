"""docs/specs/bug/defeated_enemy_readd_design_spec.md.

A defeated enemy's name can be added again, but the caller is told so, the
newcomer gets a numbered display name, and name lookups pick the living one.
"""
import unittest
from unittest.mock import AsyncMock, patch

from app import combat
from app.commands.handlers import combat as combat_handler
from app.models import Character, Combatant, GroupState
from tests import combat_calls as calls
from tests.state_store import MemoryTransactions


def _state() -> GroupState:
    state = GroupState(group_id="g")
    char = Character(name="Mark", owner_id="u1", character_id="char-mark", dex=50, hp=12, hp_max=12)
    state.characters["u1"] = char
    state.characters_by_id[char.character_id] = char
    state.active_character_id_by_user["u1"] = char.character_id
    state.scenario_npc_index = [{"name": "Walter Corbitt", "aliases": ["柯比特"], "hp": 20}]
    return state


def _enemies(state: GroupState) -> list[Combatant]:
    return [c for c in state.combat.order if c.side == "enemy"]


def _with_defeated(name: str) -> GroupState:
    state = _state()
    with patch.object(combat.checkpoints, "create_checkpoint"):
        combat.add_combatant(state, name, 50, 20)
    for c in _enemies(state):
        c.hp, c.defeated = 0, True
    return state


class ReAddTests(unittest.TestCase):
    def _add(self, state: GroupState, name: str, **kwargs):
        with patch.object(combat.checkpoints, "create_checkpoint"):
            return combat.add_combatant(state, name, 50, 20, **kwargs)

    def test_a_defeated_name_is_added_again_numbered_and_reported(self):
        for second_name in ("Walter Corbitt", "柯比特"):
            with self.subTest(second_name=second_name):
                state = _with_defeated("Walter Corbitt")
                added = self._add(state, second_name)

                self.assertFalse(added.reused)
                self.assertEqual(added.defeated_namesake.display_name, "Walter Corbitt")
                self.assertEqual(added.combatant.hp, 20)
                self.assertFalse(added.combatant.defeated)
                # Numbered only when the display name would repeat; an alias
                # already reads differently from the defeated one.
                expected = f"{second_name} 2" if second_name == "Walter Corbitt" else second_name
                self.assertEqual(added.combatant.display_name, expected)
                self.assertIn("先前已在這場戰鬥中被打倒", combat.defeated_namesake_notice(added))

    def test_a_new_name_gets_no_notice_and_no_number(self):
        state = _state()
        added = self._add(state, "Hunter")
        self.assertIsNone(added.defeated_namesake)
        self.assertEqual(added.combatant.display_name, "Hunter")
        self.assertEqual(combat.defeated_namesake_notice(added), "")

    def test_a_live_name_is_still_reused(self):
        state = _state()
        self._add(state, "Hunter")
        added = self._add(state, "Hunter")
        self.assertTrue(added.reused)
        self.assertEqual(len(_enemies(state)), 1)

    def test_numbers_keep_counting_past_a_taken_one(self):
        state = _with_defeated("深潛者")
        self._add(state, "深潛者")
        for c in _enemies(state):
            c.hp, c.defeated = 0, True
        added = self._add(state, "深潛者")
        self.assertEqual(added.combatant.display_name, "深潛者 3")

    def test_allies_are_numbered_with_distinct_ids_and_no_notice(self):
        state = _state()
        first = self._add(state, "嚮導", is_ally=True)
        second = self._add(state, "嚮導", is_ally=True)
        self.assertEqual([first.combatant.display_name, second.combatant.display_name], ["嚮導", "嚮導 2"])
        self.assertNotEqual(first.combatant.combatant_id, second.combatant.combatant_id)
        self.assertIsNone(second.defeated_namesake)


class NameLookupTests(unittest.TestCase):
    def test_damage_by_the_shared_name_hits_the_living_one(self):
        state = _with_defeated("深潛者")
        with patch.object(combat.checkpoints, "create_checkpoint"):
            combat.add_combatant(state, "深潛者", 50, 20)
        corpse, living = _enemies(state)

        calls.damage_combatant(state, "深潛者", -3)
        calls.damage_combatant(state, "深潛者 2", -3)

        self.assertEqual(living.hp, 14)
        self.assertEqual(corpse.hp, 0)

    def test_an_old_save_with_identical_names_still_prefers_the_living(self):
        """Fights saved before numbering have two identical display names."""
        state = _state()
        combat.add_npc(state, "深潛者", 50, 10)
        combat.add_npc(state, "深潛者", 50, 10)
        corpse, living = _enemies(state)
        corpse.hp, corpse.defeated = 0, True

        calls.damage_combatant(state, "深潛者", -3)

        self.assertEqual((corpse.hp, living.hp), (0, 7))


class OperatorCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_addnpc_reply_tells_the_kp_about_the_defeated_namesake(self):
        state = _with_defeated("Walter Corbitt")
        reply = AsyncMock()
        with patch.object(combat_handler, "load_state", return_value=state), \
                MemoryTransactions(state).patched(), \
                patch.object(combat.checkpoints, "create_checkpoint"):
            await combat_handler.handle_combat_command("g", reply, ["/coc", "combat", "addnpc", "柯比特", "50", "20"])

        text = reply.await_args.args[0]
        self.assertIn("「Walter Corbitt」先前已在這場戰鬥中被打倒", text)
        self.assertIn("已加入一隻新的「柯比特」", text)


if __name__ == "__main__":
    unittest.main()
