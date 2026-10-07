"""Tests for the check tools around NPC attacks and the combat RAG skip.

- offer_check_choice no longer takes an attacker_tier: a defense choice against an
  NPC attack is created by the managed combat, never by the Keeper before a fight.
- app/agents/context_builder.py skips its proactive scenario/memory RAG
  embedding calls while state.combat.active, since combat_block already
  carries the mechanical context combat narration needs.
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault(
    "app.pdf_loader",
    types.SimpleNamespace(
        extract_text=lambda pdf_bytes: ("", [], False, {}, {}),
        guess_title=lambda text, file_name="": file_name or "Untitled",
        extract_preview=lambda pdf_bytes: "",
    ),
)

from app import dice, observability, tool_dispatch
from app.commands.handlers import checks as check_commands
from app.models import Character, GroupState
from tests.state_store import StateStorePatch, clone_state


def _state_with_investigator() -> GroupState:
    state = GroupState(group_id="g")
    state.characters["u1"] = Character(
        name="小明", owner_id="u1", skills={"閃避": 45, "格鬥": 60, "射擊": 55}
    )
    return state


class OfferCheckChoiceTests(unittest.TestCase):


    def test_offer_check_choice_rejects_an_npc_attack_tier(self):
        """A defense choice against an NPC attack comes from the combat engine, so the
        Keeper cannot register one here (that would resolve the attack before a fight exists)."""
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(
                state, "offer_check_choice",
                {
                    "investigator": "小明",
                    "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                    "attacker_tier": "regular",
                },
                [], [], speaker_role="player",
            )
            self.assertFalse(result["ok"])
            self.assertEqual(store.store["g"].pending_checks, {})

    def test_npc_skill_check_and_offer_check_choice_still_work(self):
        """npc_skill_check and a plain offer_check_choice keep behaving as before."""
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            fake_roll = MagicMock(roll=10, tier="regular")
            with patch("app.dice.skill_check", return_value=fake_roll):
                npc_result = tool_dispatch.execute_tool(
                    state, "npc_skill_check", {"skill_value": 50}, [], [], speaker_role="player"
                )
                choice_result = tool_dispatch.execute_tool(
                    state,
                    "offer_check_choice",
                    {"investigator": "小明", "options": [{"label": "A", "skill": "閃避"}, {"label": "B", "skill": "格鬥"}]},
                    [],
                    [],
                    speaker_role="player",
                )
        self.assertEqual(npc_result, {"ok": True, "roll": 10, "tier": "regular", "skill_value": 50})
        self.assertTrue(choice_result["ok"])
        self.assertNotIn("attacker_tier", choice_result)  # not requested this time



class RangedDefenseEndToEndTests(unittest.TestCase):
    """docs/specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md end-to-end:
    a ranged attack's defense choice all the way through /coc check's
    resolution. Ranged combat is never dice.resolve_opposed — the attacker's
    shot is a standalone check, deferred until the defender's own
    dive-for-cover roll is known, with a penalty die added only if the dive
    succeeded."""


    def test_luck_decision_split_feedback_includes_ranged_shot_result(self):
        """Code-review regression: resolving a near-miss Luck decision
        through a Discord Luck button uses split_roll_feedback=True. The
        ranged attacker's shot IS rolled here (via ranged_opposed_text,
        exactly once per docstring), and _build_check_narration receives
        it, but the call building roll_feedback_text/keeper_header used to
        pass only result_line with no opposed_text at all — so the
        authoritative ranged outcome never reached the deterministic,
        player-facing split feedback, leaving it entirely dependent on
        whatever the generated Keeper narration happened to say."""
        state = _state_with_investigator()
        state.active = True
        # Real storage assigns a timeline on the first save, so a pending entry
        # that names one must be built against the stored value.
        state.timeline_id = "timeline-g"
        timeline_id = state.timeline_id
        state.pending_luck_decisions["u1"] = {
            "decision_id": "decision-1", "check_id": "check-1", "timeline_id": timeline_id,
            "origin_revision": state.state_revision + 1, "origin_turn_id": "", "origin_request_id": "",
            "created_at": "2026-01-01T00:00:00+00:00", "action_context": "撲向掩體",
            "skill_name": "閃避", "display_label": "閃避",
            "value": 45, "roll": 30, "bonus_dice": 0, "penalty_dice": 0,
            "original_tier": "regular", "attacker_tier": None, "difficulty": "regular",
            "options": [], "major_wound_trigger": False,
            "ranged_attacker": {"skill_value": 55, "bonus_dice": 0, "penalty_dice": 0},
        }
        with StateStorePatch(check_commands) as store:
            store.put(state)
            attacker_hit_roll = dice.SkillCheckResult(
                skill_value=55, roll=30, bonus_dice=0, penalty_dice=0,
                tier="regular", success=True, required_tier="regular",
            )
            with patch("app.dice.skill_check", return_value=attacker_hit_roll):
                resolution = check_commands.resolve_luck("g", "u1", "skip")

        self.assertIn("遠程攻擊判定", resolution.roll_feedback_text)
        self.assertIn("命中了", resolution.roll_feedback_text)


class AlreadyPendingCheckGuardTests(unittest.TestCase):
    """Ordinary checks follow the group's player-owned/autoroll policy."""

    def _options(self):
        return [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}]

    def test_skill_check_autoroll_is_immediate_and_does_not_create_pending(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        with StateStorePatch() as store:
            store.put(state)
            fake_roll = MagicMock(roll=99, tier="fumble", required_tier="regular", success=False)
            with patch("app.dice.skill_check", return_value=fake_roll):
                first = tool_dispatch.execute_tool(
                    state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
                )
                second = tool_dispatch.execute_tool(
                    state, "skill_check", {"investigator": "小明", "skill": "格鬥"}, [], [], speaker_role="player"
                )
            saved_state = store.store["g"]
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(first["resolved"])
        self.assertTrue(second["resolved"])
        self.assertEqual(saved_state.pending_checks, {})
        self.assertEqual([event["skill"] for event in saved_state.resolved_check_events], ["閃避", "格鬥"])

    def test_sanity_check_autoroll_is_immediate_and_does_not_create_pending(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        with StateStorePatch() as store:
            store.put(state)
            first = tool_dispatch.execute_tool(
                state, "sanity_check", {"investigator": "小明", "loss_success": "0", "loss_failure": "1d4"},
                [], [], speaker_role="player",
            )
            second = tool_dispatch.execute_tool(
                state, "sanity_check", {"investigator": "小明", "loss_success": "1", "loss_failure": "1d6"},
                [], [], speaker_role="player",
            )
            saved_state = store.store["g"]
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(first["resolved"])
        self.assertTrue(second["resolved"])
        self.assertEqual(saved_state.pending_checks, {})
        self.assertEqual([event["skill"] for event in saved_state.resolved_check_events], ["SAN", "SAN"])
        self.assertTrue(all("check_id" in event for event in saved_state.resolved_check_events))

    def test_character_checks_default_to_player_pending(self):
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            with patch("app.dice.skill_check") as roll_mock:
                result = tool_dispatch.execute_tool(
                    state,
                    "skill_check",
                    {"investigator": "小明", "skill": "射擊"},
                    [],
                    [],
                    speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])
        self.assertNotIn("resolved", result)
        self.assertEqual(saved_state.pending_checks["u1"]["skill"], "射擊")
        roll_mock.assert_not_called()

    def test_sanity_check_defaults_to_player_pending(self):
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            with patch("app.dice.sanity_check") as roll_mock:
                result = tool_dispatch.execute_tool(
                    state,
                    "sanity_check",
                    {"investigator": "小明", "loss_success": "0", "loss_failure": "1d4"},
                    [],
                    [],
                    speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])
        self.assertEqual(saved_state.pending_checks["u1"]["type"], "sanity")
        self.assertEqual(saved_state.characters["u1"].san, 50)
        roll_mock.assert_not_called()

    def test_sanity_check_rejects_when_a_luck_decision_is_still_pending(self):
        """Code-review finding: non-autoroll skill_check/sanity_check never
        checked pending_luck_decisions (only the autoroll branch did) — a
        character could end up with two independent unresolved states at
        once (e.g. autoroll got turned off mid-decision). sanity_check goes
        through check_lifecycle.register, which needs the same
        guard the autoroll branch already has."""
        state = _state_with_investigator()
        state.pending_luck_decisions["u1"] = {"options": []}
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(
                state,
                "sanity_check",
                {"investigator": "小明", "loss_success": "0", "loss_failure": "1d4"},
                [],
                [],
                speaker_role="player",
            )
        self.assertFalse(result["ok"])
        self.assertIn("Luck", result["error"])

    def test_adjust_character_major_wound_defaults_to_player_pending(self):
        state = _state_with_investigator()
        state.characters["u1"].hp = 10
        state.characters["u1"].hp_max = 10
        with StateStorePatch() as store:
            store.put(state)
            with patch("app.dice.skill_check") as roll_mock:
                result = tool_dispatch.execute_tool(
                    state,
                    "adjust_character",
                    {"investigator": "小明", "field": "hp", "delta": -5},
                    [],
                    [],
                    speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["major_wound"])
        self.assertIsNone(result["major_wound_check"])
        self.assertEqual(saved_state.pending_checks["u1"]["skill"], "CON")
        self.assertEqual(saved_state.characters["u1"].hp, 5)
        roll_mock.assert_not_called()

    def test_adjust_character_rejects_major_wound_while_check_is_pending(self):
        """A per-owner pending-check slot cannot hold both checks; reject the
        hit atomically so its mandatory major-wound CON check is not lost."""
        state = _state_with_investigator()
        state.characters["u1"].hp = 10
        state.characters["u1"].hp_max = 10
        state.pending_checks["u1"] = {
            "type": "skill", "skill": "偵查", "skill_value": 50,
            "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular", "pushed": False,
        }
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(
                state,
                "adjust_character",
                {"investigator": "小明", "field": "hp", "delta": -5},
                [],
                [],
                speaker_role="player",
            )
            saved_state = store.store["g"]
        self.assertFalse(result["ok"])
        self.assertIn("本次傷害未套用", result["error"])
        # Neither HP nor the pre-existing pending check changes; the Keeper
        # can retry this damage after resolving the existing check.
        self.assertEqual(saved_state.pending_checks["u1"]["skill"], "偵查")
        self.assertEqual(saved_state.characters["u1"].hp, 10)

    def test_sanity_check_autoroll_resolves_san_and_madness_immediately(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        state.characters["u1"].san = 60
        san_result = dice.SanityCheckResult(
            check=dice.SkillCheckResult(
                skill_value=60,
                roll=88,
                bonus_dice=0,
                penalty_dice=0,
                tier="fail",
                success=False,
            ),
            san_before=60,
            san_after=54,
            loss=6,
            loss_expression="1d6",
            risk_of_madness=True,
        )
        int_result = dice.SkillCheckResult(
            skill_value=50,
            roll=20,
            bonus_dice=0,
            penalty_dice=0,
            tier="hard",
            success=True,
        )
        with StateStorePatch() as store:
            store.put(state)
            with patch("app.dice.sanity_check", return_value=san_result), \
                    patch("app.dice.skill_check", return_value=int_result), \
                    patch("app.dice.roll_madness", return_value={
                        "roll": 3, "symptom": "暴力衝動", "duration": "3 輪", "guidance": "",
                    }):
                result = tool_dispatch.execute_tool(
                    state,
                    "sanity_check",
                    {"investigator": "小明", "loss_success": "0", "loss_failure": "1d6"},
                    [],
                    [],
                    speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertTrue(result["resolved"])
        self.assertEqual(result["san_after"], 54)
        self.assertEqual(result["madness_int_check"]["roll"], 20)
        self.assertEqual(result["madness"]["symptom"], "暴力衝動")
        self.assertEqual(saved_state.characters["u1"].san, 54)
        self.assertEqual(saved_state.pending_checks, {})

    def test_attack_skill_check_autoroll_is_system_owned(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        fake_roll = MagicMock(roll=22, tier="hard", required_tier="regular", success=True)
        with StateStorePatch() as store:
            store.put(state)
            with patch("app.dice.skill_check", return_value=fake_roll):
                result = tool_dispatch.execute_tool(
                    state,
                    "skill_check",
                    {"investigator": "小明", "skill": "射擊", "action_context": "小明瞄準怪物"},
                    [],
                    [],
                    speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertTrue(result["resolved"])
        self.assertEqual(result["roll"], 22)
        self.assertNotIn("/coc check", result["note"])
        self.assertEqual(saved_state.pending_checks, {})

    def test_same_turn_retry_reuses_the_authoritative_roll(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        fake_roll = MagicMock(roll=37, tier="regular", required_tier="regular", success=True)
        tool_input = {"investigator": "小明", "skill": "射擊", "action_context": "瞄準"}
        with StateStorePatch() as store:
            store.put(state)
            with observability.context(turn_id="turn-retry"), patch(
                "app.dice.skill_check", return_value=fake_roll
            ) as roll_mock:
                first = tool_dispatch.execute_tool(state, "skill_check", tool_input, [], [], speaker_role="player")
                second = tool_dispatch.execute_tool(state, "skill_check", tool_input, [], [], speaker_role="player")
        self.assertEqual(first["roll"], second["roll"])
        self.assertEqual(first["check_id"], second["check_id"])
        roll_mock.assert_called_once()

    def test_autoroll_retry_after_luck_resolution_never_rerolls(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        fake_roll = MagicMock(roll=67, tier="failure", required_tier="regular", success=False)
        tool_input = {"investigator": "小明", "skill": "射擊", "action_context": "瞄準"}
        with StateStorePatch() as store:
            store.put(state)
            with observability.context(turn_id="turn-luck-complete"), patch(
                "app.dice.skill_check", return_value=fake_roll
            ) as roll_mock, patch(
                "app.luck.buyable_options", return_value=[MagicMock(tier="regular", cost=12)]
            ):
                first = tool_dispatch.execute_tool(state, "skill_check", tool_input, [], [], speaker_role="player")
                settled = clone_state(store.store["g"])
                settled.pending_luck_decisions.clear()
                store.put(settled)
                second = tool_dispatch.execute_tool(state, "skill_check", tool_input, [], [], speaker_role="player")
        assert first["pending_luck"]
        assert not second["ok"]
        roll_mock.assert_called_once()

    def test_skill_check_rejects_when_a_luck_decision_is_still_pending(self):
        """Code-review finding: the non-autoroll skill_check branch only
        checked pending_checks before registering a new one — never
        pending_luck_decisions, unlike the autoroll branch a few lines
        below it in the same function. A character could end up with two
        independent unresolved states (a fresh pending check AND a stale
        Luck decision) at once."""
        state = _state_with_investigator()
        state.pending_luck_decisions["u1"] = {"options": []}
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player",
            )
        self.assertFalse(result["ok"])
        self.assertIn("Luck", result["error"])

    def test_check_command_does_not_start_a_new_skill_roll(self):
        state = _state_with_investigator()
        state.active = True
        with StateStorePatch(check_commands) as store:
            store.put(state)
            resolution = check_commands.resolve_check("g", "u1", "/coc check 射擊")
        self.assertFalse(resolution.should_finalize)
        self.assertIn("玩家用 /coc check 或按鈕擲骰", resolution.reply_text)

    def test_offer_check_choice_preserves_unresolved_luck(self):
        state = _state_with_investigator()
        state.pending_luck_decisions["u1"] = {"decision_id": "old-luck", "options": []}
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(
                state, "offer_check_choice", {"investigator": "小明", "options": self._options()},
                [], [], speaker_role="player",
            )
            saved = store.store["g"]
        self.assertFalse(result["ok"])
        self.assertIn("Luck", result["error"])
        self.assertEqual(saved.pending_luck_decisions["u1"]["decision_id"], "old-luck")
        self.assertNotIn("u1", saved.pending_checks)

    def test_offer_check_choice_reuses_identical_pending_request(self):
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            first = tool_dispatch.execute_tool(
                state, "offer_check_choice", {"investigator": "小明", "options": self._options()},
                [], [], speaker_role="player",
            )
            second = tool_dispatch.execute_tool(
                state, "offer_check_choice", {"investigator": "小明", "options": self._options()},
                [], [], speaker_role="player",
            )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertIn("防重複", second["note"])

    def test_offer_check_choice_rejects_different_pending_request(self):
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            first = tool_dispatch.execute_tool(
                state, "offer_check_choice", {"investigator": "小明", "options": self._options()},
                [], [], speaker_role="player",
            )
            second = tool_dispatch.execute_tool(
                state,
                "offer_check_choice",
                {"investigator": "小明", "options": [{"label": "閃避", "skill": "閃避"}, {"label": "射擊", "skill": "射擊"}]},
                [], [], speaker_role="player",
            )
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])


    def test_blocked_unknown_skill_does_not_change_character_card(self):
        state = _state_with_investigator()
        state.pending_luck_decisions["u1"] = {"decision_id": "old", "options": []}
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(state, "skill_check", {
                "investigator": "小明", "skill": "自訂古語",
            }, [], [], speaker_role="player")
        assert not result["ok"]
        assert "自訂古語" not in state.characters["u1"].skills
        assert "自訂古語" not in store.store["g"].characters["u1"].skills


    def test_different_investigators_are_independent(self):
        """The guard is scoped per investigator (per owner_id) — one
        player's pending check must not block a different player's."""
        state = _state_with_investigator()
        state.characters["u2"] = Character(name="小華", owner_id="u2", skills={"閃避": 40, "格鬥": 50})
        with StateStorePatch() as store:
            store.put(state)
            first = tool_dispatch.execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
            )
            second = tool_dispatch.execute_tool(
                state, "skill_check", {"investigator": "小華", "skill": "格鬥"}, [], [], speaker_role="player"
            )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])


