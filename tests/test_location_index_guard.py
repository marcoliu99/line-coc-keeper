"""An unstable index extraction that found fewer locations than the scenario's numbered LOCATION headings must not
replace a valid index (the Haunting came back with 9, then 8, locations on repeated /coc index runs)."""
import asyncio
import unittest
from unittest.mock import patch

from app import scenario_index, scenario_page_repair
from app.commands.handlers import system as system_handler
from app.models import GroupState
from app.services import scenario_ingestion, scenario_lifecycle


def _text(count: int, *, heading: str = "LOCATION {n}: PLACE {n}") -> str:
    return "\n\n".join(heading.format(n=n) + f"\nbody {n}" for n in range(1, count + 1))


def _locations(count: int) -> list[dict]:
    return [{"name": f"loc{n}"} for n in range(count)]


class DetectTests(unittest.TestCase):
    def test_consecutive_headings_in_every_supported_form(self):
        for heading in ("LOCATION {n}:", "Location {n}:", "## LOCATION {n}:", "### LOCATION {n}: THE OLD CORBITT PLACE"):
            with self.subTest(heading=heading):
                self.assertEqual(scenario_index.detect_numbered_location_sequence(_text(9, heading=heading)),
                                 list(range(1, 10)))

    def test_markdown_headings(self):
        text = "## LOCATION 1: INTRODUCTION\nx\n### LOCATION 2: THE BOSTON GLOBE\ny"
        self.assertEqual(scenario_index.detect_numbered_location_sequence(text), [1, 2])

    def test_prose_mentions_are_not_headings(self):
        text = ("LOCATION 1: START\nProceed to Location 2, 3 or 4 next.\nsee Location 5\nreturn to Location 9\n"
                "LOCATION 2: NEXT\nHe goes to Location 3: the cellar, he says.")
        self.assertEqual(scenario_index.detect_numbered_location_sequence(text), [1, 2])
        self.assertIsNone(scenario_index.detect_numbered_location_sequence(
            "Proceed to Location 2, 3 or 4.\nsee Location 5\nreturn to Location 9"))

    def test_only_an_exact_one_to_n_sequence_activates(self):
        for numbers in ([1, 2, 4], [1, 2, 2, 3], [2, 3, 4], [1], [1, 3, 2], []):
            text = "\n".join(f"LOCATION {n}: x" for n in numbers)
            with self.subTest(numbers=numbers):
                self.assertIsNone(scenario_index.detect_numbered_location_sequence(text))

    def test_rooms_are_not_top_level_locations(self):
        self.assertIsNone(scenario_index.detect_numbered_location_sequence("ROOM 1: a\nROOM 2: b"))


class UnderflowTests(unittest.TestCase):
    def _check(self, text: str, extracted: int):
        with patch.object(scenario_index.observability, "event") as event:
            result = scenario_index.location_index_underflow(
                text, _locations(extracted), previous_count=9, source="index_command")
        return result, event.call_args.kwargs

    def test_fewer_than_the_headings_is_rejected_and_logged_without_text(self):
        result, fields = self._check(_text(9), 8)
        self.assertEqual(result, (9, 8))
        self.assertEqual((fields["status"], fields["reason"]), ("reject", "numbered_location_underflow"))
        self.assertEqual((fields["expected_location_count"], fields["extracted_location_count"],
                          fields["previous_location_count"]), (9, 8, 9))
        self.assertNotIn("body", str(fields))

    def test_the_minimum_and_more_pass(self):
        for extracted in (9, 10):
            with self.subTest(extracted=extracted):
                result, fields = self._check(_text(9), extracted)
                self.assertIsNone(result)
                self.assertEqual(fields["status"], "pass")

    def test_without_a_sequence_the_check_skips(self):
        for text in ("LOCATION 1: a\nLOCATION 2: b\nLOCATION 4: d", "LOCATION 1: a\nLOCATION 2: b\nLOCATION 2: c",
                     "plain prose"):
            with self.subTest(text=text):
                result, fields = self._check(text, 0)
                self.assertIsNone(result)
                self.assertEqual(fields["status"], "skip")


