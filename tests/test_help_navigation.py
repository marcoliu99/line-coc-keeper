import unittest
from pathlib import Path

from app.help_docs import generate_markdown
from app.help_registry import HelpContext, HelpEntry, get_help_page, register_help, reset_registry_for_tests
from app.help_service import resolve_text_path
from app.models import GroupState


class HelpNavigationTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()

    def tearDown(self):
        reset_registry_for_tests()

    def test_root_and_category_pages_are_three_level_navigation(self):
        root = get_help_page(context=HelpContext())
        self.assertEqual(root.path, ())
        self.assertTrue(any(action.path == ("character",) for action in root.actions))

        category = get_help_page(("character",), HelpContext(pregens_exist=False))
        self.assertTrue(any(action.path == ("character", "pc") for action in category.actions))
        self.assertFalse(any(action.path == ("character", "usepregen") for action in category.actions))

        detail = get_help_page(("character", "pc"), HelpContext(pregens_exist=False))
        self.assertIn("/coc pc 角色名 [職業]", detail.text)
        self.assertEqual(detail.actions[0].path, ("character",))

    def test_pregen_context_hides_custom_creation_and_shows_pregen_commands(self):
        page = get_help_page(("character",), HelpContext(pregens_exist=True))
        paths = {action.path for action in page.actions}
        self.assertNotIn(("character", "pc"), paths)
        self.assertNotIn(("character", "create"), paths)
        self.assertIn(("character", "pregen"), paths)
        self.assertIn(("character", "usepregen"), paths)

    def test_kp_only_entry_remains_visible_and_is_marked(self):
        page = get_help_page(("scenario",), HelpContext())
        use_action = next(action for action in page.actions if action.path == ("scenario", "use"))
        detail = get_help_page(use_action.path, HelpContext())
        self.assertIn("KP-only", page.text)
        self.assertIn("KP-only", detail.text)

    def test_manual_short_path_resolves_to_canonical_entry(self):
        state = GroupState(group_id="g")
        self.assertEqual(resolve_text_path(state, "u1", ["pc"]), ("character", "pc"))
        self.assertEqual(resolve_text_path(state, "u1", ["combat", "damage"]), ("combat", "damage"))

    def test_duplicate_and_deep_paths_are_rejected(self):
        # Force initialization, then verify the public registration validation.
        get_help_page()
        with self.assertRaises(ValueError):
            register_help(HelpEntry(("combat", "damage"), "combat", "duplicate", "duplicate", ("/coc combat damage",)))
        with self.assertRaises(ValueError):
            register_help(HelpEntry(("combat", "too", "deep"), "combat", "deep", "deep", ("/coc combat too deep",)))

    def test_player_reference_is_generated_from_registry(self):
        document = generate_markdown()
        self.assertIn("/coc pc 角色名 [職業]", document)
        self.assertIn("只有劇本沒有預設角色時顯示", document)
        self.assertIn("[KP-only]", document)
        reference = Path("docs/player_command_reference.md").read_text(encoding="utf-8")
        self.assertEqual(reference, document)
