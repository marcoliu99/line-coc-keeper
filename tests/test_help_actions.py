"""Executable Discord Help plans and their temporary controls."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import help_actions
from app.help_registration import _entries
from app.models import GroupState


class HelpActionPlanTests(unittest.TestCase):
    def test_every_registered_entry_has_a_unique_executable_plan(self):
        paths = {entry.path for entry in _entries()}
        self.assertEqual(len(paths), 52)
        help_actions.validate_coverage(paths)
        self.assertEqual(len(help_actions.BY_KEY), len(help_actions.ACTIONS))
        self.assertTrue(all(action.command and action.key for action in help_actions.ACTIONS))

    def test_character_forms_and_selected_scenario_build_existing_commands(self):
        pc = help_actions.BY_KEY["pc"]
        self.assertEqual(help_actions.build_command(pc, field_values=("小明", "記者")), "/coc pc 小明 記者")
        self.assertEqual(help_actions.build_command(pc, field_values=("小明", "")), "/coc pc 小明")
        with self.assertRaisesRegex(ValueError, "不能包含空白"):
            help_actions.build_command(pc, field_values=("小 明", "記者"))
        self.assertEqual(
            help_actions.build_command(help_actions.BY_KEY["scenario_use"], "scenario-123"),
            "/coc scenario use scenario-123",
        )

    def test_pending_plain_check_still_dispatches_without_skill_argument(self):
        state = GroupState(group_id="g")
        state.pending_checks["u"] = {"skill": "Spot Hidden", "type": "skill"}
        self.assertEqual(help_actions.options_for("pending_check", state, "u"), [("Spot Hidden", "")])
        self.assertEqual(help_actions.build_command(help_actions.BY_KEY["check"]), "/coc check")

    def test_scenario_card_retains_complete_ids(self):
        state = GroupState(group_id="g")
        with patch("app.scenario_library.list_scenarios", return_value=[{"id": "s" * 80, "title": "Long"}]), \
             patch("app.repositories.manual_pregens.list_assets", return_value=[
                 {"asset_id": "a" * 80, "pregen": {"name": "Test"}}
             ]):
            options = help_actions.options_for("scenario_card", state, "u")
        self.assertEqual(options[0][1], f"{'s' * 80} {'a' * 80}")


class HelpDiscordControlTests(unittest.TestCase):
    def test_detail_view_adds_executable_button_with_persistent_id(self):
        from app.discord_bot import HelpExecuteButton, _help_view
        from app.help_registry import HelpPage

        page = HelpPage(("character", "pc"), "角色", "文字", ())
        view = _help_view("discord-channel-123", page)
        buttons = [item for item in view.children if isinstance(item, HelpExecuteButton)]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].item.custom_id, "coc_help_run:discord-channel-123:pc")
        self.assertLessEqual(len(buttons[0].item.custom_id), 100)

    def test_select_pages_at_25_and_preserves_long_option_values(self):
        from app.discord_bot import HelpOptionSelect, HelpSelectView

        options = [(f"劇本 {i}", "s" * 120 + str(i)) for i in range(26)]
        view = HelpSelectView(help_actions.BY_KEY["scenario_use"], "u", "discord-channel-123", 1, options)
        select = next(item for item in view.children if isinstance(item, HelpOptionSelect))
        self.assertEqual(len(select.options), 25)
        self.assertEqual(select.options[0].value, "0")
        self.assertTrue(view.next.disabled is False)
        last_page = HelpSelectView(help_actions.BY_KEY["scenario_use"], "u", "discord-channel-123", 1, options, 1)
        self.assertTrue(last_page.next.disabled)

    def test_merge_requires_two_parts_and_keeps_selection_order(self):
        from app.discord_bot import HelpMergeView

        action = help_actions.BY_KEY["scenario_merge"]
        parts = [("first", "one.pdf"), ("second", "two.pdf")]
        initial = HelpMergeView(action, "u", "discord-channel-123", 1, parts)
        self.assertTrue(initial.finish.disabled)
        selected = HelpMergeView(action, "u", "discord-channel-123", 1, parts, ("second", "first"))
        self.assertFalse(selected.finish.disabled)
        self.assertEqual(help_actions.build_command(action, " ".join(selected.selected)),
                         "/coc scenario merge second first")

    def test_sudo_picker_contains_only_policy_allowed_commands(self):
        from app.commands import sudo
        from app.discord_bot import _HELP_SUDO_COMMANDS

        self.assertTrue(all(sudo.is_allowed_command(command.split()[0]) for command in _HELP_SUDO_COMMANDS))
        self.assertNotIn("luck roll", _HELP_SUDO_COMMANDS)
        self.assertNotIn("usepregen", _HELP_SUDO_COMMANDS)

    def test_execute_button_rejects_wrong_channel(self):
        from app.discord_bot import HelpExecuteButton

        button = HelpExecuteButton("discord-channel-123", help_actions.BY_KEY["pc"])
        interaction = SimpleNamespace(
            channel=SimpleNamespace(id=456), user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        asyncio.run(button.callback(interaction))
        interaction.response.send_message.assert_awaited_once_with(
            "這個 Help 操作不屬於目前頻道。", ephemeral=True,
        )

    def test_help_dispatch_uses_command_router_with_clicker_identity_and_revision(self):
        from app.discord_bot import _dispatch_help_command

        state = GroupState(group_id="discord-channel-123")
        state.state_revision = 7
        interaction = SimpleNamespace(
            channel=SimpleNamespace(id=123, send=AsyncMock()),
            user=SimpleNamespace(id=42, display_name="Player", roles=[]),
            response=SimpleNamespace(defer=AsyncMock()),
        )
        with patch("app.discord_bot.load_group_state", return_value=state), \
             patch("app.discord_bot.help_service.get_page", return_value=SimpleNamespace(title="角色")), \
             patch("app.discord_bot.command_router.handle_text_message", new_callable=AsyncMock) as route, \
             patch("app.discord_bot._post_pending_buttons", new_callable=AsyncMock):
            asyncio.run(_dispatch_help_command(
                interaction, help_actions.BY_KEY["pc"], "/coc pc 小明", 7,
            ))
        interaction.response.defer.assert_awaited_once()
        self.assertEqual(route.await_args.args[0:2], ("discord-channel-123", "42"))
        self.assertEqual(route.await_args.args[7], "/coc pc 小明")
        self.assertEqual(route.await_args.kwargs["expected_revision"], 7)

    def test_long_pdf_import_rejects_changed_help_revision_before_extraction(self):
        from app.legacy_commands import handle_pdf_upload

        state = GroupState(group_id="g")
        state.state_revision = 8
        reply = AsyncMock()
        with patch("app.legacy_commands.load_state", return_value=state), \
             patch("app.legacy_commands.pdf_loader.extract_preview") as extract:
            accepted = asyncio.run(handle_pdf_upload(
                "g", reply, reply, b"fake", "book.pdf", expected_revision=7,
            ))
        self.assertFalse(accepted)
        extract.assert_not_called()
        reply.assert_awaited_once()
