"""Tests for docs/npc_attack_latency_design_spec.md's two optimizations:

- app/keeper.py's new offer_npc_attack_defense_choice tool, which merges
  what used to be two sequential tool calls (npc_skill_check, then
  offer_check_choice with attacker_tier filled in from its result) into
  one — cutting one LLM round-trip off the most common combat exchange
  (NPC attacks, player picks dodge/counter).
- app/agents/context_builder.py skipping its proactive scenario/memory RAG
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

from app import dice, keeper, legacy_commands
from app.models import Character, GroupState


def clone_state(state: GroupState) -> GroupState:
    return GroupState.from_dict(state.to_dict())


class StateStorePatch:
    def __init__(self, *modules) -> None:
        self.modules = modules
        self.store: dict[str, GroupState] = {}
        self.originals = []

    def __enter__(self):
        def load_state(group_id: str) -> GroupState:
            return clone_state(self.store.get(group_id, GroupState(group_id=group_id)))

        def save_state(state: GroupState, *, reason: str = "command") -> None:
            self.store[state.group_id] = clone_state(state)

        for module in self.modules:
            self.originals.append((module, module.load_state, module.save_state))
            module.load_state = load_state
            module.save_state = save_state
        return self

    def __exit__(self, exc_type, exc, tb):
        for module, load_state, save_state in reversed(self.originals):
            module.load_state = load_state
            module.save_state = save_state

    def put(self, state: GroupState) -> None:
        self.store[state.group_id] = clone_state(state)


def _state_with_investigator() -> GroupState:
    state = GroupState(group_id="g")
    state.characters["u1"] = Character(name="小明", owner_id="u1", skills={"閃避": 45, "格鬥": 60})
    return state


class OfferNpcAttackDefenseChoiceTests(unittest.TestCase):
    def test_registers_pending_choice_with_system_rolled_attacker_tier(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            fake_roll = MagicMock(roll=42, tier="hard")
            with patch("app.keeper.dice.skill_check", return_value=fake_roll) as skill_check_mock:
                result = keeper._execute_tool(
                    state,
                    "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [
                            {"label": "閃避", "skill": "閃避"},
                            {"label": "反擊", "skill": "格鬥"},
                        ],
                        "attacker_skill_value": 55,
                        "attacker_bonus_dice": 1,
                    },
                    [],
                    [],
                    speaker_role="player",
                )
            saved_state = store.store["g"]

        skill_check_mock.assert_called_once_with(55, bonus_dice=1, penalty_dice=0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])
        self.assertEqual(result["attacker_roll"], 42)
        self.assertEqual(result["attacker_tier"], "hard")
        self.assertEqual(
            result["options"],
            [
                {"label": "閃避", "skill": "閃避", "skill_value": 45, "bonus_dice": 0, "penalty_dice": 0},
                {"label": "反擊", "skill": "格鬥", "skill_value": 60, "bonus_dice": 0, "penalty_dice": 0},
            ],
        )
        pending = saved_state.pending_checks["u1"]
        self.assertEqual(pending["type"], "choice")
        self.assertEqual(pending["attacker_tier"], "hard")
        self.assertEqual(pending["options"], result["options"])

    def test_unknown_investigator_returns_error_without_rolling(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check") as skill_check_mock:
                result = keeper._execute_tool(
                    state,
                    "offer_npc_attack_defense_choice",
                    {
                        "investigator": "不存在的人",
                        "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                        "attacker_skill_value": 50,
                    },
                    [],
                    [],
                    speaker_role="player",
                )
        self.assertFalse(result["ok"])
        skill_check_mock.assert_not_called()

    def test_zero_options_returns_error_without_rolling(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check") as skill_check_mock:
                result = keeper._execute_tool(
                    state,
                    "offer_npc_attack_defense_choice",
                    {"investigator": "小明", "options": [], "attacker_skill_value": 50},
                    [],
                    [],
                    speaker_role="player",
                )
        self.assertFalse(result["ok"])
        skill_check_mock.assert_not_called()

    def test_single_option_is_accepted_for_ranged_attacks(self):
        """COC7e: Fight Back isn't valid against a ranged attack, so a ranged
        attack's defense choice legitimately only has "Dodge" — one option,
        not two. Regression guard for the P2 Codex finding on PR #43."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            fake_roll = MagicMock(roll=10, tier="regular")
            with patch("app.keeper.dice.skill_check", return_value=fake_roll) as skill_check_mock:
                result = keeper._execute_tool(
                    state,
                    "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}],
                        "attacker_skill_value": 50,
                    },
                    [],
                    [],
                    speaker_role="player",
                )
            saved_state = store.store["g"]

        skill_check_mock.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["options"]), 1)
        self.assertEqual(saved_state.pending_checks["u1"]["options"], result["options"])

    def test_npc_skill_check_and_offer_check_choice_are_unaffected(self):
        """Regression: both original tools must keep behaving exactly as
        before — this is an additive change, not a replacement."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            fake_roll = MagicMock(roll=10, tier="regular")
            with patch("app.keeper.dice.skill_check", return_value=fake_roll):
                npc_result = keeper._execute_tool(
                    state, "npc_skill_check", {"skill_value": 50}, [], [], speaker_role="player"
                )
                choice_result = keeper._execute_tool(
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


class OfferNpcAttackDefenseChoiceEndToEndTests(unittest.TestCase):
    """Connects offer_npc_attack_defense_choice's pending_checks write all
    the way through to /coc check's actual resolution
    (legacy_commands._resolve_check_deterministically) — the two were only
    ever covered by separate, unconnected tests before (this tool's own
    pending_checks shape vs. the pre-existing choice+attacker_tier
    resolution logic), so nothing verified end-to-end that the "merge two
    tool calls into one" change didn't also change what the player
    actually experiences when they resolve it."""

    def test_choosing_fight_back_resolves_with_the_system_rolled_attacker_tier(self):
        state = _state_with_investigator()
        state.active = True
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=MagicMock(roll=1, tier="critical")):
                tool_result = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                        "attacker_skill_value": 70,
                    },
                    [], [], speaker_role="player",
                )
            self.assertTrue(tool_result["ok"])
            self.assertEqual(tool_result["attacker_tier"], "critical")

            defender_roll = dice.SkillCheckResult(
                skill_value=60, roll=50, bonus_dice=0, penalty_dice=0,
                tier="regular", success=True, required_tier="regular",
            )
            with patch("app.legacy_commands.dice.skill_check", return_value=defender_roll):
                resolution = legacy_commands._resolve_check_deterministically("g", "u1", "/coc check 反擊")
            saved_state = store.store["g"]

        # The choice check is fully consumed — not left dangling for a
        # future call to trip over (this is also what makes clear_pending_check
        # normally unnecessary here: resolving it the normal way already pops it).
        self.assertNotIn("u1", saved_state.pending_checks)
        # attacker_tier="critical" beats the defender's "regular" Fight Back
        # roll — COC7e opposed roll, higher tier wins — so the narration must
        # say the counter-attack failed to land, not that it succeeded.
        combined_text = resolution.roll_line + resolution.keeper_message
        self.assertIn("反擊沒有生效", combined_text)


class AlreadyPendingCheckGuardTests(unittest.TestCase):
    """Regression tests for 風險 1 in docs/npc_attack_latency_design_spec.md:
    skill_check/sanity_check/offer_check_choice/
    offer_npc_attack_defense_choice must all reject a second call for an
    investigator who already has an unresolved pending check, instead of
    silently overwriting it (and, for offer_npc_attack_defense_choice,
    silently discarding an already-rolled attacker check)."""

    def _options(self):
        return [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}]

    def test_skill_check_rejects_when_one_already_pending(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            first = keeper._execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
            )
            second = keeper._execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "格鬥"}, [], [], speaker_role="player"
            )
            saved_state = store.store["g"]
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        # The first pending check must survive untouched — still "閃避", not
        # overwritten by the rejected second call's "格鬥".
        self.assertEqual(saved_state.pending_checks["u1"]["skill"], "閃避")

    def test_sanity_check_rejects_when_one_already_pending(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            first = keeper._execute_tool(
                state, "sanity_check", {"investigator": "小明", "loss_success": "0", "loss_failure": "1d4"},
                [], [], speaker_role="player",
            )
            second = keeper._execute_tool(
                state, "sanity_check", {"investigator": "小明", "loss_success": "1", "loss_failure": "1d6"},
                [], [], speaker_role="player",
            )
            saved_state = store.store["g"]
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(saved_state.pending_checks["u1"]["loss_failure"], "1d4")

    def test_offer_check_choice_rejects_when_one_already_pending(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            first = keeper._execute_tool(
                state, "offer_check_choice", {"investigator": "小明", "options": self._options()},
                [], [], speaker_role="player",
            )
            second = keeper._execute_tool(
                state, "offer_check_choice", {"investigator": "小明", "options": self._options()},
                [], [], speaker_role="player",
            )
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])

    def test_offer_npc_attack_defense_choice_rejects_and_does_not_reroll(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            roll_calls = []

            def fake_skill_check(skill_value, *, bonus_dice=0, penalty_dice=0):
                roll_calls.append(skill_value)
                return MagicMock(roll=1, tier="critical")

            with patch("app.keeper.dice.skill_check", side_effect=fake_skill_check):
                first = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {"investigator": "小明", "options": self._options(), "attacker_skill_value": 50},
                    [], [], speaker_role="player",
                )
                second = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {"investigator": "小明", "options": self._options(), "attacker_skill_value": 99},
                    [], [], speaker_role="player",
                )
            saved_state = store.store["g"]
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        # The second call must be rejected before rolling — only the first
        # call's attacker_skill_value (50) should have reached dice.skill_check.
        self.assertEqual(roll_calls, [50])
        self.assertEqual(saved_state.pending_checks["u1"]["attacker_tier"], "critical")

    def test_different_investigators_are_independent(self):
        """The guard is scoped per investigator (per owner_id) — one
        player's pending check must not block a different player's."""
        state = _state_with_investigator()
        state.characters["u2"] = Character(name="小華", owner_id="u2", skills={"閃避": 40, "格鬥": 50})
        with StateStorePatch(keeper) as store:
            store.put(state)
            first = keeper._execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
            )
            second = keeper._execute_tool(
                state, "skill_check", {"investigator": "小華", "skill": "格鬥"}, [], [], speaker_role="player"
            )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])


class ClearPendingCheckTests(unittest.TestCase):
    """clear_pending_check — the escape hatch for a pending check that's
    gone stale (combat moved on, character left, etc.) and would otherwise
    permanently block that investigator from skill_check/sanity_check/
    offer_check_choice/offer_npc_attack_defense_choice forever, since
    nothing else in the codebase ever pops pending_checks except the
    player's own /coc check resolution."""

    def test_clears_an_existing_pending_check(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
            )
            result = keeper._execute_tool(
                state, "clear_pending_check", {"investigator": "小明"}, [], [], speaker_role="player"
            )
            saved_state = store.store["g"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["cleared"])
        self.assertEqual(result["cleared_check_type"], "skill")
        self.assertNotIn("u1", saved_state.pending_checks)

    def test_no_pending_check_is_a_safe_noop(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state, "clear_pending_check", {"investigator": "小明"}, [], [], speaker_role="player"
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["cleared"])

    def test_unknown_investigator_returns_error(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state, "clear_pending_check", {"investigator": "不存在的人"}, [], [], speaker_role="player"
            )
        self.assertFalse(result["ok"])

    def test_clearing_unblocks_a_new_check(self):
        """The actual point of this tool: without it, a stale pending check
        would permanently reject every future check request for that
        investigator (see AlreadyPendingCheckGuardTests)."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
            )
            blocked = keeper._execute_tool(
                state, "skill_check", {"investigator": "小明", "skill": "格鬥"}, [], [], speaker_role="player"
            )
            keeper._execute_tool(
                state, "clear_pending_check", {"investigator": "小明"}, [], [], speaker_role="player"
            )
            after_clear = keeper._execute_tool(
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
