"""A turn that cannot finish says what the table has already been shown, and only that."""
from __future__ import annotations

import unittest

from app.domain.models import MechanicResult, StateDelta, TurnResolution
from app.models import GroupState
from app.services import prompt_config, turn_fallback


def state_with(*, narration: tuple[str, ...] = (), locations=(), npcs=(), clues=()) -> GroupState:
    state = GroupState(group_id="g", timeline_id="t")
    state.scenario_location_index = [dict(item) for item in locations]
    state.scenario_npc_index = [dict(item) for item in npcs]
    state.known_clues = [dict(item) for item in clues]
    for text in narration:
        state.log.append({"role": "user", "content": "我想去地下室和鎮長辦公室"})
        state.log.append({"role": "assistant", "content": text, "audience": "public", "timeline_id": "t"})
    return state


LOCATIONS = [{"name": "二樓房間"}, {"name": "洗衣房"}, {"name": "地下室", "aliases": ["酒窖"]}]
NPCS = [{"name": "房東", "aliases": ["Gardiner 的房東"]}, {"name": "神祕訪客"}]


class SceneHintTests(unittest.TestCase):
    def test_nothing_shown_means_no_line(self):
        self.assertEqual(turn_fallback.scene_hints(state_with(locations=LOCATIONS, npcs=NPCS)), "")

    def test_only_what_the_narration_has_said_is_named(self):
        state = state_with(narration=("你站在二樓房間，房東低聲說話。",), locations=LOCATIONS, npcs=NPCS)
        hint = turn_fallback.scene_hints(state)
        self.assertIn("二樓房間", hint)
        self.assertIn("房東", hint)
        for hidden in ("洗衣房", "地下室", "酒窖", "神祕訪客"):
            self.assertNotIn(hidden, hint, "a place or person nobody has been shown must not be named")

    def test_what_a_player_typed_does_not_count(self):
        """The fixture's player lines mention the basement; only narration may reveal a name."""
        state = state_with(narration=("你站在二樓房間。",), locations=LOCATIONS)
        self.assertNotIn("地下室", turn_fallback.scene_hints(state))

    def test_private_narration_does_not_count(self):
        state = state_with(locations=LOCATIONS)
        state.log.append({"role": "assistant", "content": "只有你看見了地下室的暗門。", "audience": "player_private"})
        self.assertEqual(turn_fallback.scene_hints(state), "")

    def test_an_alias_counts_and_is_listed_as_the_narration_wrote_it(self):
        state = state_with(narration=("門後是陰涼的酒窖。",), locations=LOCATIONS)
        hint = turn_fallback.scene_hints(state)
        self.assertIn("酒窖", hint)
        self.assertNotIn("地下室", hint)

    def test_an_alias_that_was_narrated_is_listed_under_that_alias_not_the_canonical_name(self):
        """The canonical name may be the secret: only the spelling the table was actually told may appear."""
        npcs = [{"name": "兇手的真名", "aliases": ["蒙面人"]}]
        hint = turn_fallback.scene_hints(state_with(narration=("走廊盡頭站著一個蒙面人。",), npcs=npcs))
        self.assertIn("蒙面人", hint)
        self.assertNotIn("兇手的真名", hint)

    def test_an_entry_is_listed_once_under_the_spelling_narrated_most_recently(self):
        """One NPC must not read as two people, nor use up two of the five slots."""
        npcs = [{"name": "兇手的真名", "aliases": ["蒙面人"]}]
        later_reveal = state_with(narration=("走廊盡頭站著一個蒙面人。", "原來他就是兇手的真名。"), npcs=npcs)
        hint = turn_fallback.scene_hints(later_reveal)
        self.assertIn("兇手的真名", hint)
        self.assertNotIn("蒙面人", hint)
        earlier_reveal = state_with(narration=("原來他就是兇手的真名。", "走廊盡頭站著一個蒙面人。"), npcs=npcs)
        hint = turn_fallback.scene_hints(earlier_reveal)
        self.assertIn("蒙面人", hint)
        self.assertNotIn("兇手的真名", hint)

    def test_narration_from_another_timeline_does_not_disclose_a_same_named_entry(self):
        """A new scenario keeps the log but starts a new timeline: its undisclosed "地下室" was not shown by the old one."""
        state = state_with(narration=("你們走進了地下室。",), locations=LOCATIONS)
        state.log[-1]["timeline_id"] = "an-earlier-timeline"
        self.assertEqual(turn_fallback.scene_hints(state), "")
        state.timeline_id = "an-earlier-timeline"
        self.assertIn("地下室", turn_fallback.scene_hints(state))

    def test_narration_without_a_timeline_stamp_is_not_trusted_once_the_state_has_a_timeline(self):
        state = state_with(narration=("你們走進了地下室。",), locations=LOCATIONS)
        del state.log[-1]["timeline_id"]
        self.assertEqual(turn_fallback.scene_hints(state), "")
        state.timeline_id = ""
        self.assertIn("地下室", turn_fallback.scene_hints(state))

    def test_a_clue_from_another_timeline_is_not_listed(self):
        clues = [{"text": "舊劇本的線索", "visibility": "public", "timeline_id": "an-earlier-timeline"},
                 {"text": "這份劇本的線索", "visibility": "public", "timeline_id": "t"},
                 {"text": "尚未驗證的線索", "visibility": "public"}]
        hint = turn_fallback.scene_hints(state_with(clues=clues))
        self.assertNotIn("舊劇本的線索", hint)
        self.assertIn("這份劇本的線索", hint)
        self.assertIn("尚未驗證的線索", hint)  # an unstamped record is this timeline's, as everywhere else

    def test_the_canonical_name_is_listed_when_it_was_the_one_narrated(self):
        npcs = [{"name": "房東", "aliases": ["Gardiner 的房東"]}]
        hint = turn_fallback.scene_hints(state_with(narration=("房東低聲說話。",), npcs=npcs))
        self.assertIn("房東", hint)
        self.assertNotIn("Gardiner", hint)

    def test_a_name_inside_a_longer_name_of_another_entry_does_not_count(self):
        npcs = [{"name": "房東"}, {"name": "房東太太"}]
        hint = turn_fallback.scene_hints(state_with(narration=("房東太太端來了茶。",), npcs=npcs))
        self.assertIn("房東太太", hint)
        self.assertNotIn("人物：房東　", hint + "　")
        self.assertEqual(hint.count("房東"), 1, "the landlord is not listed because his wife was mentioned")

    def test_it_still_counts_when_the_short_name_appears_on_its_own_as_well(self):
        npcs = [{"name": "房東"}, {"name": "房東太太"}]
        hint = turn_fallback.scene_hints(state_with(narration=("房東太太端來了茶。", "房東接過杯子。"), npcs=npcs))
        self.assertIn("房東、房東太太", hint.replace("人物：", ""))

    def test_ascii_names_do_not_match_inside_other_words(self):
        npcs = [{"name": "Ann"}]
        self.assertEqual(turn_fallback.scene_hints(state_with(narration=("Annette 走了進來。",), npcs=npcs)), "")
        self.assertIn("Ann", turn_fallback.scene_hints(state_with(narration=("Ann 走了進來。",), npcs=npcs)))

    def test_the_most_recent_come_first_and_the_list_is_bounded(self):
        names = [f"地點{n}號" for n in range(1, 9)]
        state = state_with(narration=tuple(f"你來到{name}。" for name in names), locations=[{"name": n} for n in names])
        hint = turn_fallback.scene_hints(state)
        self.assertLess(hint.index("地點8號"), hint.index("地點7號"))
        self.assertEqual(sum(name in hint for name in names), 5)

    def test_a_one_character_name_never_matches(self):
        state = state_with(narration=("門開了。",), locations=[{"name": "門"}])
        self.assertEqual(turn_fallback.scene_hints(state), "")

    def test_only_public_clues_are_listed_and_long_ones_are_cut(self):
        state = state_with(clues=[
            {"text": "染血紙片上有希臘文", "visibility": "public"},
            {"text": "兇手其實是房東", "visibility": "kp_only"},
            {"text": "這是一條非常非常非常非常非常非常非常非常非常長的公開線索文字", "visibility": "public"},
        ])
        hint = turn_fallback.scene_hints(state)
        self.assertIn("染血紙片上有希臘文", hint)
        self.assertNotIn("兇手", hint)
        self.assertIn("…", hint)

    def test_the_whole_state_is_left_alone(self):
        state = state_with(narration=("二樓房間。",), locations=LOCATIONS)
        before = state.to_dict()
        turn_fallback.scene_hints(state)
        self.assertEqual(state.to_dict(), before)


