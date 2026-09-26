"""Acceptance tests for the dedicated player narrative correction command.

These tests intentionally define the command contract before its handler exists.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.commands import router


class NarrativeCorrectionCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_correct_is_a_known_coc_command(self):
        self.assertTrue(router.is_known_coc_command("correct"))
        self.assertTrue(router.is_known_coc_command("CORRECT"))

    async def test_report_is_acknowledged_without_gameplay_or_help(self):
        reply = AsyncMock()
        with (
            patch.object(router.supervisor, "run_turn", new_callable=AsyncMock) as run_turn,
            patch.object(router, "handle_check_command", new_callable=AsyncMock) as check,
            patch.object(router.system_handler, "handle_system_command", new_callable=AsyncMock) as system,
            patch.object(router.help_service, "get_page", side_effect=AssertionError("correct must not show generic help")),
        ):
            await router.handle_text_message(
                "correction-test", "player", AsyncMock(), reply,
                AsyncMock(), AsyncMock(), AsyncMock(),
                "/coc correct 12345 剛才說有地下室，但劇本沒有",
            )

        run_turn.assert_not_awaited()
        check.assert_not_awaited()
        system.assert_not_awaited()
        reply.assert_awaited_once()
        self.assertIn("待核對", reply.await_args.args[0])

    async def test_missing_report_details_get_command_specific_guidance(self):
        reply = AsyncMock()
        with (
            patch.object(router.supervisor, "run_turn", new_callable=AsyncMock) as run_turn,
            patch.object(router.help_service, "get_page", side_effect=AssertionError("correct must not show generic help")),
        ):
            await router.handle_text_message(
                "correction-test", "player", AsyncMock(), reply,
                AsyncMock(), AsyncMock(), AsyncMock(), "/coc correct",
            )

        run_turn.assert_not_awaited()
        reply.assert_awaited_once()
        self.assertIn("/coc correct", reply.await_args.args[0])
