import sys
import types
import unittest
from unittest.mock import patch

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

from app import legacy_commands as commands
from app.commands import router
from app.commands.sudo import parse_sudo_command
from app.models import Character, Combatant, CombatState, EffectState, GroupState


def clone_state(state: GroupState) -> GroupState:
    return GroupState.from_dict(state.to_dict())


class ReplyCollector:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


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
            if hasattr(module, "load_state"):
                self.originals.append((module, "load_state", module.load_state))
                module.load_state = load_state
            if hasattr(module, "save_state"):
                self.originals.append((module, "save_state", module.save_state))
                module.save_state = save_state
        return self

    def __exit__(self, exc_type, exc, tb):
        for module, name, original in reversed(self.originals):
            setattr(module, name, original)

    def put(self, state: GroupState) -> None:
        self.store[state.group_id] = clone_state(state)

    def get(self, group_id: str) -> GroupState:
        return clone_state(self.store[group_id])


async def _noop(*args) -> None:
    return None


class SudoParserTests(unittest.TestCase):
    def test_parser_accepts_mentions_opaque_ids_and_subject_lifecycle_commands(self):
        parsed, error = parse_sudo_command(["/coc", "sudo", "<@123>", "away"])
        self.assertIsNone(error)
        self.assertEqual(parsed.subject_user_id, "123")
        self.assertEqual(parsed.player_parts, ["/coc", "away"])

        parsed, error = parse_sudo_command(
            ["/coc", "sudo", "player-1", "retire", "小明"], allow_opaque_target=True
        )
        self.assertIsNone(error)
        self.assertEqual(parsed.subject_user_id, "player-1")
        self.assertEqual(parsed.player_parts, ["/coc", "retire", "小明"])
        self.assertEqual(
            parse_sudo_command(["/coc", "sudo", "player-1", "retire", "小明"])[1],
            "invalid_target",
        )

    def test_parser_rejects_player_only_luck_roll_and_character_creation(self):
        self.assertEqual(
            parse_sudo_command(["/coc", "sudo", "p1", "luck", "roll"], allow_opaque_target=True)[1],
            "player_only_luck_roll",
        )
        self.assertEqual(
            parse_sudo_command(["/coc", "sudo", "p1", "usepregen", "1"], allow_opaque_target=True)[1],
            "player_only_character_creation",
        )
        self.assertEqual(
            parse_sudo_command(["/coc", "sudo", "Player Name", "sheet"])[1],
            "invalid_target",
        )
        self.assertEqual(
            parse_sudo_command(["/coc", "sudo", "p1", "sheet"], allow_opaque_target=False)[1],
            "invalid_target",
        )