class GuidanceTests(unittest.TestCase):
    def test_hints_are_added_for_the_reasons_that_ask_for_something_else(self):
        for reason in ("executor_no_action", "unsupported_action", "no_scenario_evidence"):
            with self.subTest(reason=reason):
                self.assertTrue(turn_fallback.guidance(reason, "提示行").endswith("\n提示行"))

    def test_other_reasons_and_empty_hints_are_unchanged(self):
        for reason in ("tool_failure", "internal_error", "state_conflict", "narration_failure"):
            self.assertEqual(turn_fallback.guidance(reason, "提示行"), turn_fallback.guidance(reason))
        self.assertEqual(turn_fallback.guidance("executor_no_action", ""), turn_fallback.guidance("executor_no_action"))


def blocked_result(reason: str, **status) -> MechanicResult:
    return MechanicResult(
        success=True, action_type="none", narrative_facts=[], state_delta=StateDelta(),
        check_status={"tool_called": False, "pending": None, **status},
        turn_resolution=TurnResolution(disposition="incomplete", validation_code="model_incomplete"),
        fallback_reason=reason,
    )


class ReplyTests(unittest.TestCase):
    def test_the_reply_names_what_the_table_has_seen(self):
        state = state_with(narration=("你站在二樓房間，房東低聲說話。",), locations=LOCATIONS, npcs=NPCS)
        reply = prompt_config.enforce_mechanic_check_consistency(
            "narration", blocked_result("executor_no_action"), state=state)
        self.assertIn(turn_fallback.guidance("executor_no_action"), reply)
        self.assertIn("目前已在劇情中出現", reply)
        self.assertIn("二樓房間", reply)
        self.assertNotIn("洗衣房", reply)

    def test_without_state_or_without_anything_shown_the_reply_is_the_plain_one(self):
        plain = prompt_config.enforce_mechanic_check_consistency("narration", blocked_result("executor_no_action"))
        self.assertNotIn("目前已在劇情中出現", plain)
        empty = prompt_config.enforce_mechanic_check_consistency(
            "narration", blocked_result("executor_no_action"), state=state_with(locations=LOCATIONS))
        self.assertEqual(empty, plain)

    def test_a_scenario_evidence_block_gets_the_line_too(self):
        state = state_with(narration=("你站在二樓房間。",), locations=LOCATIONS)
        reply = prompt_config.enforce_mechanic_check_consistency(
            "narration", blocked_result("no_scenario_evidence", scenario_evidence_blocked=True), state=state)
        self.assertIn("劇本依據", reply)
        self.assertIn("二樓房間", reply)

    def test_an_error_reply_does_not_get_a_list(self):
        state = state_with(narration=("你站在二樓房間。",), locations=LOCATIONS)
        reply = prompt_config.enforce_mechanic_check_consistency(
            "narration", blocked_result("internal_error"), state=state)
        self.assertNotIn("目前已在劇情中出現", reply)


if __name__ == "__main__":
    unittest.main()
