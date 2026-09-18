import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import scenario_library


class ScenarioLibraryContextTests(unittest.TestCase):
    def _write_scenario(self, root: Path) -> None:
        scenario = root / "test-scenario"
        images = scenario / "images"
        images.mkdir(parents=True)
        manifest = {
            "id": "test-scenario",
            "title": "Test Scenario",
            "chapters": [
                {"id": "chapter-01", "title": "One", "kind": "playable", "start_page": 1, "end_page": 2},
                {"id": "chapter-02", "title": "Two", "kind": "playable", "start_page": 3, "end_page": 4},
                {"id": "chapter-03", "title": "Three", "kind": "playable", "start_page": 5, "end_page": 5},
            ],
            "image_assets": [
                {"id": "page-2-map", "page": 2, "type": "map", "chapter_id": "chapter-01", "tags": ["map"], "description": "first map"},
                {"id": "page-5-portrait", "page": 5, "type": "portrait", "chapter_id": "chapter-03", "tags": ["portrait"], "description": "future portrait"},
            ],
        }
        (scenario / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (scenario / "scenario.txt").write_text(
            "--- 第 1 頁 ---\none\n--- 第 2 頁 ---\ntwo\n--- 第 3 頁 ---\nthree\n"
            "--- 第 4 頁 ---\nfour\n--- 第 5 頁 ---\nfive\n", encoding="utf-8"
        )
        (scenario / "indexes.json").write_text(json.dumps({
            "npcs": [{"name": "visible", "page": 2}, {"name": "future", "page": 5}],
            "locations": [{"name": "visible place", "page": 3}, {"name": "future place", "page": 5}],
        }), encoding="utf-8")
        (scenario / "pregens.json").write_text("[]", encoding="utf-8")
        (scenario / "scene_maps.json").write_text(json.dumps({"2": {"id": "map"}, "5": {"id": "future"}}), encoding="utf-8")
        (images / "page_2.png").write_bytes(b"page-2")
        (images / "page_5.png").write_bytes(b"page-5")

    def test_context_includes_only_current_and_next_chapter_assets(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(temp)):
            self._write_scenario(Path(temp))
            context = scenario_library.load_context("test-scenario")

            self.assertEqual(context["context_chapter_ids"], ["chapter-01", "chapter-02"])
            self.assertEqual(context["page_numbers"], {1, 2, 3, 4})
            self.assertIn("--- 第 4 頁 ---", context["text"])
            self.assertNotIn("--- 第 5 頁 ---", context["text"])
            self.assertEqual([item["name"] for item in context["indexes"]["npcs"]], ["visible"])
            self.assertEqual(set(context["scene_maps"]), {"2"})
            copied = {}
            scenario_library.copy_context_images("test-scenario", context["page_numbers"], copied.__setitem__)
            self.assertEqual(copied, {2: b"page-2"})

    def test_future_images_cannot_be_searched_in_current_context(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(temp)):
            self._write_scenario(Path(temp))
            current = scenario_library.search_images("test-scenario", allowed_chapter_ids={"chapter-01", "chapter-02"})
            self.assertEqual([asset["id"] for asset in current], ["page-2-map"])
            self.assertEqual(scenario_library.next_chapter_id("test-scenario", "chapter-02"), "chapter-03")
            self.assertIsNone(scenario_library.next_chapter_id("test-scenario", "chapter-03"))

    def test_find_similar_ignores_broken_preview_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(temp)):
            broken = Path(temp) / "broken-scenario"
            broken.mkdir()
            (broken / "manifest.json").write_text(json.dumps({"id": "broken-scenario", "title": "Other"}), encoding="utf-8")
            self.assertEqual(scenario_library.find_similar("Different", "new preview"), [])


def _pdf_with_toc(toc: list[list], page_count: int) -> bytes:
    pymupdf = __import__("pymupdf")
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page()
    doc.set_toc(toc)
    return doc.tobytes()


