import unittest

from app.legacy_commands import HELP_TEXT


class HelpTextTests(unittest.TestCase):
    def test_help_lists_every_router_command_family(self):
        for command in (
            "/coc pc",
            "/coc characters",
            "/coc switch",
            "/coc check",
            "/coc luck",
            "/coc combat",
            "/coc pdf",
            "/coc scenario",
            "/coc era",
            "/coc checkpoint",
            "/coc digest",
            "/coc showpage",
            "/coc help",
        ):
            self.assertIn(command, HELP_TEXT)
