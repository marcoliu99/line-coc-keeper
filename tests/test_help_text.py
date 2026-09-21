import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.commands import router
from app.commands.router import is_known_coc_command
from app.help_registry import HelpContext, get_help_page, reset_registry_for_tests


class HelpTextTests(unittest.TestCase):
    def tearDown(self):
        reset_registry_for_tests()

    def test_help_lists_every_router_command_family(self):
        root = get_help_page(context=HelpContext())
        categories = {action.path[0] for action in root.actions if action.path}
        self.assertEqual(categories, {"character", "check", "combat", "map", "scenario", "kp", "other"})

        scenario = get_help_page(("scenario",), HelpContext())
        details = [get_help_page(action.path, HelpContext()).text for action in scenario.actions if len(action.path) == 2]
        for command in ("/coc scenario list", "/coc scenario use 劇本ID", "/coc scenario reparse", "/coc scenario cancel"):
            self.assertTrue(any(command in text for text in details))

    def test_router_command_families_have_known_command_gate(self):
        for command in ("characters", "switch", "scenario", "checkpoint", "checkpoints", "rollback", "digest", "digests", "sudo"):
            self.assertTrue(is_known_coc_command(command))
        self.assertTrue(is_known_coc_command("CHECKPOINT"))
        self.assertFalse(is_known_coc_command("typo"))

    def test_checkpoint_commands_do_not_match_check_prefix(self):
        async def run(command: str) -> tuple[AsyncMock, AsyncMock]:
            check = AsyncMock()
            system = AsyncMock()
            with patch.object(router, "handle_check_command", check), patch.object(
                router.system_handler, "handle_system_command", system
            ):
                await router.handle_text_message(
                    "router-help-test",
                    "u1",
                    AsyncMock(),
                    AsyncMock(),
                    AsyncMock(),
                    AsyncMock(),
                    AsyncMock(),
                    f"/coc {command}",
                )
            return check, system

        for command in ("checkpoint", "checkpoints"):
            check, system = asyncio.run(run(command))
            check.assert_not_awaited()
            system.assert_awaited_once()
            self.assertEqual(system.call_args.args[6], ["/coc", command])

    def test_router_normalizes_top_level_command_token(self):
        async def run() -> AsyncMock:
            character = AsyncMock()
            with patch.object(router.character_handler, "handle_character_command", character):
                await router.handle_text_message(
                    "router-help-case-test",
                    "u1",
                    AsyncMock(),
                    AsyncMock(),
                    AsyncMock(),
                    AsyncMock(),
                    AsyncMock(),
                    "/coc CHARACTERS",
                )
            return character

        character = asyncio.run(run())
        character.assert_awaited_once()
        self.assertEqual(character.call_args.args[4], ["/coc", "characters"])
