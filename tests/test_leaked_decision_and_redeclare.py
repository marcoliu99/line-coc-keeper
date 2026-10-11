"""Two replies from the 2026-10-11 soak runs: an Executor decision shown to a player, and a turn already ended that
the Keeper declared again."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import presentation
from app.agents import executor, reply_pipeline
from app.models import GroupState
from app.services import turn_fallback

LEAK = ('{"disposition":"await_luck","actor_character_id":"legacy-user:player-1","waiting_for":"legacy-user:player-1",'
        '"check_id":"","evidence_refs":["state"]}')


class LeakedDecisionTests(unittest.TestCase):
    def test_the_decision_object_in_front_of_a_reply_is_removed(self):
        reply = f"{LEAK}\n\nAlan Mercer 的 偵查與聆聽 已擲出 23，目前仍等待 Luck 決定。"
        self.assertEqual(presentation.strip_leaked_decision(reply), "Alan Mercer 的 偵查與聆聽 已擲出 23，目前仍等待 Luck 決定。")

    def test_a_decision_with_nested_values_or_a_code_fence_goes_whole(self):
        nested = json.dumps({"disposition": "deferred", "waiting_for": "x", "detail": {"a": [1, {"b": 2}]}})
        self.assertEqual(presentation.strip_leaked_decision(f"{nested}\n\n他停下腳步。"), "他停下腳步。")
        fenced = f"```json\n{LEAK}\n```\n\n雨還在下。"
        self.assertEqual(presentation.strip_leaked_decision(fenced), "雨還在下。")

    def test_a_decision_after_or_between_narration_is_removed_and_the_narration_kept(self):
        self.assertEqual(presentation.strip_leaked_decision(f"雨聲漸大。\n\n{LEAK}"), "雨聲漸大。")
        self.assertEqual(presentation.strip_leaked_decision(f"前面。 {LEAK} 後面。"), "前面。  後面。")

    def test_other_braces_and_json_are_left_alone(self):
        for text in ("他寫下 {not json} 在牆上。", '{"name":"Alan","hp":13}', "沒有任何大括號。",
                     'a "disposition" is a mood {x}'):
            self.assertEqual(presentation.strip_leaked_decision(text), text)

    def test_the_reply_pipeline_scrubs_before_anything_else(self):
        draft = SimpleNamespace(text=f"{LEAK}\n\n林間靜了下來。")
        reply_pipeline._scrub_leaks(SimpleNamespace(), draft)
        self.assertEqual(draft.text, "林間靜了下來。")


class JunkTailTests(unittest.TestCase):
    """The tail of a JSON wrapper the model put around its narration (two 2026-10-11 soak replies)."""

    def test_the_wrapper_tail_and_the_stray_word_after_it_are_removed(self):
        opening = "Amelia Ward，你是古董商。浪聲幾乎吞沒一切——你打算如何向他們介紹自己，又為何前往羅克波特？」}} bheidh"
        self.assertEqual(presentation.strip_junk_tail(opening),
                         "Amelia Ward，你是古董商。浪聲幾乎吞沒一切——你打算如何向他們介紹自己，又為何前往羅克波特？")
        self.assertEqual(presentation.strip_junk_tail("你們已抵達海灘，Reidel，你要怎麼查看？」}}"), "你們已抵達海灘，Reidel，你要怎麼查看？")
        self.assertEqual(presentation.strip_junk_tail('雨還在下。"}'), "雨還在下。")
        self.assertEqual(presentation.strip_junk_tail("雨還在下。]}\n"), "雨還在下。")

    def test_a_stray_word_glued_to_the_closers_goes_too(self):
        self.assertEqual(presentation.strip_junk_tail("還是進入 lobby？」}}.Begin"), "還是進入 lobby？")
        self.assertEqual(presentation.strip_junk_tail("雨還在下。}}Begin"), "雨還在下。")

    def test_only_the_unmatched_closer_goes_when_balanced_braces_come_before_it(self):
        self.assertEqual(presentation.strip_junk_tail("資料是 {hp: 13}}"), "資料是 {hp: 13}")
        self.assertEqual(presentation.strip_junk_tail("名單 [Alan]]} bheidh"), "名單 [Alan]")

    def test_an_unmatched_closer_earlier_does_not_cost_a_balanced_ending_its_brace(self):
        text = "先前多了一個 }，但資料是 {hp: 13}"
        self.assertEqual(presentation.strip_junk_tail(text), text)

    def test_a_quote_the_narration_opened_stays_with_its_text(self):
        self.assertEqual(presentation.strip_junk_tail("他低聲說：「走吧。」}}"), "他低聲說：「走吧。」")
        self.assertEqual(presentation.strip_junk_tail("He said, “Go.”}}"), "He said, “Go.”")
        self.assertEqual(presentation.strip_junk_tail('He said, "Go."}}'), 'He said, "Go."')

    def test_balanced_braces_and_ordinary_endings_are_left_alone(self):
        for text in ("他寫下 {not json} 在牆上。", '{"name":"Alan","hp":13}', "沒有任何大括號。", "她說：「跟我來。」",
                     "名單上寫著 [Alan, Mercer]。", "雨還在下。 bheidh"):
            self.assertEqual(presentation.strip_junk_tail(text), text)

    def test_the_reply_pipeline_removes_the_tail_too(self):
        draft = SimpleNamespace(text="林間靜了下來。」}} bheidh")
        reply_pipeline._scrub_leaks(SimpleNamespace(), draft)
        self.assertEqual(draft.text, "林間靜了下來。")
        self.assertEqual(reply_pipeline.STEPS[0].name, "scrub_leaks")

    def test_a_reply_that_was_only_the_decision_is_recorded_and_left_for_the_lines_the_pipeline_adds(self):
        ctx = SimpleNamespace(state=GroupState(group_id="g"), user_id="p1", turn_id="t1")
        for text in (LEAK, f"```json\n{LEAK}\n```"):
            draft = SimpleNamespace(text=text)
            with patch.object(turn_fallback, "record") as record:
                reply_pipeline._scrub_leaks(ctx, draft)
            self.assertEqual(draft.text, "", text)
            record.assert_called_once_with("narration_failure", state=ctx.state, user_id="p1", turn_id="t1")
        empty = SimpleNamespace(text="")
        with patch.object(turn_fallback, "record") as record:
            reply_pipeline._scrub_leaks(ctx, empty)
        self.assertEqual(empty.text, "")
        record.assert_not_called()  # a reply that was empty to begin with is another path's problem

    def test_finalize_sends_the_luck_line_alone_and_the_failure_text_only_when_nothing_is_left(self):
        draft = SimpleNamespace(text="", private_controls=None)
        with patch.object(reply_pipeline.turn_delivery, "finalize", return_value=("骰值 63 還在等 Luck 決定。", [])):
            reply_pipeline._finalize(SimpleNamespace(message=None), draft)
        self.assertEqual(draft.text, "骰值 63 還在等 Luck 決定。")
        with patch.object(reply_pipeline.turn_delivery, "finalize", return_value=("", [])):
            reply_pipeline._finalize(SimpleNamespace(message=None), draft)
        self.assertEqual(draft.text, turn_fallback.guidance("narration_failure"))


def _event(name: str, ok: bool = True, *, changed: bool = True) -> dict:
    return {"name": name, "arguments": {}, "result": {"ok": ok},
            "gameplay_before": {"round": 1}, "gameplay_after": {"round": 2 if changed else 1}}


class PassedTheTurnTests(unittest.TestCase):
    def test_an_advance_that_changed_the_battle_is_a_passed_turn(self):
        self.assertTrue(executor._passed_the_turn([_event("declare_combat_action", ok=False, changed=False),
                                                   _event("advance_combat_turn")]))

    def test_a_refused_or_unchanged_advance_and_other_tools_are_not(self):
        self.assertFalse(executor._passed_the_turn([_event("advance_combat_turn", ok=False)]))
        self.assertFalse(executor._passed_the_turn([_event("advance_combat_turn", changed=False)]))
        self.assertFalse(executor._passed_the_turn([_event("skill_check")]))
        held = _event("advance_combat_turn")
        held["result"]["pending"] = True  # held at a due check: the turn did not move on
        self.assertFalse(executor._passed_the_turn([held]))
        self.assertFalse(executor._passed_the_turn([]))


if __name__ == "__main__":
    unittest.main()
