"""docs/specs/bug/keeper_role_superuser_design_spec.md.

KP authority comes only from being the group's registered KP Assistant; no
Discord role grants it. /coc kp transfer and takeover replace the role as the
way to fill a missing KP seat.
"""
import ast
import asyncio
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import discord_bot
from app.commands import permissions, router
from app.commands.handlers import system as system_handler
from app.models import Character, GroupState

KP_ONLY = "只有目前的 KP 助手"


def _state(**kwargs) -> GroupState:
    return GroupState(group_id="g", **kwargs)


def _with_investigator(state: GroupState, user_id: str) -> GroupState:
    char = Character(name=f"inv-{user_id}", owner_id=user_id, character_id=f"char-{user_id}")
    state.characters[user_id] = char
    state.characters_by_id[char.character_id] = char
    state.active_character_id_by_user[user_id] = char.character_id
    return state


def _run(state: GroupState, user_id: str, text: str, **kwargs) -> tuple[list[str], MagicMock]:
    replies: list[str] = []

    async def reply(message):
        replies.append(message)

    with patch.object(system_handler, "load_state", return_value=state), \
            patch.object(system_handler, "save_state") as save:
        asyncio.run(system_handler.handle_system_command(
            "g", user_id, reply, AsyncMock(), AsyncMock(), AsyncMock(), text.split(),
            lambda owner_id: f"<@{owner_id}>", **kwargs,
        ))
    return replies, save


class PermissionTests(unittest.TestCase):
    def test_only_the_registered_kp_assistant_is_kp(self):
        state = _state(kp_assistant_user_id="kp")
        self.assertTrue(permissions.is_kp(state, "kp"))
        self.assertFalse(permissions.is_kp(state, "player"))
        self.assertFalse(permissions.is_kp(_state(), ""))

    def test_lifecycle_is_open_unless_kp_only(self):
        state = _state(kp_assistant_user_id="kp")
        with patch.object(permissions.config, "SCENARIO_LIFECYCLE_KP_ONLY", False):
            self.assertTrue(permissions.may_manage_scenario_lifecycle(state, "player"))
        with patch.object(permissions.config, "SCENARIO_LIFECYCLE_KP_ONLY", True):
            self.assertFalse(permissions.may_manage_scenario_lifecycle(state, "player"))
            self.assertTrue(permissions.may_manage_scenario_lifecycle(state, "kp"))

    def test_mentions(self):
        self.assertEqual(permissions.mentioned_user_id("<@123>"), "123")
        self.assertEqual(permissions.mentioned_user_id("<@!123>"), "123")
        self.assertIsNone(permissions.mentioned_user_id("123"))


class KpOnlyActionsTests(unittest.TestCase):
    """Every always-checked action refuses a member who isn't the KP Assistant,
    even one with Manage Server (that permission is only for takeover)."""

    ALWAYS = (
        "/coc checkpoints", "/coc checkpoint 名稱", "/coc rollback x", "/coc digest", "/coc digests",
        "/coc scenario source status sid", "/coc scenario template export sample",
        "/coc scenario cards list sid", "/coc scenario use sid",
    )
    LIFECYCLE = ("/coc scenario reparse", "/coc scenario cancel", "/coc scenario clean", "/coc pdf new")

    def test_always_checked_actions_refuse_a_non_kp(self):
        for text in self.ALWAYS:
            with self.subTest(text=text):
                replies, save = _run(_state(kp_assistant_user_id="kp"), "manager", text, can_manage_server=True)
                self.assertTrue(replies and KP_ONLY in replies[0], replies)
                save.assert_not_called()

    def test_lifecycle_actions_refuse_a_non_kp_when_kp_only(self):
        with patch.object(permissions.config, "SCENARIO_LIFECYCLE_KP_ONLY", True):
            for text in self.LIFECYCLE:
                with self.subTest(text=text):
                    replies, save = _run(_state(kp_assistant_user_id="kp"), "manager", text, can_manage_server=True)
                    self.assertTrue(replies and KP_ONLY in replies[0], replies)
                    save.assert_not_called()

    def test_sudo_refuses_a_non_kp_even_with_manage_server(self):
        reply = AsyncMock()
        with patch.object(router, "load_state", return_value=_state(kp_assistant_user_id="kp")):
            asyncio.run(router.handle_text_message(
                "g", "manager", AsyncMock(), reply, AsyncMock(), AsyncMock(), AsyncMock(),
                "/coc sudo <@444> act 開門", can_manage_server=True,
            ))
        self.assertIn(KP_ONLY, reply.await_args.args[0])

    def test_the_old_keyword_is_gone(self):
        with self.assertRaises(TypeError):
            asyncio.run(router.handle_text_message(
                "g", "u", AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), "/coc status",
                is_keeper=True,
            ))


