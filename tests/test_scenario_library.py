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


if __name__ == "__main__":
    unittest.main()