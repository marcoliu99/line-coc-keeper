"""Acceptance tests for the dedicated player narrative correction command.

These tests intentionally define the command contract before its handler exists.
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.commands import router
from app.models import GroupState


class NarrativeCorrectionCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = GroupState(group_id="correction-test")
        load_patch = patch.object(router.correct_handler, "load_state", return_value=self.state)
        save_patch = patch.object(router.correct_handler, "save_state", new_callable=Mock)
        load_patch.start()
        self.save_state = save_patch.start()
        self.addCleanup(load_patch.stop)
        self.addCleanup(save_patch.stop)

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
        self.save_state.assert_called_once()
        self.assertEqual(self.state.narrative_corrections[0]["status"], "pending")
        self.assertEqual(self.state.log, [])

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
        self.save_state.assert_not_called()

    async def test_reply_reference_can_target_message_without_repeating_id(self):
        reply = AsyncMock()
        await router.handle_text_message(
            "correction-test", "player", AsyncMock(), reply,
            AsyncMock(), AsyncMock(), AsyncMock(),
            "/coc correct 劇本沒有地下室",
            referenced_message_id="9876543210",
        )

        self.assertEqual(self.state.narrative_corrections[0]["target_message_id"], "9876543210")
        self.assertEqual(self.state.narrative_corrections[0]["issue"], "劇本沒有地下室")

    async def test_duplicate_report_is_idempotent(self):
        reply = AsyncMock()
        for _ in range(2):
            await router.handle_text_message(
                "correction-test", "player", AsyncMock(), reply,
                AsyncMock(), AsyncMock(), AsyncMock(),
                "/coc correct 9876543210 劇本沒有地下室",
            )

        self.assertEqual(len(self.state.narrative_corrections), 1)
        self.save_state.assert_called_once()

    async def test_pending_reports_are_capped_per_player(self):
        reply = AsyncMock()
        for index in range(4):
            await router.handle_text_message(
                "correction-test", "player", AsyncMock(), reply,
                AsyncMock(), AsyncMock(), AsyncMock(),
                f"/coc correct 9876543210 疑點{index}",
            )
        self.assertEqual(len(self.state.narrative_corrections), 3)
        self.assertIn("已達上限", reply.await_args.args[0])

    async def test_closed_reports_are_pruned_but_recent_approved_remains(self):
        from app.commands.handlers import correct
        self.state.narrative_corrections = [
            {"id": str(index), "status": "rejected"} for index in range(30)
        ] + [
            {"id": f"a{index}", "status": "approved"} for index in range(40)
        ]
        correct._prune_adjudicated(self.state)
        self.assertEqual(len(self.state.narrative_corrections), 36)
        self.assertEqual(self.state.narrative_corrections[-1]["id"], "a39")

    async def test_only_kp_can_approve_and_approval_survives_serialization(self):
        reply = AsyncMock()
        self.state.kp_assistant_user_id = "kp"
        self.state.openai_previous_response_id = "stale-response"
        self.state.openai_previous_response_timeline_id = "old-timeline"
        await router.handle_text_message(
            "correction-test", "player", AsyncMock(), reply,
            AsyncMock(), AsyncMock(), AsyncMock(),
            "/coc correct 9876543210 劇本沒有地下室",
        )
        report_id = self.state.narrative_corrections[0]["id"]

        await router.handle_text_message(
            "correction-test", "player", AsyncMock(), reply,
            AsyncMock(), AsyncMock(), AsyncMock(),
            f"/coc correct approve {report_id} 先前提到的地下室不存在",
        )
        self.assertEqual(self.state.narrative_corrections[0]["status"], "pending")

        await router.handle_text_message(
            "correction-test", "kp", AsyncMock(), reply,
            AsyncMock(), AsyncMock(), AsyncMock(),
            f"/coc correct approve {report_id} 先前提到的地下室不存在",
        )
        restored = GroupState.from_dict(self.state.to_dict())
        self.assertEqual(restored.narrative_corrections[0]["status"], "approved")
        self.assertEqual(restored.narrative_corrections[0]["resolution"], "先前提到的地下室不存在")
        self.assertIn("敘事更正", restored.log[-1]["content"])
        self.assertEqual(restored.openai_previous_response_id, "")