class SudoStateTests(unittest.TestCase):
    def test_retire_active_character_preserves_history_and_removes_binding(self):
        state = GroupState(group_id="g")
        character = Character(name="小明", owner_id="p1")
        state.characters["p1"] = character
        state.set_active_character("p1", character.character_id)
        state.pending_checks["p1"] = {"type": "skill"}
        state.pending_luck_decisions["p1"] = {"options": []}
        state.pending_pregen_luck["p1"] = character.character_id

        retired = state.retire_active_character("p1", "小明")

        self.assertIs(retired, state.characters_by_id[retired.character_id])
        self.assertIsNone(state.get_active_character("p1"))
        self.assertNotIn("p1", state.characters)
        self.assertFalse(state.characters_by_id[retired.character_id].active)
        self.assertNotIn("p1", state.pending_checks)
        self.assertNotIn("p1", state.pending_luck_decisions)
        self.assertEqual(state.pending_pregen_luck["p1"], retired.character_id)

    def test_retire_removes_character_from_live_combat_and_selects_next_turn(self):
        state = GroupState(group_id="g")
        first = Character(name="小明", owner_id="p1", dex=70)
        second = Character(name="小華", owner_id="p2", dex=60)
        state.characters.update({"p1": first, "p2": second})
        state.set_active_character("p1", first.character_id)
        state.set_active_character("p2", second.character_id)
        state.combat = CombatState(
            active=True,
            round_number=1,
            order=[
                Combatant(name=first.name, dex=first.dex, hp=first.hp, hp_max=first.hp_max,
                          is_pc=True, side="pc", character_id=first.character_id,
                          combatant_id=f"pc:{first.character_id}"),
                Combatant(name=second.name, dex=second.dex, hp=second.hp, hp_max=second.hp_max,
                          is_pc=True, side="pc", character_id=second.character_id,
                          combatant_id=f"pc:{second.character_id}"),
            ],
            current_index=0,
        )
        retired_id = f"pc:{first.character_id}"
        survivor_id = f"pc:{second.character_id}"
        state.combat.plans = {
            "remove": {"enemy_combatant_id": "enemy:1", "target_ids": [retired_id]},
            "keep": {"enemy_combatant_id": "enemy:2", "target_ids": [survivor_id]},
        }
        state.combat.effects = [
            EffectState(id="remove-effect", label="stun", target_id=retired_id),
            EffectState(id="keep-effect", label="shield", target_id=survivor_id),
        ]
        state.combat.range_bands = {
            f"enemy:1:{retired_id}": "engaged",
            f"enemy:2:{survivor_id}": "near",
        }

        state.retire_active_character("p1", "小明")

        self.assertEqual([item.character_id for item in state.combat.order], [second.character_id])
        self.assertEqual(state.combat.current_index, 0)
        self.assertTrue(state.combat.active)
        self.assertEqual(set(state.combat.plans), {"keep"})
        self.assertEqual([effect.id for effect in state.combat.effects], ["keep-effect"])
        self.assertEqual(state.combat.range_bands, {f"enemy:2:{survivor_id}": "near"})

    def test_retire_cleans_legacy_combat_entry_without_character_id(self):
        state = GroupState(group_id="g")
        character = Character(name="小明", owner_id="p1")
        state.characters["p1"] = character
        state.set_active_character("p1", character.character_id)
        state.combat = CombatState(
            active=True,
            round_number=1,
            order=[Combatant(name="小明", dex=50, hp=10, hp_max=10, is_pc=True, side="pc")],
            current_index=0,
        )

        state.retire_active_character("p1", "小明")

        self.assertEqual(state.combat.order, [])
        self.assertFalse(state.combat.active)

    def test_retire_current_turn_skips_away_and_processes_next_turn_start(self):
        state = GroupState(group_id="g")
        first = Character(name="小明", owner_id="p1", dex=70)
        away = Character(name="小華", owner_id="p2", dex=60, away=True)
        next_player = Character(name="小安", owner_id="p3", dex=50)
        state.characters.update({"p1": first, "p2": away, "p3": next_player})
        for owner_id, character in state.characters.items():
            state.set_active_character(owner_id, character.character_id)

        first_id = f"pc:{first.character_id}"
        away_id = f"pc:{away.character_id}"
        next_id = f"pc:{next_player.character_id}"
        state.combat = CombatState(
            active=True,
            round_number=1,
            order=[
                Combatant(name=first.name, dex=first.dex, hp=first.hp, hp_max=first.hp_max,
                          is_pc=True, side="pc", character_id=first.character_id,
                          combatant_id=first_id),
                Combatant(name=away.name, dex=away.dex, hp=away.hp, hp_max=away.hp_max,
                          is_pc=True, side="pc", character_id=away.character_id,
                          combatant_id=away_id),
                Combatant(name=next_player.name, dex=next_player.dex, hp=next_player.hp,
                          hp_max=next_player.hp_max, is_pc=True, side="pc",
                          character_id=next_player.character_id, combatant_id=next_id),
            ],
            current_index=0,
            effects=[EffectState(id="next-turn", label="毒", target_id=next_id, timing="turn_start", damage="3")],
        )

        state.retire_active_character("p1", first.name)

        self.assertEqual(state.combat.order[state.combat.current_index].combatant_id, next_id)
        self.assertEqual(state.combat.order[state.combat.current_index].hp, 7)
        self.assertEqual(state.characters_by_id[next_player.character_id].hp, 7)
        self.assertTrue(any("turn_start" in key for key in state.combat.processed_timings))

    def test_retire_current_turn_wraps_round_before_next_turn_start(self):
        state = GroupState(group_id="g")
        first = Character(name="小明", owner_id="p1", dex=70)
        next_player = Character(name="小華", owner_id="p2", dex=60)
        state.characters.update({"p1": first, "p2": next_player})
        state.set_active_character("p1", first.character_id)
        state.set_active_character("p2", next_player.character_id)

        first_id = f"pc:{first.character_id}"
        next_id = f"pc:{next_player.character_id}"
        state.combat = CombatState(
            active=True,
            round_number=1,
            order=[
                Combatant(name=next_player.name, dex=next_player.dex, hp=next_player.hp,
                          hp_max=next_player.hp_max, is_pc=True, side="pc",
                          character_id=next_player.character_id, combatant_id=next_id),
                Combatant(name=first.name, dex=first.dex, hp=first.hp, hp_max=first.hp_max,
                          is_pc=True, side="pc", character_id=first.character_id,
                          combatant_id=first_id),
            ],
            current_index=1,
        )

        state.retire_active_character("p1", first.name)

        self.assertEqual(state.combat.round_number, 2)
        self.assertEqual(state.combat.current_index, 0)
        self.assertEqual(state.combat.order[0].combatant_id, next_id)
        self.assertIn("round_end", " ".join(state.combat.processed_timings))
        self.assertIn("round_start", " ".join(state.combat.processed_timings))

    def test_retire_current_turn_keeps_all_skippable_combat_without_phantom_round(self):
        state = GroupState(group_id="g")
        away = Character(name="暫離", owner_id="p1", dex=70, away=True)
        current = Character(name="離場", owner_id="p2", dex=60)
        state.characters.update({"p1": away, "p2": current})
        state.set_active_character("p1", away.character_id)
        state.set_active_character("p2", current.character_id)

        away_id = f"pc:{away.character_id}"
        current_id = f"pc:{current.character_id}"
        state.combat = CombatState(
            active=True,
            round_number=3,
            order=[
                Combatant(name=away.name, dex=away.dex, hp=away.hp, hp_max=away.hp_max,
                          is_pc=True, side="pc", character_id=away.character_id,
                          combatant_id=away_id),
                Combatant(name=current.name, dex=current.dex, hp=current.hp, hp_max=current.hp_max,
                          is_pc=True, side="pc", character_id=current.character_id,
                          combatant_id=current_id),
            ],
            current_index=1,
            effects=[EffectState(id="round-end", label="毒", timing="round_end", remaining_rounds=2)],
        )

        state.retire_active_character("p2", current.name)

        self.assertEqual(state.combat.round_number, 3)
        self.assertEqual(state.combat.current_index, 0)
        self.assertEqual(state.combat.processed_timings, [])
        self.assertEqual(state.combat.effects[0].remaining_rounds, 2)
        self.assertEqual(state.combat.order[0].combatant_id, away_id)

    def test_stale_inactive_active_binding_is_not_returned(self):
        state = GroupState(group_id="g")
        character = Character(name="已退出", owner_id="p1")
        state.characters["p1"] = character
        state.set_active_character("p1", character.character_id)
        character.active = False

        self.assertIsNone(state.get_active_character("p1"))
        self.assertNotIn("p1", state.active_character_id_by_user)
        self.assertNotIn("p1", state.characters)

    def test_stale_active_binding_cannot_cross_character_owner(self):
        state = GroupState(group_id="g")
        owner_character = Character(name="小明", owner_id="p1")
        foreign_character = Character(name="小華", owner_id="p2")
        state.characters.update({"p1": owner_character, "p2": foreign_character})
        state.set_active_character("p1", owner_character.character_id)
        state.set_active_character("p2", foreign_character.character_id)
        state.active_character_id_by_user["p1"] = foreign_character.character_id

        self.assertIs(state.get_active_character("p1"), owner_character)
        self.assertEqual(state.active_character_id_by_user["p1"], owner_character.character_id)
        self.assertIs(state.get_active_character("p2"), foreign_character)

    def test_legacy_owner_index_cannot_return_foreign_character(self):
        state = GroupState(group_id="g")
        state.characters["p1"] = Character(name="小華", owner_id="p2")

        self.assertIsNone(state.get_active_character("p1"))
        self.assertNotIn("p1", state.characters)