class ClearPendingCheckTests(unittest.TestCase):
    """clear_pending_check remains an escape hatch for old snapshots."""

    def test_clears_an_existing_pending_check(self):
        state = _state_with_investigator()
        state.pending_checks["u1"] = {"type": "skill", "skill": "閃避", "skill_value": 45}
        with StateStorePatch() as store:
            store.put(state)
            tool_dispatch.execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
            )
            result = tool_dispatch.execute_tool(
                state, "clear_pending_check", {"investigator": "小明"}, [], [], speaker_role="player"
            )
            saved_state = store.store["g"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["cleared"])
        self.assertEqual(result["cleared_check_type"], "skill")
        self.assertNotIn("u1", saved_state.pending_checks)

    def test_no_pending_check_is_a_safe_noop(self):
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(
                state, "clear_pending_check", {"investigator": "小明"}, [], [], speaker_role="player"
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["cleared"])

    def test_unknown_investigator_returns_error(self):
        state = _state_with_investigator()
        with StateStorePatch() as store:
            store.put(state)
            result = tool_dispatch.execute_tool(
                state, "clear_pending_check", {"investigator": "不存在的人"}, [], [], speaker_role="player"
            )
        self.assertFalse(result["ok"])

    def test_clearing_unblocks_a_new_check(self):
        """The escape hatch remains for pending checks from old snapshots."""
        state = _state_with_investigator()
        state.pending_checks["u1"] = {"type": "skill", "skill": "閃避", "skill_value": 45}
        with StateStorePatch() as store:
            store.put(state)
            tool_dispatch.execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
            )
            blocked = tool_dispatch.execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "格鬥"}, [], [], speaker_role="player"
            )
            tool_dispatch.execute_tool(
                state, "clear_pending_check", {"investigator": "小明"}, [], [], speaker_role="player"
            )
            after_clear = tool_dispatch.execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "格鬥"}, [], [], speaker_role="player"
            )
        self.assertFalse(blocked["ok"])
        self.assertTrue(after_clear["ok"])
        self.assertEqual(after_clear["skill"], "格鬥")


