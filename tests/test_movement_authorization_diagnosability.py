"""A blocked turn has to be diagnosable — by the player and after the fact.

Observed in the 2026-09-28 05:03 log: three of a new game's turns resolved
`blocked`, all with `validation_code: validated`, and the player saw the same
sentence every time. One was 「直奔商店購買油燈跟煤油罐」, refused
`no_player_movement_authorization`; another was `unknown_map`, whose page
argument was recorded nowhere.
"""
import logging
import unittest
from unittest.mock import patch

from app import intent_parser
from app.services import movement, prompt_config


class MovementVerbTests(unittest.TestCase):
    def test_the_verbs_that_were_missing_are_recognised(self):
        for text in ("直奔商店", "奔向大門", "趕往醫院", "趕到碼頭", "衝向門口",
                     "衝進地下室", "跑向樓梯", "跑到二樓", "抵達宅邸", "來到地下室",
                     "返回客廳", "折返走廊"):
            with self.subTest(text=text):
                self.assertTrue(intent_parser.has_movement_verb(text))

    def test_carrying_and_observing_are_still_not_movement(self):
        # 走/去 are in the pattern; these must not trip them.
        for text in ("他右手邊有把刀", "我拿走桌上的刀", "我帶走那本日記",
                     "我收走證物", "我偷走鑰匙"):
            with self.subTest(text=text):
                self.assertFalse(intent_parser.has_movement_verb(text))

    def test_the_observed_sentence_now_parses_as_movement(self):
        self.assertTrue(intent_parser.has_movement_verb("直奔商店購買油燈跟煤油罐"))


class AuthorizedSpanTests(unittest.TestCase):
    """The gate that refused the observed turn."""

    text = "直奔商店購買油燈跟煤油罐"

    def test_a_whole_clause_is_authorized_as_before(self):
        self.assertTrue(movement._authorized_span(self.text, self.text))
        other = "前往商店，購買油燈跟煤油罐"
        self.assertTrue(movement._authorized_span("前往商店", other))
        self.assertTrue(movement._authorized_span("購買油燈跟煤油罐", other))

    def test_the_movement_half_of_a_compound_clause_is_authorized(self):
        # The refusal this whole spec is about: no comma, so one clause, and
        # quoting only the movement was rejected.
        self.assertTrue(movement._authorized_span("直奔商店", self.text))

    def test_a_prefix_without_movement_intent_is_refused(self):
        for span in ("直", "直奔", "直奔商"):
            with self.subTest(span=span):
                self.assertFalse(movement._authorized_span(span, self.text))

    def test_a_fragment_that_is_not_a_prefix_is_refused(self):
        for span in ("商店購買", "購買油燈", "油燈跟煤油罐"):
            with self.subTest(span=span):
                self.assertFalse(movement._authorized_span(span, self.text))


class RejectionReportingTests(unittest.TestCase):
    def _state(self, scene_maps=None):
        from app.models import GroupState

        state = GroupState(group_id="g")
        state.scene_maps = scene_maps if scene_maps is not None else {"17": {"rooms": []}}
        return state

    def test_a_rejection_records_the_page_it_got_and_the_pages_that_exist(self):
        args = {"page": "6", "destination": "舊科比特宅邸", "path": ["a", "b"]}
        with patch.object(movement.observability, "event") as event:
            result = movement._reject("unknown_map", self._state(), args, "我前往宅邸")
        self.assertEqual(result, {"ok": False, "error": "unknown_map"})
        self.assertEqual(event.call_args.args, ("movement.rejected",))
        kwargs = event.call_args.kwargs
        self.assertEqual(kwargs["error_type"], "unknown_map")
        self.assertEqual(kwargs["page"], "6")
        self.assertEqual(kwargs["scene_map_pages"], ["17"])
        self.assertEqual(kwargs["path_length"], 2)
        self.assertEqual(kwargs["source_span"], "我前往宅邸")
        self.assertEqual(kwargs["level"], logging.WARNING)

    def test_a_missing_path_reports_none_rather_than_zero(self):
        # None means "not a list"; 0 would read as "an empty path was sent".
        with patch.object(movement.observability, "event") as event:
            movement._reject("invalid_movement_target", self._state(), {}, "")
        self.assertIsNone(event.call_args.kwargs["path_length"])

    def test_long_values_are_truncated(self):
        args = {"destination": "房" * 300, "page": "頁" * 100, "path": []}
        with patch.object(movement.observability, "event") as event:
            movement._reject("invalid_movement_path", self._state(), args, "走" * 400)
        kwargs = event.call_args.kwargs
        self.assertEqual(len(kwargs["destination"]), 80)
        self.assertEqual(len(kwargs["page"]), 40)
        self.assertEqual(len(kwargs["source_span"]), 120)


