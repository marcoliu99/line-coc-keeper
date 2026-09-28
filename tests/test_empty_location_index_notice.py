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
            self.assertEqual(event.call_args.args, ("scenario.location_index.loaded",))
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
