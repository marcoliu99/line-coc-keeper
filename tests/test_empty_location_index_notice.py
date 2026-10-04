"""A republished scenario ships with its derived artifacts invalidated, and
nothing in any reply says so.

Observed: the AI-prepared library variant carries `indexes.json == {}` and no
floor plans, while the same scenario's original variant carries eight locations
and one plan. A group installs the empty one and is told nothing.

These notices report what is missing. The restored legacy map resolver does
not read the location index, and the Keeper can narrate a mapless trip.
"""
import unittest
from unittest.mock import patch

from app import scenario_index


class ReportLocationIndexTests(unittest.TestCase):
    def test_an_empty_index_returns_the_notice(self):
        with patch.object(scenario_index.observability, "event"):
            notice = scenario_index.report_location_index([], source="library")
        self.assertEqual(notice, scenario_index.EMPTY_LOCATION_INDEX_NOTICE)

    def test_neither_notice_claims_movement_is_refused(self):
        """Neither missing artifact alone proves all travel is rejected."""
        for notice in (scenario_index.EMPTY_LOCATION_INDEX_NOTICE,
                       scenario_index.EMPTY_SCENE_MAPS_NOTICE):
            with self.subTest(notice=notice[:12]):
                self.assertNotIn("會被拒絕", notice)

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
    """Map tracking and the location-index prompt have separate notices."""

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
    """The confirmation shows the reporter's verdict verbatim. Re-deciding here
    is how the missing-floor-plan case went unseen: it only read the index."""

    def _text(self, notice):
        from app.services import scenario_ingestion

        return scenario_ingestion._pdf_upload_confirmation_text(
            "劇本", "內文", [], False, {}, {"npcs": [], "locations": []}, 0, notice)

    def test_the_reporters_notice_is_shown(self):
        for notice in (scenario_index.EMPTY_LOCATION_INDEX_NOTICE,
                       scenario_index.EMPTY_SCENE_MAPS_NOTICE):
            with self.subTest(notice=notice[:12]):
                self.assertIn(notice, self._text(notice))

    def test_both_notices_survive_together(self):
        combined = (f"{scenario_index.EMPTY_SCENE_MAPS_NOTICE}\n\n"
                    f"{scenario_index.EMPTY_LOCATION_INDEX_NOTICE}")
        shown = self._text(combined)
        self.assertIn(scenario_index.EMPTY_SCENE_MAPS_NOTICE, shown)
        self.assertIn(scenario_index.EMPTY_LOCATION_INDEX_NOTICE, shown)

    def test_no_notice_stays_silent(self):
        self.assertNotIn("⚠️", self._text(""))


if __name__ == "__main__":
    unittest.main()