class TransferTests(unittest.TestCase):
    def test_only_the_kp_can_transfer(self):
        state = _state(kp_assistant_user_id="kp")
        replies, save = _run(state, "player", "/coc kp transfer <@222>")
        self.assertIn(KP_ONLY, replies[0])
        self.assertEqual(state.kp_assistant_user_id, "kp")
        save.assert_not_called()

    def test_transfer_needs_a_target(self):
        replies, _ = _run(_state(kp_assistant_user_id="kp"), "kp", "/coc kp transfer")
        self.assertIn("用法", replies[0])

    def test_transfer_respects_the_seat_rules(self):
        cases = {
            "investigator": (_with_investigator(_state(kp_assistant_user_id="kp"), "222"), frozenset(), "調查員"),
            "bot": (_state(kp_assistant_user_id="kp"), frozenset({"222"}), "機器人"),
        }
        for name, (state, bots, expected) in cases.items():
            with self.subTest(case=name):
                replies, save = _run(state, "kp", "/coc kp transfer <@222>", bot_user_ids=bots)
                self.assertIn(expected, replies[0])
                self.assertEqual(state.kp_assistant_user_id, "kp")
                save.assert_not_called()

    def test_transfer_hands_over_and_announces(self):
        state = _state(kp_assistant_user_id="kp", kp_ooc_log=[{"role": "kp_assistant", "content": "x"}])
        with patch.object(system_handler.observability, "event") as event:
            replies, save = _run(state, "kp", "/coc kp transfer <@222>")
        self.assertEqual(state.kp_assistant_user_id, "222")
        self.assertEqual(state.kp_ooc_log, [])
        save.assert_called_once_with(state)
        self.assertIn("<@kp> 已將 KP 助手交接給 <@222>", replies[0])
        self.assertEqual(event.call_args.args[0], "kp.transfer")


class TakeoverTests(unittest.TestCase):
    def test_takeover_needs_manage_server(self):
        state = _state(kp_assistant_user_id="gone")
        replies, save = _run(state, "member", "/coc kp takeover")
        self.assertIn("管理伺服器", replies[0])
        self.assertEqual(state.kp_assistant_user_id, "gone")
        save.assert_not_called()

    def test_a_manager_takes_the_seat_and_it_is_announced(self):
        state = _state(kp_assistant_user_id="gone")
        with patch.object(system_handler.observability, "event") as event:
            replies, save = _run(state, "manager", "/coc kp takeover", can_manage_server=True)
        self.assertEqual(state.kp_assistant_user_id, "manager")
        save.assert_called_once_with(state)
        self.assertIn("<@manager> 已接手成為這局的 KP 助手", replies[0])
        self.assertIn("<@gone>", replies[0])
        self.assertEqual(event.call_args.args[0], "kp.takeover")

    def test_a_playing_manager_is_told_to_appoint_instead(self):
        state = _with_investigator(_state(kp_assistant_user_id="gone"), "manager")
        replies, save = _run(state, "manager", "/coc kp takeover", can_manage_server=True)
        self.assertIn("/coc kp takeover @成員", replies[0])
        self.assertEqual(state.kp_assistant_user_id, "gone")
        save.assert_not_called()

    def test_a_playing_manager_can_appoint_someone_eligible(self):
        state = _with_investigator(_state(kp_assistant_user_id="gone"), "manager")
        replies, _ = _run(state, "manager", "/coc kp takeover <@333>", can_manage_server=True)
        self.assertEqual(state.kp_assistant_user_id, "333")
        self.assertIn("<@manager> 指派 <@333> 擔任這局的 KP 助手", replies[0])

    def test_appointing_an_ineligible_member_is_refused(self):
        creating = _state(kp_assistant_user_id="gone")
        creating.creation_sessions["333"] = {}
        cases = {
            "investigator": (_with_investigator(_state(kp_assistant_user_id="gone"), "333"), frozenset()),
            "creation": (creating, frozenset()),
            "bot": (_state(kp_assistant_user_id="gone"), frozenset({"333"})),
        }
        for name, (state, bots) in cases.items():
            with self.subTest(case=name):
                _, save = _run(state, "manager", "/coc kp takeover <@333>", can_manage_server=True, bot_user_ids=bots)
                self.assertEqual(state.kp_assistant_user_id, "gone")
                save.assert_not_called()


class TransportTests(unittest.TestCase):
    def _member(self, *, roles=(), bot=False, manage_guild=False):
        return SimpleNamespace(
            id=7, bot=bot, roles=[SimpleNamespace(name=r) for r in roles],
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
        )

    def test_manage_server_is_read_from_discord_permissions(self):
        self.assertTrue(discord_bot._can_manage_server(self._member(manage_guild=True)))
        self.assertFalse(discord_bot._can_manage_server(self._member(roles=("keeper",))))
        self.assertFalse(discord_bot._can_manage_server(SimpleNamespace(id=7)))  # a DM user has no server permissions

    def test_transition_log_fires_only_for_a_human_with_the_role(self):
        with patch.object(discord_bot.observability, "event") as event:
            discord_bot._note_ignored_keeper_role(self._member(roles=("Keeper",)), "sudo")
            discord_bot._note_ignored_keeper_role(self._member(roles=("keeper",), bot=True), "sudo")
            discord_bot._note_ignored_keeper_role(self._member(roles=("player",)), "sudo")
        self.assertEqual([c.args[0] for c in event.call_args_list], ["authz.keeper_role_ignored"])
        self.assertEqual(event.call_args.kwargs["action"], "sudo")


class NoRoleAuthorityTests(unittest.TestCase):
    def test_no_app_module_carries_the_old_keeper_authority(self):
        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        banned = {"is_keeper", "actor_is_keeper", "_is_keeper_member", "_is_kp_or_keeper"}
        found = []
        for path in app_dir.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                name = getattr(node, "id", None) or getattr(node, "attr", None) or getattr(node, "arg", None)
                if name in banned:
                    found.append(f"{path.name}:{getattr(node, 'lineno', '?')}:{name}")
        self.assertEqual(found, [])

    def test_a_role_name_appears_only_in_the_transition_log(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "discord_bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        holders = [
            fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
            and any(isinstance(n, ast.Constant) and n.value == "keeper" for n in ast.walk(fn))
        ]
        self.assertEqual(holders, ["_note_ignored_keeper_role"])


if __name__ == "__main__":
    unittest.main()
