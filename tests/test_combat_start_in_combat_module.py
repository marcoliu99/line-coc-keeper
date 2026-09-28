"""docs/specs/refactor/combat_start_in_combat_module_design_spec.md.

The pre-combat checkpoint and the live-enemy duplicate guard live in
combat.py; the /coc combat operator command and the Keeper tools both go
through them, so the two paths can't drift apart again.
"""
import unittest
from unittest.mock import AsyncMock, patch

from app import combat
from app.commands.handlers import combat as combat_handler
from app.models import Character, GroupState


def _state() -> GroupState:
    state = GroupState(group_id="g")
    char = Character(name="Mark", owner_id="u1", character_id="char-mark", dex=50, hp=12, hp_max=12)
    state.characters["u1"] = char
    state.characters_by_id[char.character_id] = char
    state.active_character_id_by_user["u1"] = char.character_id
    state.scenario_npc_index = [{"name": "Walter Corbitt", "aliases": ["柯比特"], "hp": 20}]
    return state


def _enemies(state: GroupState) -> list[str]:
    return [c.name for c in state.combat.order if c.side == "enemy"]


class OperatorCommandTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, state: GroupState, *parts: str) -> tuple[GroupState, AsyncMock, list]:
        """Run `/coc combat <parts>` against `state`; return the saved state,
        the reply mock and the checkpoint calls."""
        saved: list[GroupState] = []
        reply = AsyncMock()
        with patch.object(combat_handler, "load_state", return_value=state), \
                patch.object(combat_handler, "save_state", side_effect=lambda s, reason: saved.append(s)), \
                patch.object(combat.checkpoints, "create_checkpoint") as checkpoint:
            await combat_handler.handle_combat_command("g", reply, ["/coc", "combat", *parts])
        return (saved[-1] if saved else state), reply, checkpoint.call_args_list

    async def test_start_checkpoints_once_before_the_fight(self):
        state = _state()
        state.state_revision = 7
        state, _, calls = await self._run(state, "start")

        self.assertTrue(state.combat.active)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["label"], "開戰前")
        self.assertEqual(calls[0].kwargs["reason"], "auto_combat_start")
        self.assertEqual(calls[0].kwargs["event_id"], "combat-start:g:7")

    async def test_start_during_a_fight_does_not_checkpoint(self):
        state = _state()
        combat.start_combat(state)
        _, _, calls = await self._run(state, "start")
        self.assertEqual(calls, [])

    async def test_addnpc_starts_the_fight_with_one_checkpoint(self):
        state, _, calls = await self._run(_state(), "addnpc", "Hunter", "50", "14")
        self.assertEqual(_enemies(state), ["Hunter"])
        self.assertEqual(len(calls), 1)

    async def test_addnpc_reuses_a_live_enemy_by_name_or_index_alias(self):
        for second_name in ("Walter Corbitt", "柯比特"):
            with self.subTest(second_name=second_name):
                state = _state()
                combat.add_npc(state, "Walter Corbitt", 50, 20)
                state, reply, calls = await self._run(state, "addnpc", second_name, "50", "20")

                self.assertEqual(_enemies(state), ["Walter Corbitt"])
                self.assertIn("沒有重複建立第二份", reply.await_args.args[0])
                self.assertEqual(calls, [])

    async def test_addally_is_never_deduplicated(self):
        state = _state()
        combat.add_npc(state, "Guide", 50, 10, is_ally=True)
        state, _, _ = await self._run(state, "addally", "Guide", "50", "10")
        self.assertEqual([c.name for c in state.combat.order if c.name == "Guide"], ["Guide", "Guide"])

    async def test_a_defeated_enemy_can_be_added_fresh(self):
        state = _state()
        combat.add_npc(state, "Hunter", 50, 14)
        next(c for c in state.combat.order if c.name == "Hunter").defeated = True
        state, _, _ = await self._run(state, "addnpc", "Hunter", "50", "14")
        self.assertEqual(_enemies(state), ["Hunter", "Hunter"])


class SharedWithKeeperToolsTests(unittest.TestCase):
    """The Keeper tools and the operator command build the same checkpoint."""

    def test_begin_combat_and_add_combatant_checkpoint_only_when_starting(self):
        state = _state()
        with patch.object(combat.checkpoints, "create_checkpoint") as checkpoint:
            combat.begin_combat(state)
            combat.begin_combat(state)
            self.assertFalse(combat.add_combatant(state, "Hunter", 50, 14).reused)
        self.assertEqual(checkpoint.call_count, 1)

    def test_add_combatant_returns_the_existing_enemy_without_adding(self):
        state = _state()
        with patch.object(combat.checkpoints, "create_checkpoint"):
            combat.add_combatant(state, "Walter Corbitt", 50, 20)
            added = combat.add_combatant(state, "柯比特", 50, 20)
        self.assertTrue(added.reused)
        self.assertEqual(added.combatant.name, "Walter Corbitt")
        self.assertEqual(_enemies(state), ["Walter Corbitt"])


if __name__ == "__main__":
    unittest.main()