class IndexCommandTests(unittest.TestCase):
    def _run(self, state: GroupState, extracted_locations: int):
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        async def noop(*args) -> None:
            return None

        index = {"npcs": [{"name": "new npc"}], "locations": _locations(extracted_locations)}
        with patch.object(system_handler, "load_state", return_value=state), \
                patch("app.repositories.state_transaction.commit_snapshot") as commit, \
                patch.object(system_handler.scenario_index, "extract_scenario_index", return_value=index):
            asyncio.run(system_handler.handle_system_command(
                "group-index-guard", "kp-1", reply, noop, noop, noop, ["/coc", "index"]))
        return replies, commit

    def _state(self, text: str, previous: int) -> GroupState:
        state = GroupState("group-index-guard")
        state.scenario_text = text
        state.scenario_npc_index = [{"name": "old npc"}] if previous else []
        state.scenario_location_index = _locations(previous)
        return state

    def test_a_complete_rebuild_is_committed(self):
        state = self._state(_text(9), 9)
        _, commit = self._run(state, 9)
        commit.assert_called_once()
        self.assertEqual(len(state.scenario_location_index), 9)
        self.assertEqual(state.scenario_npc_index, [{"name": "new npc"}])

    def test_a_larger_rebuild_is_committed(self):
        state = self._state(_text(9), 9)
        _, commit = self._run(state, 10)
        commit.assert_called_once()
        self.assertEqual(len(state.scenario_location_index), 10)

    def test_an_incomplete_rebuild_keeps_the_previous_index_untouched(self):
        state = self._state(_text(9), 9)
        replies, commit = self._run(state, 8)
        commit.assert_not_called()
        self.assertEqual(len(state.scenario_location_index), 9)
        self.assertEqual(state.scenario_npc_index, [{"name": "old npc"}])
        self.assertIn("LOCATION 1–9", replies[0])
        self.assertIn("已保留上一版索引", replies[0])

    def test_without_numbered_headings_the_rebuild_is_committed_as_before(self):
        state = self._state("LOCATION 1: a\nLOCATION 2: b\nLOCATION 4: d", 9)
        _, commit = self._run(state, 1)
        commit.assert_called_once()
        self.assertEqual(len(state.scenario_location_index), 1)

    def test_the_effective_text_with_a_page_repair_is_what_is_checked(self):
        base = "--- 第 10 頁 ---\n" + _text(9) + "\n\n--- 第 11 頁 ---\nbroken text"
        repaired = scenario_page_repair.apply_pages(base, {11: "fixed text"})
        self.assertIn("fixed text", repaired)
        state = self._state(repaired, 9)
        replies, commit = self._run(state, 8)
        commit.assert_not_called()
        self.assertEqual(len(state.scenario_location_index), 9)
        self.assertIn("保留上一版索引", replies[0])

    def test_no_previous_index_does_not_commit_an_incomplete_one_and_invents_nothing(self):
        state = self._state(_text(9), 0)
        replies, commit = self._run(state, 8)
        commit.assert_not_called()
        self.assertEqual(state.scenario_location_index, [])
        self.assertIn("未寫入這份不完整索引", replies[0])


class UploadIndexTests(unittest.TestCase):
    def _extract(self, extracted: int):
        index = {"npcs": [{"name": "n"}], "locations": _locations(extracted)}
        with patch.object(scenario_ingestion.scenario_index, "extract_scenario_index", return_value=index):
            return asyncio.run(scenario_ingestion._extract_checked_index(_text(9), source="pdf_upload"))

    def test_an_incomplete_index_is_dropped_with_a_notice_and_the_import_goes_on(self):
        index, notice = self._extract(8)
        self.assertEqual(index, {"npcs": [], "locations": []})
        self.assertIn("未寫入這份不完整索引", notice)
        self.assertNotIn("已載入", notice)
        self.assertIn("9", notice)

    def test_a_complete_index_is_kept_without_a_notice(self):
        index, notice = self._extract(9)
        self.assertEqual(len(index["locations"]), 9)
        self.assertEqual(notice, "")


class CorrectionTests(unittest.TestCase):
    def _repair(self, indexes: dict) -> GroupState:
        state = GroupState("group-index-guard")
        state.scenario_npc_index = [{"name": "old npc"}]
        state.scenario_location_index = _locations(9)
        scenario_lifecycle._repair(state, {"manifest": {"title": "t"}, "text": "new text", "indexes": indexes,
                                           "pregens": []})
        return state

    def test_a_correction_with_an_empty_index_keeps_the_running_index(self):
        state = self._repair({"npcs": [], "locations": []})
        self.assertEqual(state.scenario_text, "new text")
        self.assertEqual(len(state.scenario_location_index), 9)
        self.assertEqual(state.scenario_npc_index, [{"name": "old npc"}])

    def test_a_correction_with_an_index_replaces_it(self):
        state = self._repair({"npcs": [], "locations": _locations(9)})
        self.assertEqual(state.scenario_npc_index, [])


if __name__ == "__main__":
    unittest.main()