class SudoRouterTests(unittest.IsolatedAsyncioTestCase):
    def _state(self, *, actor_has_character: bool = False) -> GroupState:
        state = GroupState(group_id="g", active=True, game_started=True, kp_assistant_user_id="kp")
        target = Character(name="小明", owner_id="p1")
        state.characters["p1"] = target
        state.set_active_character("p1", target.character_id)
        if actor_has_character:
            actor = Character(name="KP 的角色", owner_id="kp")
            state.characters["kp"] = actor
            state.set_active_character("kp", actor.character_id)
        return state

    async def test_non_kp_actor_cannot_sudo_or_mutate_target(self):
        state = self._state()
        with StateStorePatch(router, commands) as store:
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "intruder", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 away",
                allow_opaque_sudo_target=True,
            )
            saved = store.get("g")

        self.assertFalse(saved.get_active_character("p1").away)
        self.assertIn("只有目前的 KP Assistant", reply.messages[0])

    async def test_sudo_act_requires_started_game(self):
        state = self._state()
        state.game_started = False
        run_turn = router.supervisor.run_turn
        router.supervisor.run_turn = lambda **kwargs: self.fail("run_turn must not be called")
        try:
            with StateStorePatch(router, commands) as store:
                store.put(state)
                reply = ReplyCollector()
                await router.handle_text_message(
                    "g", "kp", _noop, reply, _noop, _noop, _noop,
                    "/coc sudo p1 act 調查房間",
                    allow_opaque_sudo_target=True,
                )
        finally:
            router.supervisor.run_turn = run_turn

        self.assertIn("已開始的遊戲", reply.messages[0])

    async def test_kp_can_mark_missing_player_away(self):
        state = self._state()
        with StateStorePatch(router, commands, router.system_handler) as store:
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 away",
                allow_opaque_sudo_target=True,
            )
            saved = store.get("g")

        self.assertTrue(saved.get_active_character("p1").away)
        self.assertEqual(saved.kp_assistant_user_id, "kp")
        self.assertIn("【KP Assistant 代操作：小明】", reply.messages[0])

    async def test_kp_can_retire_missing_player_without_deleting_history(self):
        state = self._state()
        with StateStorePatch(router, commands, router.character_handler) as store:
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 retire 小明",
                allow_opaque_sudo_target=True,
            )
            saved = store.get("g")

        self.assertIsNone(saved.get_active_character("p1"))
        self.assertEqual(len(saved.characters_for_owner("p1")), 1)
        self.assertEqual(saved.characters_for_owner("p1")[0].name, "小明")
        self.assertEqual(saved.kp_assistant_user_id, "kp")
        self.assertIn("【KP Assistant 代操作：小明】", reply.messages[0])

    async def test_kp_sudo_act_uses_subject_player_turn_and_marker(self):
        state = self._state()
        calls = []

        async def fake_run_turn(**kwargs):
            calls.append(kwargs)
            return "角色行動結果", [], []

        async def fake_maintenance(conversation_id, reply, public_message, *args, **kwargs):
            await reply(public_message)

        original_run_turn = router.supervisor.run_turn
        original_resolve = router._resolve_map_action_transaction
        original_maintenance = router._run_post_turn_maintenance_after_output
        router.supervisor.run_turn = fake_run_turn
        router._resolve_map_action_transaction = lambda *args: None
        router._run_post_turn_maintenance_after_output = fake_maintenance
        try:
            with StateStorePatch(router, commands) as store:
                store.put(state)
                reply = ReplyCollector()
                await router.handle_text_message(
                    "g", "kp", _noop, reply, _noop, _noop, _noop,
                    "/coc sudo p1 act 調查房間",
                    allow_opaque_sudo_target=True,
                )
        finally:
            router.supervisor.run_turn = original_run_turn
            router._resolve_map_action_transaction = original_resolve
            router._run_post_turn_maintenance_after_output = original_maintenance

        self.assertEqual(calls[0]["user_id"], "p1")
        self.assertEqual(calls[0]["speaker_role"], "player")
        self.assertTrue(calls[0]["text"].startswith("[KP Assistant 代操作 小明]"))
        self.assertEqual(reply.messages, ["【KP Assistant 代操作：小明】\n角色行動結果"])

    async def test_actor_with_player_role_cannot_sudo_and_luck_roll_is_unchanged(self):
        state = self._state(actor_has_character=True)
        with StateStorePatch(router, commands, router.system_handler) as store:
            store.put(state)
            role_conflict = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, role_conflict, _noop, _noop, _noop,
                "/coc sudo p1 away",
                allow_opaque_sudo_target=True,
            )
            self.assertFalse(store.get("g").get_active_character("p1").away)

            luck_roll = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, luck_roll, _noop, _noop, _noop,
                "/coc sudo p1 luck roll",
                allow_opaque_sudo_target=True,
            )

        self.assertIn("必須先脫離", role_conflict.messages[0])
        self.assertEqual(luck_roll.messages, ["LUCK 必須由玩家本人擲骰。"])

    async def test_player_can_retire_then_register_as_kp_assistant(self):
        state = GroupState(group_id="g", active=True, game_started=True)
        character = Character(name="小明", owner_id="p1")
        state.characters["p1"] = character
        state.set_active_character("p1", character.character_id)
        with StateStorePatch(router, commands, router.character_handler, router.system_handler) as store:
            store.put(state)
            retire_reply = ReplyCollector()
            await router.handle_text_message(
                "g", "p1", _noop, retire_reply, _noop, _noop, _noop,
                "/coc retire 小明",
            )
            kp_reply = ReplyCollector()
            await router.handle_text_message(
                "g", "p1", _noop, kp_reply, _noop, _noop, _noop,
                "/coc kp",
            )
            saved = store.get("g")

        self.assertIsNone(saved.get_active_character("p1"))
        self.assertEqual(saved.kp_assistant_user_id, "p1")
        self.assertIn("已退出角色", retire_reply.messages[0])
        self.assertIn("已登記", kp_reply.messages[0])

    async def test_kp_can_switch_retired_target_back_to_owned_history(self):
        state = self._state()
        current = state.get_active_character("p1")
        historical = Character(name="小華", owner_id="p1")
        state.characters_by_id[historical.character_id] = historical
        state.retire_active_character("p1", current.name)

        with StateStorePatch(router, commands, router.character_handler) as store:
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 switch 小華",
                allow_opaque_sudo_target=True,
            )
            saved = store.get("g")

        self.assertEqual(saved.get_active_character("p1").name, "小華")
        self.assertIn("目前使用角色已切換", reply.messages[0])
        self.assertIn("【KP Assistant 代操作：小華】", reply.messages[0])

    async def test_rejected_switch_marker_keeps_current_character(self):
        state = self._state()
        historical = Character(name="小華", owner_id="p1", character_id="p1:historical")
        state.characters_by_id[historical.character_id] = historical
        state.pending_pregen_luck["p1"] = historical.character_id

        with StateStorePatch(router, commands, router.character_handler) as store:
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 switch 小華",
                allow_opaque_sudo_target=True,
            )

        self.assertIn("【KP Assistant 代操作：小明】", reply.messages[0])
        self.assertNotIn("【KP Assistant 代操作：小華】", reply.messages[0])
        self.assertIn("尚未完成 LUCK", reply.messages[0])

    async def test_sudo_audit_events_use_redacted_actor_and_subject_ids(self):
        state = self._state()
        with StateStorePatch(router, commands, router.system_handler) as store, patch.object(
            router.observability, "event"
        ) as event:
            store.put(state)
            await router.handle_text_message(
                "g", "kp", _noop, ReplyCollector(), _noop, _noop, _noop,
                "/coc sudo p1 away",
                allow_opaque_sudo_target=True,
            )

        event_names = [call.args[0] for call in event.call_args_list]
        self.assertIn("sudo.started", event_names)
        self.assertIn("sudo.completed", event_names)
        completed = next(call.kwargs for call in event.call_args_list if call.args[0] == "sudo.completed")
        self.assertNotEqual(completed["actor_user_id_hash"], "kp")
        self.assertNotEqual(completed["subject_user_id_hash"], "p1")
        self.assertEqual(completed["status"], "success")

    async def test_sudo_completed_status_is_rejected_for_handler_guard(self):
        state = self._state()
        with StateStorePatch(router, commands) as store, patch.object(router.observability, "event") as event:
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 check",
                allow_opaque_sudo_target=True,
            )

        completed = next(call.kwargs for call in event.call_args_list if call.args[0] == "sudo.completed")
        self.assertEqual(completed["status"], "rejected")

    async def test_sudo_check_rejects_mismatched_pending_skill_without_dispatch(self):
        state = self._state()
        state.pending_checks["p1"] = {
            "type": "skill",
            "skill": "SpotHidden",
            "skill_value": 50,
            "bonus_dice": 0,
            "penalty_dice": 0,
        }

        async def unexpected_check(*args, **kwargs):
            self.fail("mismatched sudo check must not reach the roll handler")

        with StateStorePatch(router, commands) as store, patch.object(
            router, "handle_check_command", unexpected_check
        ):
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 check Fighting",
                allow_opaque_sudo_target=True,
            )
            saved = store.get("g")

        self.assertIn("目前等待中的檢定", reply.messages[0])
        self.assertEqual(saved.pending_checks["p1"]["skill"], "SpotHidden")

    async def test_sudo_check_dispatches_only_matching_pending_skill(self):
        state = self._state()
        state.pending_checks["p1"] = {
            "type": "skill",
            "skill": "SpotHidden",
            "skill_value": 50,
            "bonus_dice": 0,
            "penalty_dice": 0,
        }
        calls = []

        async def fake_check(*args, **kwargs):
            calls.append((args, kwargs))
            return True

        with StateStorePatch(router, commands) as store, patch.object(router, "handle_check_command", fake_check):
            store.put(state)
            reply = ReplyCollector()
            await router.handle_text_message(
                "g", "kp", _noop, reply, _noop, _noop, _noop,
                "/coc sudo p1 check SpotHidden",
                allow_opaque_sudo_target=True,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][-1], "/coc check SpotHidden")
