"""An arrival is committed against a known destination, so a scenario with no
location index cannot have movement validated at all.

Observed: a five-investigator party spent twenty-five turns in one room. Every
attempt to reach the stairs was refused while the narration kept describing
them, because the AI-prepared library variant carried `indexes.json == {}`
while the same scenario's original variant carried eight locations. Nothing in
any reply said so.
"""
import unittest
from unittest.mock import patch

from app import scenario_index


class ReportLocationIndexTests(unittest.TestCase):
    def test_an_empty_index_returns_the_notice(self):
        with patch.object(scenario_index.observability, "event"):
            notice = scenario_index.report_location_index([], source="library")
        self.assertEqual(notice, scenario_index.EMPTY_LOCATION_INDEX_NOTICE)
        self.assertIn("移動", notice)

    def test_a_populated_index_returns_nothing(self):
        with patch.object(scenario_index.observability, "event"):
            notice = scenario_index.report_location_index(
                [{"name": "地下室"}, {"name": "書房"}], source="library")
        self.assertEqual(notice, "")

    def test_the_count_is_recorded_either_way(self):
        for locations, expected in (([], 0), ([{"name": "a"}, {"name": "b"}], 2)):
            with self.subTest(count=expected), \
                    patch.object(scenario_index.observability, "event") as event:
                scenario_index.report_location_index(locations, source="pdf_upload")
            self.assertEqual(event.call_args.args, ("scenario.derived_artifacts.loaded",))
            self.assertEqual(event.call_args.kwargs["location_count"], expected)
            self.assertEqual(event.call_args.kwargs["source"], "pdf_upload")

    def test_an_empty_index_is_logged_as_a_warning(self):
        import logging

        with patch.object(scenario_index.observability, "event") as event:
            scenario_index.report_location_index([], source="chapter_switch")
        self.assertEqual(event.call_args.kwargs["level"], logging.WARNING)
        with patch.object(scenario_index.observability, "event") as event:
            scenario_index.report_location_index([{"name": "a"}], source="chapter_switch")
        self.assertEqual(event.call_args.kwargs["level"], logging.INFO)

    def test_the_scenario_title_is_hashed_not_passed_through(self):
        with patch.object(scenario_index.observability, "event") as event:
            scenario_index.report_location_index([], source="library",
                                                 scenario_title="The Haunting")
        self.assertNotIn("The Haunting", str(event.call_args.kwargs))


class SceneMapNoticeTests(unittest.TestCase):
    """Movement resolves against scene_maps, not the location index, so empty
    floor plans are the condition that actually refuses every move."""

    def test_empty_floor_plans_are_reported(self):
        with patch.object(scenario_index.observability, "event"):
            notice = scenario_index.report_location_index(
                [{"name": "地下室"}], source="library", scene_maps={})
        self.assertEqual(notice, scenario_index.EMPTY_SCENE_MAPS_NOTICE)

    def test_both_missing_reports_both(self):
        with patch.object(scenario_index.observability, "event"):
            notice = scenario_index.report_location_index([], source="library", scene_maps={})
        self.assertIn(scenario_index.EMPTY_SCENE_MAPS_NOTICE, notice)
        self.assertIn(scenario_index.EMPTY_LOCATION_INDEX_NOTICE, notice)

    def test_populated_floor_plans_report_nothing(self):
        with patch.object(scenario_index.observability, "event"):
            notice = scenario_index.report_location_index(
                [{"name": "地下室"}], source="library", scene_maps={"1": {"rooms": []}})
        self.assertEqual(notice, "")

    def test_unknown_floor_plans_stay_silent(self):
        # A caller that does not assign maps passes None; that must not read as
        # "assigned and empty".
        with patch.object(scenario_index.observability, "event") as event:
            notice = scenario_index.report_location_index([{"name": "地下室"}], source="correction")
        self.assertEqual(notice, "")
        self.assertIsNone(event.call_args.kwargs["scene_map_count"])

    def test_the_count_is_recorded(self):
        with patch.object(scenario_index.observability, "event") as event:
            scenario_index.report_location_index([], source="library",
                                                 scene_maps={"1": {}, "2": {}})
        self.assertEqual(event.call_args.kwargs["scene_map_count"], 2)


class UploadConfirmationTests(unittest.TestCase):
    def _text(self, location_count):
        from app import legacy_commands

        return legacy_commands._pdf_upload_confirmation_text(
            "劇本", "內文", [], False, {}, {"npcs": [], "locations": []}, 0, location_count)

    def test_an_upload_with_no_locations_says_so(self):
        self.assertIn(scenario_index.EMPTY_LOCATION_INDEX_NOTICE, self._text(0))

    def test_an_upload_with_locations_does_not(self):
        self.assertNotIn(scenario_index.EMPTY_LOCATION_INDEX_NOTICE, self._text(8))

    def test_an_unknown_count_stays_silent(self):
        # None means the caller did not look; it must not read as "none found".
        self.assertNotIn(scenario_index.EMPTY_LOCATION_INDEX_NOTICE, self._text(None))


if __name__ == "__main__":
    unittest.main()
