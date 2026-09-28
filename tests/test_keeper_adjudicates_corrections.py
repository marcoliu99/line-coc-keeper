"""docs/specs/feature/keeper_adjudicates_corrections_design_spec.md.

When a group has no KP Assistant, the Keeper rules on narrative corrections
from system-held evidence only; the reporter's text is never evidence.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import scenario_templates
from app.commands.handlers import correct as correct_handler
from app.commands.handlers import system as system_handler
from app.models import Character, GroupState
from app.repositories.group_state import load_state, save_state
from app.services import correction_adjudication
from tests.provider_fakes import use_fake_provider


def _state() -> GroupState:
    state = GroupState(group_id="g", timeline_id="t1")
    ada = Character(name="Ada", owner_id="u1", character_id="char-ada", carried_items=["手電筒"])
    state.characters["u1"] = ada
    state.characters_by_id[ada.character_id] = ada
    state.active_character_id_by_user["u1"] = ada.character_id
    return state


def _report(issue: str = "守秘人說我拿到了一把手槍", excerpt: str = "你打開抽屜，裡面只有灰塵。") -> dict:
    return {
        "id": "r1", "target_message_id": "123456", "issue": issue, "reporter_id": "u1",
        "status": "pending", "timeline_id": "t1",
        "target_receipt": {"excerpt": excerpt, "state_revision": 3, "turn_id": "turn-1"},
    }


def _model(**ruling) -> SimpleNamespace:
    return SimpleNamespace(analyze_text=Mock(return_value=ruling))


class RulingTests(unittest.TestCase):
    def test_an_approval_claiming_an_item_nobody_holds_becomes_undecided(self):
        fake = _model(
            decision="approve", evidence=["narration"], reason="敘事漏寫了手槍",
            resolution="Ada 從抽屜拿到了一把手槍。",
            claims=[{"kind": "item", "name": "手槍", "investigator": "Ada"}],
        )
        with use_fake_provider(fake, role="analysis"):
            ruling = correction_adjudication.rule(_state(), _report())
        self.assertEqual(ruling.decision, "undecided")

    def test_invalid_model_output_is_undecided(self):
        for output in (None, "approve", {"decision": "probably", "evidence": ["narration"], "reason": "x"}):
            with self.subTest(output=output):
                fake = SimpleNamespace(analyze_text=Mock(return_value=output))
                with use_fake_provider(fake, role="analysis"):
                    self.assertEqual(correction_adjudication.rule(_state(), _report()).decision, "undecided")

    def test_a_ruling_must_cite_real_evidence_and_an_approval_needs_its_text(self):
        cases = {
            "no evidence": {"decision": "reject", "evidence": [], "reason": "x"},
            "cites the allegation": {"decision": "reject", "evidence": ["allegation"], "reason": "x"},
            "approve without text": {"decision": "approve", "evidence": ["narration"], "reason": "x", "resolution": ""},
            "approve text too long": {"decision": "approve", "evidence": ["narration"], "reason": "x", "resolution": "字" * 1001},
        }
        for name, output in cases.items():
            with self.subTest(case=name), use_fake_provider(_model(**output), role="analysis"):
                self.assertEqual(correction_adjudication.rule(_state(), _report()).decision, "undecided")

    def test_a_reject_backed_by_the_narration_stands(self):
        fake = _model(decision="reject", evidence=["narration", "sheet:Ada"], reason="敘事與角色卡一致")
        with use_fake_provider(fake, role="analysis"):
            ruling = correction_adjudication.rule(_state(), _report())
        self.assertEqual((ruling.decision, ruling.evidence), ("reject", ("narration", "sheet:Ada")))

    def test_an_approval_stands_only_on_public_state_that_exists(self):
        state = _state()
        state.characters["u1"].status_tags.append("受傷")
        state.known_clues += [{"text": "地下室的鑰匙", "visibility": "public"},
                              {"text": "教團首領是神父", "visibility": "kp_only"}]
        state.established_facts.append({"text": "圖書館晚上關門", "visibility": "public"})
        holds = [{"kind": "item", "name": "手電筒", "investigator": "Ada"},
                 {"kind": "status", "name": "受傷", "investigator": "Ada"},
                 {"kind": "clue", "name": "地下室的鑰匙"}, {"kind": "fact", "name": "圖書館晚上關門"}]
        cases = {"public state": (holds, "approve"),
                 "a kp-only clue": ([{"kind": "clue", "name": "教團首領是神父"}], "undecided"),
                 "someone else's item": ([{"kind": "item", "name": "手電筒", "investigator": "Bob"}], "undecided")}
        for name, (claims, expected) in cases.items():
            fake = _model(decision="approve", evidence=["narration", "sheet:Ada"], reason="敘事漏寫了手電筒",
                          resolution="Ada 受傷了，手上仍拿著手電筒和地下室的鑰匙；圖書館晚上關門。", claims=claims)
            with self.subTest(case=name), use_fake_provider(fake, role="analysis"):
                self.assertEqual(correction_adjudication.rule(state, _report()).decision, expected)

    def test_recent_log_and_scenario_passages_are_citable_evidence(self):
        state = _state()
        state.scenario_text = "劇本全文"
        state.log.append({"role": "assistant", "content": "抽屜裡只有灰塵。"})
        fake = _model(decision="reject", evidence=["log:1", "scenario:1"], reason="劇本寫抽屜是空的")
        rows = [{"page": 3, "text": "書房抽屜是空的。", "score": 1.0}]
        with use_fake_provider(fake, role="analysis"), \
                patch.object(scenario_templates, "search_for_state", return_value=(None, rows)):
            self.assertEqual(correction_adjudication.rule(state, _report()).decision, "reject")
        packet = fake.analyze_text.call_args.args[0]
        self.assertIn("[log:1] 抽屜裡只有灰塵。", packet)
        self.assertIn("[scenario:1] （第 3 頁）書房抽屜是空的。", packet)

    def test_malformed_output_or_a_provider_error_is_undecided(self):
        cases = {
            "evidence is not text": _model(decision="reject", evidence=[{"id": "narration"}], reason="x"),
            "claims is not a list": _model(**dict(_APPROVAL, claims="手電筒")),
            "provider error": SimpleNamespace(analyze_text=Mock(side_effect=RuntimeError("503"))),
        }
        for name, fake in cases.items():
            with self.subTest(case=name), use_fake_provider(fake, role="analysis"):
                self.assertEqual(correction_adjudication.rule(_state(), _report()).decision, "undecided")

    def test_an_approval_must_rest_on_state_its_text_names(self):
        cases = {
            "no claims": dict(_APPROVAL, claims=[]),
            "text doesn't name what it claims": dict(_APPROVAL, resolution="Ada 從抽屜拿到了手槍。"),
            "cites only the disputed narration": dict(_APPROVAL, evidence=["narration"]),
        }
        for name, output in cases.items():
            with self.subTest(case=name), use_fake_provider(_model(**output), role="analysis"):
                self.assertEqual(correction_adjudication.rule(_state(), _report()).decision, "undecided")

    def test_skill_and_characteristic_claims_must_match_the_sheet(self):
        state = _state()
        state.characters["u1"].skills["圖書館使用"] = 60
        state.characters["u1"].luck = 45
        cases = {
            "skill as on the sheet": ({"kind": "skill", "name": "圖書館使用", "value": 60}, "approve"),
            "skill raised": ({"kind": "skill", "name": "圖書館使用", "value": 80}, "undecided"),
            "luck as on the sheet": ({"kind": "stat", "name": "LUCK", "value": 45}, "approve"),
            "hp invented": ({"kind": "stat", "name": "HP", "value": 99}, "undecided"),
        }
        for name, (claim, expected) in cases.items():
            claim = dict(claim, investigator="Ada")
            output = dict(_APPROVAL, claims=[claim], resolution=f"Ada 的{claim['name']}是 {claim['value']}。")
            with self.subTest(case=name), use_fake_provider(_model(**output), role="analysis"):
                self.assertEqual(correction_adjudication.rule(state, _report()).decision, expected)

    def test_the_packet_has_the_receipt_the_location_and_the_log_around_the_turn(self):
        state = _state()
        state.narrative_locations["u1"] = "書房"
        state.log += [{"role": "assistant", "content": f"第 {n} 段"} for n in range(20)]
        state.log[5] = {"role": "assistant", "content": "前情。你打開抽屜，裡面只有灰塵。後續。"}
        fake = _model(decision="reject", evidence=["narration"], reason="x")
        with use_fake_provider(fake, role="analysis"):
            correction_adjudication.rule(state, _report())
        packet = fake.analyze_text.call_args.args[0]
        self.assertIn("第 3 版狀態", packet)
        self.assertIn("turn-1", packet)
        self.assertIn("書房", packet)
        self.assertIn("第 2 段", packet)
        self.assertIn("第 8 段", packet)
        self.assertNotIn("第 19 段", packet)

    def test_a_failed_scenario_search_still_rules_on_the_rest(self):
        state = _state()
        state.scenario_text = "劇本全文"
        fake = _model(decision="reject", evidence=["narration"], reason="x")
        with use_fake_provider(fake, role="analysis"), \
                patch.object(scenario_templates, "search_for_state", side_effect=RuntimeError("index down")):
            self.assertEqual(correction_adjudication.rule(state, _report()).decision, "reject")



def _open(report_id: str, reporter: str, status: str) -> dict:
    return {"id": report_id, "target_message_id": "123456", "issue": "x", "reporter_id": reporter,
            "status": status, "timeline_id": "t1"}


class CorrectCommandTests(unittest.IsolatedAsyncioTestCase):
    """The /coc correct handler with and without a KP Assistant."""

    def setUp(self):
        self.state = _state()
        for target, value in (
            (patch.object(correct_handler, "load_state", side_effect=lambda _cid: self.state), None),
            (patch("app.repositories.group_state.save_state", new_callable=Mock), "save"),
            (patch.object(correct_handler, "target_receipt", return_value={"excerpt": "敘事"}), None),
            (patch.object(correction_adjudication, "adjudicate_pending", new_callable=AsyncMock), "adjudicate"),
        ):
            mock = target.start()
            self.addCleanup(target.stop)
            if value:
                setattr(self, value, mock)

    async def _run(self, user_id: str, text: str) -> list[str]:
        reply = AsyncMock()
        await correct_handler.handle_correct_command("g", user_id, reply, text.split())
        return [c.args[0] for c in reply.await_args_list]

    async def test_without_a_kp_a_new_report_or_a_list_asks_the_keeper_to_rule(self):
        await self._run("u1", "/coc correct 123456 劇本沒有地下室")
        await self._run("u1", "/coc correct list")
        self.assertEqual(self.adjudicate.call_count, 2)
        self.assertEqual(self.adjudicate.call_args.args[0], "g")

    async def test_with_a_kp_the_keeper_never_rules(self):
        self.state.kp_assistant_user_id = "kp"
        await self._run("u1", "/coc correct 123456 劇本沒有地下室")
        await self._run("kp", "/coc correct list")
        self.adjudicate.assert_not_called()

    async def test_unverified_reports_dont_fill_the_pending_limits(self):
        self.state.narrative_corrections += [_open(f"o{n}", f"other{n}", "unverified") for n in range(12)]
        self.state.narrative_corrections += [_open(f"u{n}", "u1", "unverified") for n in range(2)]
        replies = await self._run("u1", "/coc correct 123456 劇本沒有地下室")
        self.assertIn("已收到", replies[0])

    async def test_three_unverified_reports_block_only_that_reporter(self):
        self.state.narrative_corrections += [_open(f"u{n}", "u1", "unverified") for n in range(3)]
        refused = await self._run("u1", "/coc correct 123456 劇本沒有地下室")
        accepted = await self._run("u2", "/coc correct 123456 劇本沒有閣樓")
        self.assertIn("未能證實", refused[0])
        self.assertIn("已收到", accepted[0])
        self.assertEqual(sum(r["status"] == "pending" for r in self.state.narrative_corrections), 1)

    async def test_an_unverified_report_stays_listed_withdrawable_and_rulable_by_a_later_kp(self):
        self.state.narrative_corrections += [_open("a", "u1", "unverified"), _open("b", "u1", "unverified")]
        listed = await self._run("u1", "/coc correct list")
        self.assertIn("#a｜unverified", listed[0])
        await self._run("u1", "/coc correct withdraw a")
        self.state.kp_assistant_user_id = "kp"
        await self._run("kp", "/coc correct approve b Ada 手上沒有手槍。")
        self.assertEqual([r["status"] for r in self.state.narrative_corrections], ["withdrawn", "approved"])

    async def test_a_reporter_never_holds_more_than_three_unverified(self):
        self.state.narrative_corrections += [_open("u0", "u1", "unverified"), _open("u1", "u1", "unverified"),
                                             _open("p0", "u1", "pending")]
        refused = await self._run("u1", "/coc correct 123456 劇本沒有地下室")
        self.assertIn("未能證實", refused[0])

    async def test_a_later_kp_can_hold_an_unverified_report(self):
        self.state.kp_assistant_user_id = "kp"
        self.state.narrative_corrections.append(_open("a", "u1", "unverified"))
        await self._run("kp", "/coc correct hold a 地下室")
        self.assertEqual(self.state.narrative_corrections[0]["hold_scope"], ["地下室"])

    async def test_without_a_kp_nobody_may_hold_or_supersede(self):
        self.state.narrative_corrections += [_open("a", "u1", "pending"), _open("b", "u1", "approved"),
                                             _open("c", "u1", "approved")]
        held = await self._run("u1", "/coc correct hold a 地下室")
        superseded = await self._run("u1", "/coc correct supersede b c")
        self.assertIn("只有 KP", held[0])
        self.assertIn("只有 KP", superseded[0])
        self.assertEqual([r["status"] for r in self.state.narrative_corrections], ["pending", "approved", "approved"])

    async def test_pruning_keeps_unverified_reports(self):
        self.state.kp_assistant_user_id = "kp"
        self.state.narrative_corrections += [_open("keep", "u1", "unverified"), _open("r", "u2", "pending")]
        await self._run("kp", "/coc correct reject r")
        self.assertIn("keep", [r["id"] for r in self.state.narrative_corrections])



_APPROVAL = {"decision": "approve", "evidence": ["narration", "sheet:Ada"], "reason": "Ada 的角色卡上有手電筒",
                 "resolution": "Ada 手上一直拿著手電筒。", "claims": [{"kind": "item", "name": "手電筒", "investigator": "Ada"}]}


class AdjudicatePendingTests(unittest.IsolatedAsyncioTestCase):
    """The Keeper's background ruling, through the real state repository."""

    def setUp(self):
        self._seed(f"keeper-rules-{self._testMethodName}")

    def _seed(self, group: str) -> None:
        self.group = group
        state = _state()
        state.group_id = group
        state.narrative_corrections.append(_report())
        save_state(state)

    async def _adjudicate(self, fake) -> list[str]:
        reply = AsyncMock()
        with use_fake_provider(fake, role="analysis"):
            await correction_adjudication.adjudicate_pending(self.group, reply)
        return [c.args[0] for c in reply.await_args_list]

    async def test_an_approval_is_stored_as_the_keepers_ruling_and_posted(self):
        with patch.object(correction_adjudication.observability, "event") as event:
            posted = await self._adjudicate(_model(**_APPROVAL))
        state = load_state(self.group)
        report = state.narrative_corrections[0]
        self.assertEqual((report["status"], report["adjudicated_by"]), ("approved", "keeper"))
        self.assertEqual(report["resolution"], "Ada 手上一直拿著手電筒。")
        self.assertIn("已由守秘人依證據更正：Ada 手上一直拿著手電筒。", state.log[-1]["content"])
        self.assertEqual(len(posted), 1)
        self.assertIn("Ada 的角色卡上有手電筒", posted[0])
        ruling = next(c for c in event.call_args_list if c.args[0] == "correction.keeper_ruling")
        self.assertEqual(ruling.kwargs["decision"], "approve")
        self.assertNotIn("手槍", str(ruling.kwargs))  # the reporter's text never goes into the log


    async def test_undecided_marks_the_report_unverified_and_says_so(self):
        posted = await self._adjudicate(_model(decision="undecided", evidence=[], reason=""))
        self.assertEqual(load_state(self.group).narrative_corrections[0]["status"], "unverified")
        self.assertIn("無法依現有證據證實", posted[0])

    async def test_with_a_kp_the_keeper_does_not_rule(self):
        state = load_state(self.group)
        state.kp_assistant_user_id = "kp"
        save_state(state)
        fake = _model(**_APPROVAL)
        self.assertEqual(await self._adjudicate(fake), [])
        fake.analyze_text.assert_not_called()

    async def test_a_change_while_the_keeper_deliberates_discards_the_ruling(self):
        def register_kp(state):
            state.kp_assistant_user_id = "kp"

        def withdraw(state):
            state.narrative_corrections[0]["status"] = "withdrawn"

        def new_timeline(state):
            state.timeline_id = "t2"

        for change in (register_kp, withdraw, new_timeline):
            with self.subTest(change=change.__name__):
                self._seed(f"keeper-rules-race-{change.__name__}")

                def meanwhile(*_args, change=change):
                    current = load_state(self.group)
                    change(current)
                    save_state(current)
                    return _APPROVAL

                fake = SimpleNamespace(analyze_text=Mock(side_effect=meanwhile))
                self.assertEqual(await self._adjudicate(fake), [])
                after = load_state(self.group)
                self.assertNotIn("approved", [r["status"] for r in after.narrative_corrections])
                self.assertEqual(after.log, [])

    async def test_the_public_post_never_carries_a_protected_term(self):
        state = load_state(self.group)
        state.known_clues.append({"text": "教團首領是神父", "visibility": "kp_only"})
        save_state(state)
        leaky = dict(_APPROVAL, decision="reject", reason="劇本說教團首領是神父，所以抽屜本來就空")
        posted = await self._adjudicate(_model(**leaky))
        self.assertEqual(posted, ["敘事異議 #r1 經守秘人依證據核對後不成立。"])
        self.assertEqual(load_state(self.group).narrative_corrections[0]["status"], "rejected")

    async def test_a_later_kp_can_supersede_the_keepers_ruling(self):
        await self._adjudicate(_model(**_APPROVAL))
        state = load_state(self.group)
        state.kp_assistant_user_id = "kp"
        state.narrative_corrections.append(dict(_report(), id="r2"))
        save_state(state)
        reply = AsyncMock()
        await correct_handler.handle_correct_command(self.group, "kp", reply, ["/coc", "correct", "approve", "r2", "Ada", "沒有手電筒。"])
        await correct_handler.handle_correct_command(self.group, "kp", reply, ["/coc", "correct", "supersede", "r1", "r2"])
        statuses = {r["id"]: r["status"] for r in load_state(self.group).narrative_corrections}
        self.assertEqual(statuses, {"r1": "superseded", "r2": "approved"})

    async def test_a_report_left_by_a_restart_is_ruled_on_the_next_list_exactly_once(self):
        fake = _model(**_APPROVAL)
        with use_fake_provider(fake, role="analysis"):
            for _ in range(2):
                await correct_handler.handle_correct_command(self.group, "u1", AsyncMock(), ["/coc", "correct", "list"])
                await asyncio.gather(*(t for t in asyncio.all_tasks() if t is not asyncio.current_task()))
        fake.analyze_text.assert_called_once()
        self.assertEqual(load_state(self.group).narrative_corrections[0]["status"], "approved")

    async def test_a_report_ruled_meanwhile_is_not_sent_to_the_model_again(self):
        state = load_state(self.group)
        state.narrative_corrections.append(dict(_report(), id="r2"))
        save_state(state)

        def rule_r1_and_meanwhile_r2(*_args):
            current = load_state(self.group)
            current.narrative_corrections[1]["status"] = "rejected"  # another run ruled r2
            save_state(current)
            return _APPROVAL

        fake = SimpleNamespace(analyze_text=Mock(side_effect=rule_r1_and_meanwhile_r2))
        await self._adjudicate(fake)
        fake.analyze_text.assert_called_once()

    async def test_overlapping_triggers_rule_on_a_report_once(self):
        fake = _model(**_APPROVAL)
        reply = AsyncMock()
        with use_fake_provider(fake, role="analysis"):
            await asyncio.gather(correction_adjudication.adjudicate_pending(self.group, reply),
                                 correction_adjudication.adjudicate_pending(self.group, reply))
        fake.analyze_text.assert_called_once()
        reply.assert_awaited_once()



class KpQuitTests(unittest.TestCase):
    def _quit(self, state: GroupState) -> Mock:
        with patch.object(system_handler, "load_state", return_value=state), \
                patch.object(system_handler, "save_state"), \
                patch.object(correction_adjudication, "adjudicate_pending", new_callable=AsyncMock) as adjudicate:
            asyncio.run(system_handler.handle_system_command(
                "g", "kp", AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), ["/coc", "kp", "quit"],
                lambda owner_id: f"<@{owner_id}>",
            ))
        return adjudicate

    def test_the_keeper_takes_over_open_reports_when_the_kp_quits(self):
        state = _state()
        state.kp_assistant_user_id = "kp"
        state.narrative_corrections.append(_report())
        self.assertEqual(self._quit(state).call_args.args[0], "g")
        self.assertEqual(state.kp_assistant_user_id, "")

    def test_a_kp_quitting_with_nothing_pending_starts_nothing(self):
        state = _state()
        state.kp_assistant_user_id = "kp"
        state.narrative_corrections.append(dict(_report(), status="approved"))
        self._quit(state).assert_not_called()


if __name__ == "__main__":
    unittest.main()