class BlockedAdviceTests(unittest.TestCase):
    """The player-facing half: one sentence per cause, not one for all of them."""

    def _reply(self, status):
        from app.domain.models import MechanicResult, StateDelta
        from app.services.turn_resolution import TurnResolution

        result = MechanicResult(True, "none", [], StateDelta(), check_status=status,
                                turn_resolution=TurnResolution(disposition="blocked"))
        return prompt_config.enforce_mechanic_check_consistency("敘事", result)

    def test_each_mapped_code_gets_its_own_sentence(self):
        seen = set()
        for code, advice in prompt_config.MOVEMENT_BLOCKED_ADVICE.items():
            with self.subTest(code=code):
                reply = self._reply({"movement_blocked": code})
                self.assertIn(advice, reply)
                self.assertNotIn("請先確認目前狀態或更正原本的行動", reply)
                seen.add(advice)
        self.assertEqual(len(seen), len(prompt_config.MOVEMENT_BLOCKED_ADVICE))

    def test_the_observed_failure_tells_the_player_what_to_change(self):
        reply = self._reply({"movement_blocked": "no_player_movement_authorization"})
        self.assertIn("前往商店，然後購買油燈", reply)

    def test_an_unmapped_code_keeps_the_original_sentence(self):
        for code in ("", "something_new"):
            with self.subTest(code=code):
                self.assertIn("請先確認目前狀態或更正原本的行動",
                              self._reply({"movement_blocked": code}))

    def test_a_pending_check_still_wins_over_the_movement_advice(self):
        reply = self._reply({"movement_blocked": "unknown_map",
                             "pending": {"investigator": "Marco", "skill": "Spot Hidden"}})
        self.assertIn("請按檢定按鈕", reply)

    def test_scenario_evidence_blocked_still_wins(self):
        reply = self._reply({"movement_blocked": "unknown_map",
                             "scenario_evidence_blocked": True})
        self.assertIn("目前未取得足夠的劇本依據", reply)


class CheckStatusTests(unittest.TestCase):
    def test_a_failed_arrival_records_its_code(self):
        from app.agents import tool_gateway

        status: dict = {}
        tool_gateway._record_check_status(
            status, "commit_movement", {"ok": False, "error": "unknown_map"})
        self.assertEqual(status["movement_blocked"], "unknown_map")

    def test_a_later_success_clears_it(self):
        from app.agents import tool_gateway

        status: dict = {"movement_blocked": "unknown_map"}
        tool_gateway._record_check_status(
            status, "commit_movement", {"ok": True, "arrival": {}})
        self.assertNotIn("movement_blocked", status)


if __name__ == "__main__":
    unittest.main()


class ThirdPartyClauseTests(unittest.TestCase):
    """A wider verb list must not turn someone else's movement into the player's.

    `executor.py:232` downgrades an otherwise resolved turn to
    `arrival_not_committed` whenever a proposal exists and never arrived. So a
    proposal built out of 「怪物衝進地下室」 breaks the gunshot that followed it:
    the player asked for one thing, got 「這次行動尚未完整處理」.
    """

    def _state(self):
        from app.models import Character, GroupState

        state = GroupState(group_id="g")
        state.timeline_id = "t"
        marco = Character(name="Marco", owner_id="u1")
        state.characters["u1"] = marco
        state.characters_by_id[marco.character_id] = marco
        state.active_character_id_by_user["u1"] = marco.character_id
        state.scenario_npc_index = [{"name": "科比特", "aliases": ["老科比特", "W. Corbitt"]}]
        return state

    def _span(self, text):
        from app.services import movement as m

        proposal = m.propose(self._state(), "u1", "u1", text)
        return proposal.original_span if proposal else None

    def test_a_named_creature_does_not_propose_the_players_movement(self):
        # The regression the wider verb list introduced: 衝進 was not a verb
        # before, so this made no proposal at all.
        self.assertIsNone(self._span("怪物衝進地下室，我開槍"))

    def test_a_scenario_npc_does_not_either(self):
        # 走進 was always a verb, so this one was wrong before the verbs were
        # widened too.
        for text in ("科比特衝向我，我後退", "老科比特走進客廳，我觀察"):
            with self.subTest(text=text):
                self.assertIsNone(self._span(text))

    def test_a_non_human_pronoun_is_dropped_with_the_others(self):
        for text in ("牠衝進地下室，我開槍", "它走向門口，我後退", "某人走進來，我躲起來"):
            with self.subTest(text=text):
                self.assertIsNone(self._span(text))

    def test_the_players_own_clause_is_still_found_alongside_one(self):
        self.assertEqual(self._span("怪物衝進地下室，我跟著跑進去"), "我跟著跑進去")

    def test_the_players_own_movement_is_untouched(self):
        for text in ("直奔商店購買油燈跟煤油罐", "我走向門口", "前往商店"):
            with self.subTest(text=text):
                self.assertEqual(self._span(text), text)

    def test_the_player_moving_with_a_creature_is_still_the_player_moving(self):
        self.assertEqual(self._span("我和怪物一起衝進地下室"), "我和怪物一起衝進地下室")

    def test_a_one_character_name_is_not_treated_as_an_actor(self):
        # Matching a single character against a prefix would reject far too much.
        state = self._state()
        state.scenario_npc_index = [{"name": "科"}]
        self.assertNotIn("科", movement._other_actor_names(state, "u1"))