class ContextBuilderCombatRagSkipTests(unittest.IsolatedAsyncioTestCase):
    def _state(self, *, combat_active: bool) -> GroupState:
        state = GroupState(group_id="g")
        state.scenario_text = "some scenario text"
        state.scenario_title = "Some Title"
        state.characters["u1"] = Character(name="小明", owner_id="u1")
        state.combat.active = combat_active
        return state

    async def test_skips_both_proactive_rag_calls_during_active_combat(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
             patch.object(scenario_rag, "get_index") as mock_get_index, \
             patch.object(scenario_rag, "search") as mock_search, \
             patch.object(memory_rag, "search_memory") as mock_search_memory:
            message = await context_builder.build_context(
                state=self._state(combat_active=True), user_id="u1", display_name="小明", text="我攻擊怪物",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        mock_get_index.assert_not_called()
        mock_search.assert_not_called()
        mock_search_memory.assert_not_called()
        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["memory_context"], "")

    async def test_runs_both_proactive_rag_calls_when_combat_not_active(self):
        """Regression: outside combat, behavior is unchanged from before —
        matches tests/test_agentic_pipeline.py's existing gating tests."""
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
             patch.object(scenario_rag, "get_index", return_value="fake-index") as mock_get_index, \
             patch.object(scenario_rag, "search", return_value=[]) as mock_search, \
             patch.object(scenario_rag, "format_results", return_value=""), \
             patch.object(memory_rag, "search_memory", return_value=[]) as mock_search_memory, \
             patch.object(memory_rag, "format_results", return_value=""):
            await context_builder.build_context(
                state=self._state(combat_active=False), user_id="u1", display_name="小明", text="我四處看看",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        mock_get_index.assert_called_once()
        mock_search.assert_called_once()
        mock_search_memory.assert_called_once()


if __name__ == "__main__":
    unittest.main()
