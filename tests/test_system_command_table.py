"""``/coc`` system subcommands are a table of handlers, and every routed subcommand has one."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.commands import router
from app.commands.handlers import system
from app.models import GroupState

SOURCE = Path(system.__file__)


async def silent(*_args, **_kwargs):
    return None


class SystemCommandTableTests(unittest.IsolatedAsyncioTestCase):
    def test_every_subcommand_the_router_sends_here_has_a_handler(self):
        self.assertLessEqual(router._SYSTEM_COMMANDS, set(system._COMMANDS))

    def test_the_scenario_actions_the_router_treats_as_long_all_have_a_handler(self):
        # The router keeps these outside its conversation lock because their handlers lock around the short parts.
        self.assertLessEqual({"import", "merge", "reparse", "cancel", "use"}, set(system._SCENARIO_ACTIONS))

    def test_aliases_share_a_handler(self):
        self.assertIs(system._COMMANDS["checkpoint"], system._COMMANDS["checkpoints"])
        self.assertIs(system._COMMANDS["checkpoint"], system._COMMANDS["rollback"])
        self.assertIs(system._COMMANDS["digest"], system._COMMANDS["digests"])

    def test_the_entry_point_only_guards_and_dispatches(self):
        """It was 641 lines of ``if sub == ...``; a new subcommand is a handler and one table row."""
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        entry = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_system_command")
        self.assertLess(entry.end_lineno - entry.lineno, 45)

    async def test_an_unknown_subcommand_is_named(self):
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        await system.handle_system_command("c", "u", reply, silent, silent, silent, ["/coc", "nonsense"])
        self.assertEqual(replies, ["未知的系統指令：nonsense"])

    async def test_an_unknown_scenario_action_gets_the_usage_line(self):
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        with patch.object(system, "load_state", return_value=GroupState(group_id="c")):
            await system.handle_system_command("c", "u", reply, silent, silent, silent, ["/coc", "scenario", "nonsense"])
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0].startswith("用法：/coc scenario list"))

    async def test_dispatch_hands_the_handler_the_callers_arguments(self):
        seen: list[system._Call] = []

        async def handler(call):
            seen.append(call)

        reply = AsyncMock()
        with patch.dict(system._COMMANDS, {"status": handler}):
            await system.handle_system_command(
                "c", "u", reply, silent, silent, silent, ["/coc", "STATUS"], expected_revision=7)
        self.assertEqual(len(seen), 1)
        call = seen[0]
        self.assertEqual((call.conversation_id, call.user_id, call.sub, call.expected_revision), ("c", "u", "status", 7))
        self.assertIs(call.reply, reply)


if __name__ == "__main__":
    unittest.main()
