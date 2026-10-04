"""Step 2 of docs/specs/refactor/discord_events_through_router_design_spec.md.

Attachments enter through the router; app/commands/handlers/uploads.py owns
which handler each file goes to, and staging a multi-part PDF respects the
admission hold.
"""
import unittest
from unittest.mock import AsyncMock, patch

from app import legacy_commands
from app.commands import router
from app.commands.handlers import uploads
from app.commands.handlers.uploads import Upload
from app.models import GroupState
from app.services import mutation_admission


def _upload(name: str, content: bytes = b"data") -> Upload:
    return Upload(name, AsyncMock(return_value=content))


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def _route(self, *names: str):
        reply, buttons = AsyncMock(), AsyncMock()
        handlers = {name: AsyncMock() for name in (
            "handle_pdf_upload", "handle_map_upload", "handle_role_sheet_upload", "handle_scenario_compare_upload",
            "handle_scenario_markdown_upload",
        )}
        stage = AsyncMock()
        with patch.multiple(uploads, **handlers), patch.object(uploads, "_stage_pdf_parts", stage):
            handled = await uploads.handle_uploads("g", [_upload(n) for n in names], reply, post_pdf_buttons=buttons)
        return handled, handlers, stage, reply, buttons

    async def test_a_single_pdf_is_parsed_and_gets_its_buttons(self):
        handled, handlers, stage, _, buttons = await self._route("scenario.pdf")
        self.assertTrue(handled)
        handlers["handle_pdf_upload"].assert_awaited_once()
        self.assertEqual(handlers["handle_pdf_upload"].await_args.args[3:], (b"data", "scenario.pdf"))
        buttons.assert_awaited_once()
        stage.assert_not_awaited()

    async def test_scenario_markdown_is_ingested_and_gets_its_buttons(self):
        handled, handlers, stage, _, buttons = await self._route("scenario_the_haunting.md")
        self.assertTrue(handled)
        handlers["handle_scenario_markdown_upload"].assert_awaited_once()
        self.assertEqual(
            handlers["handle_scenario_markdown_upload"].await_args.args[3:],
            (b"data", "scenario_the_haunting.md"),
        )
        handlers["handle_scenario_compare_upload"].assert_not_awaited()
        buttons.assert_awaited_once()
        stage.assert_not_awaited()

    async def test_markdown_scenario_helpers_recognize_page_markers_and_title(self):
        self.assertIsNotNone(
            legacy_commands._MARKDOWN_PAGE_MARKER_RE.search("--- 第 7 頁 ---\n內容")
        )
        self.assertEqual(
            legacy_commands._markdown_scenario_title("# Ignored", "scenario_The_Haunting.md"),
            "The Haunting",
        )
        self.assertEqual(
            legacy_commands._markdown_scenario_title("# The Haunting\nBody", "scenario.md"),
            "The Haunting",
        )

    async def test_several_pdfs_or_a_part_name_are_staged(self):
        for names in (("a.pdf", "b.pdf"), ("scenario_part1.pdf",)):
            with self.subTest(names=names):
                _, handlers, stage, _, _ = await self._route(*names)
                stage.assert_awaited_once()
                handlers["handle_pdf_upload"].assert_not_awaited()

    async def test_each_kind_goes_to_its_handler(self):
        cases = {
            "map_lighthouse.yaml": "handle_map_upload",
            "role_ken.txt": "handle_role_sheet_upload",
            "scenario_lightless_beacon.md": "handle_scenario_markdown_upload",
            "alt_extraction.md": "handle_scenario_compare_upload",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                handled, handlers, _, _, _ = await self._route(name)
                self.assertTrue(handled)
                for handler_name, handler in handlers.items():
                    self.assertEqual(handler.await_count, 1 if handler_name == expected else 0)

    async def test_every_role_card_in_a_message_is_handled(self):
        _, handlers, _, _, _ = await self._route("role_a.txt", "role_b.txt")
        self.assertEqual(handlers["handle_role_sheet_upload"].await_count, 2)

    async def test_an_unprefixed_yaml_gets_a_rename_hint(self):
        handled, handlers, _, reply, _ = await self._route("lighthouse.yaml")
        self.assertTrue(handled)
        self.assertIn("map_ 開頭", reply.await_args.args[0])
        self.assertFalse(any(h.await_count for h in handlers.values()))

    async def test_an_unrecognised_attachment_is_left_to_the_caller(self):
        handled, handlers, _, _, _ = await self._route("photo.png")
        self.assertFalse(handled)
        self.assertFalse(any(h.await_count for h in handlers.values()))


class StagingTests(unittest.IsolatedAsyncioTestCase):
    """Audit finding 2: a held group gets the hold notice and no orphaned files."""

    async def test_a_held_group_stages_nothing(self):
        reply = AsyncMock()
        with patch.object(uploads.mutation_admission, "is_held", return_value=True), \
                patch.object(uploads.scenario_library, "stage_upload") as stage, \
                patch.object(uploads, "save_state") as save:
            await uploads._stage_pdf_parts("g", [_upload("a.pdf"), _upload("b.pdf")], reply)
        reply.assert_awaited_once_with(mutation_admission.NOTICE)
        stage.assert_not_called()
        save.assert_not_called()

    async def test_a_hold_that_starts_mid_staging_keeps_content_addressed_files(self):
        state = GroupState(group_id="g")
        state.staged_pdf_parts = [{"key": "old", "file_name": "kept.pdf"}]
        reply = AsyncMock()
        keys = iter(["old", "new"])
        with patch.object(uploads.mutation_admission, "is_held", return_value=False), \
                patch.object(uploads.scenario_library, "stage_upload", side_effect=lambda _b: next(keys)), \
                patch.object(uploads.scenario_library, "discard_staged_upload") as discard, \
                patch.object(uploads, "load_state", return_value=state), \
                patch.object(uploads, "save_state", side_effect=mutation_admission.MutationHeld("held")):
            await uploads._stage_pdf_parts("g", [_upload("kept.pdf"), _upload("b.pdf")], reply)
        reply.assert_awaited_once_with(mutation_admission.NOTICE)
        discard.assert_not_called()

    async def test_staged_parts_are_recorded_and_listed(self):
        state = GroupState(group_id="g")
        reply = AsyncMock()
        keys = iter(["a" * 64, "b" * 64])
        with patch.object(uploads.mutation_admission, "is_held", return_value=False), \
                patch.object(uploads.scenario_library, "stage_upload", side_effect=lambda _b: next(keys)), \
                patch.object(uploads, "load_state", return_value=state), \
                patch.object(uploads, "save_state") as save:
            await uploads._stage_pdf_parts("g", [_upload("a.pdf"), _upload("b.pdf")], reply)
        save.assert_called_once_with(state)
        self.assertEqual([p["file_name"] for p in state.staged_pdf_parts], ["a.pdf", "b.pdf"])
        self.assertIn("/coc scenario merge", reply.await_args.args[0])


class RouterEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_attachments_is_not_handled(self):
        self.assertFalse(await router.handle_uploads("g", [], AsyncMock(), post_pdf_buttons=AsyncMock()))

    async def test_uploads_enter_through_the_router(self):
        with patch.object(router.uploads_handler, "handle_uploads", AsyncMock(return_value=True)) as handle, \
                patch.object(router.observability, "event") as event:
            handled = await router.handle_uploads("g", [_upload("a.pdf")], AsyncMock(), post_pdf_buttons=AsyncMock())
        self.assertTrue(handled)
        handle.assert_awaited_once()
        event.assert_any_call("turn.entry", entry="upload")


if __name__ == "__main__":
    unittest.main()
