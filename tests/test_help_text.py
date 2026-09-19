import unittest

from app.help_registry import HelpContext, get_help_page, reset_registry_for_tests
from app.commands.router import is_known_coc_command


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
        for command in ("characters", "switch", "scenario", "checkpoint", "checkpoints", "rollback", "digest", "digests"):
            self.assertTrue(is_known_coc_command(command))
        self.assertTrue(is_known_coc_command("CHECKPOINT"))
        self.assertFalse(is_known_coc_command("typo"))