class ScenarioLibraryChapterBuildingTests(unittest.TestCase):
    """Regression tests for the review finding that a flat (single-level)
    bookmark TOC was exploded into one playable chapter per bookmark,
    instead of being collapsed into a single chapter per the design spec's
    own Lightless Beacon worked example."""

    def test_flat_toc_collapses_into_one_playable_chapter_with_sections(self):
        toc = [
            [1, "Introduction", 6], [1, "Scenario Overview", 7], [1, "Background", 8],
            [1, "Start: Choppy Waters", 9], [1, "Dead Beacon", 11], [1, "Amphibious Assault", 18],
            [1, "Conclusion", 21], [1, "Rewards", 22], [1, "Epilogue", 23],
            [1, "Collected Handouts", 25], [1, "Pre-Generated Character Sheets", 31],
        ]
        pdf_bytes = _pdf_with_toc(toc, page_count=43)

        chapters = scenario_library.build_chapters(pdf_bytes, "")

        playable = [c for c in chapters if c.get("kind") == "playable"]
        self.assertEqual(len(playable), 1, "flat bookmarks must collapse into a single playable chapter")
        chapter = playable[0]
        self.assertEqual(chapter["start_page"], 6)
        self.assertLess(chapter["end_page"], 25, "must stop before the Collected Handouts appendix")
        section_titles = [s["title"] for s in chapter["sections"]]
        self.assertEqual(
            section_titles,
            ["Introduction", "Scenario Overview", "Background", "Start: Choppy Waters",
             "Dead Beacon", "Amphibious Assault", "Conclusion", "Rewards", "Epilogue"],
        )
        # The opening scene must be inside the (only) chapter's context window —
        # this is exactly what broke when every bookmark became its own chapter.
        opening = next(s for s in chapter["sections"] if s["title"] == "Start: Choppy Waters")
        self.assertTrue(chapter["start_page"] <= opening["start_page"] <= chapter["end_page"])

    def test_multi_level_toc_still_splits_into_separate_chapters(self):
        toc = [
            [1, "Chapter 1: The Beginning", 1], [2, "Scene A", 2], [2, "Scene B", 5],
            [1, "Chapter 2: The Middle", 10], [2, "Scene C", 11],
            [1, "Chapter 3: The End", 20], [1, "Appendix: Handouts", 30],
        ]
        pdf_bytes = _pdf_with_toc(toc, page_count=50)

        chapters = scenario_library.build_chapters(pdf_bytes, "")

        self.assertEqual([c["title"] for c in chapters], ["Chapter 1: The Beginning", "Chapter 2: The Middle", "Chapter 3: The End"])
        self.assertTrue(all(c["kind"] == "playable" for c in chapters))
        self.assertEqual(chapters[-1]["end_page"], 29, "must stop before the Appendix bookmark")


class ScenarioLibraryReparseTests(unittest.TestCase):
    """Regression tests for the review finding that /coc scenario reparse
    discarded the KP-confirmed matched candidate and always let save_scenario
    derive a fresh content-hash id, silently duplicating the library entry
    a corrected re-upload was supposed to update."""

    def _save(self, *, title: str, text: str, scenario_id: str | None = None, reparse_candidate_id: str | None = None) -> str:
        return scenario_library.save_scenario(
            b"%PDF-1.4 fake", title=title, filename="t.pdf", preview=text[:500], text=text,
            indexes={}, pregens=[], page_maps={}, page_images={},
            scenario_id=scenario_id, reparse_candidate_id=reparse_candidate_id,
        )

    def test_reparse_with_similar_content_updates_existing_entry(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(temp)):
            original = "--- 第 1 頁 ---\n" + "The quick brown fox jumps over the lazy dog. " * 50
            first_id = self._save(title="Test Scenario", text=original)

            corrected = original + " A small correction appended at the end for testing."
            second_id = self._save(title="Test Scenario", text=corrected, reparse_candidate_id=first_id)

            self.assertEqual(second_id, first_id)
            self.assertEqual(len(scenario_library.list_scenarios()), 1)

    def test_reparse_with_unrelated_content_creates_new_entry(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(temp)):
            original = "--- 第 1 頁 ---\n" + "The quick brown fox jumps over the lazy dog. " * 50
            first_id = self._save(title="Test Scenario", text=original)

            unrelated = "--- 第 1 頁 ---\n" + "Completely unrelated content about giant space whales. " * 50
            second_id = self._save(title="Different Scenario", text=unrelated, reparse_candidate_id=first_id)

            self.assertNotEqual(second_id, first_id)
            self.assertEqual(len(scenario_library.list_scenarios()), 2)

    def test_content_similar_matches_on_exact_hash_even_with_short_text(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(temp)):
            scenario_id = self._save(title="Short", text="tiny")
            self.assertTrue(scenario_library.content_similar(scenario_id, "tiny"))
            self.assertFalse(scenario_library.content_similar(scenario_id, "totally different"))


if __name__ == "__main__":
    unittest.main()