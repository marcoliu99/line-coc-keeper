import unittest

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
