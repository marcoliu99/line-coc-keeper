import unittest
from pathlib import Path

from app.help_docs import generate_markdown
from app.help_registry import HelpCategory, HelpContext, HelpEntry, get_help_page, register_help, register_help_category, reset_registry_for_tests
from app.help_service import bounded_page_text, parse_help_path, resolve_text_path
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
        self.assertEqual(resolve_text_path(state, "u1", ["check"]), ("check",))
        self.assertEqual(resolve_text_path(state, "u1", ["kp"]), ("kp",))

    def test_duplicate_and_deep_paths_are_rejected(self):
        # Force initialization, then verify the public registration validation.
        get_help_page()
        with self.assertRaises(ValueError):
            register_help(HelpEntry(("combat", "damage"), "combat", "duplicate", "duplicate", ("/coc combat damage",)))
        with self.assertRaises(ValueError):
            register_help(HelpEntry(("combat", "too", "deep"), "combat", "deep", "deep", ("/coc combat too deep",)))
        with self.assertRaises(ValueError):
            register_help(HelpEntry(("combat",), "combat", "short", "short", ("/coc combat",)))

    def test_help_path_parser_rejects_extra_depth_instead_of_truncating(self):
        self.assertEqual(parse_help_path(["combat", "damage", "extra"]), ("combat", "damage", "extra"))
        page = get_help_page(("combat", "damage", "extra"), HelpContext())
        self.assertIn("目前情境沒有這個 help 頁面", page.text)

    def test_direct_registration_is_idempotent(self):
        from app.help_registration import register_all_help

        register_all_help()
        register_all_help()
        self.assertEqual(get_help_page().path, ())

    def test_registry_rejects_invalid_path_tokens_and_visibility(self):
        register_help_category(HelpCategory("custom", "自訂"))
        with self.assertRaises(ValueError):
            register_help(HelpEntry(("custom", "含中文"), "custom", "bad", "bad", ("/coc bad",)))
        with self.assertRaises(ValueError):
            register_help(HelpEntry(("custom", "bad"), "custom", "bad", "bad", ("/coc bad",), visibility="unknown"))

    def test_main_v2_commands_are_documented(self):
        document = generate_markdown()
        for command in (
            "/coc characters",
            "/coc switch 角色名",
            "/coc scenario import 檔名.pdf",
            "/coc scenario merge 暫存ID1 暫存ID2 ...",
            "/coc checkpoint [名稱]",
            "/coc checkpoints",
            "/coc rollback 節點ID或唯一名稱",
            "/coc digest",
            "/coc digests",
        ):
            self.assertIn(command, document)

    def test_help_page_text_is_bounded_for_platform_renderers(self):
        page = get_help_page(("combat", "damage"), HelpContext())
        bounded = bounded_page_text(page, 40)
        self.assertLessEqual(len(bounded), 40)
        self.assertIn("內容過長", bounded)

    def test_scenario_permissions_are_classified_per_command(self):
        page = get_help_page(("scenario",), HelpContext())
        detail = get_help_page(("scenario", "list"), HelpContext())
        self.assertTrue(any(action.path == ("scenario", "use") for action in page.actions))
        self.assertNotIn("KP-only", detail.text)

    def test_player_reference_is_generated_from_registry(self):
        document = generate_markdown()
        self.assertIn("/coc pc 角色名 [職業]", document)
        self.assertIn("只有劇本沒有預設角色時顯示", document)
        self.assertIn("[KP-only]", document)
        reference = Path("docs/player_command_reference.md").read_text(encoding="utf-8")
        self.assertEqual(reference, document)
