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

from app import dice, keeper, legacy_commands, observability
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
    state.characters["u1"] = Character(
        name="小明", owner_id="u1", skills={"閃避": 45, "格鬥": 60, "射擊": 55}
    )
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

    def test_is_ranged_defers_the_attacker_roll_entirely(self):
        """docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md §2: a
        ranged attack is never an opposed roll, so the attacker must NOT be
        rolled at registration time — only after the defender's own dive-
        for-cover result is known (see legacy_commands._resolve_ranged_defense_outcome).
        Regression guard for the bug this rebuild fixed: ranged attacks used
        to be pre-rolled exactly like melee."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check") as skill_check_mock:
                result = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}],
                        "attacker_skill_value": 55,
                        "is_ranged": True,
                    },
                    [], [], speaker_role="player",
                )
            saved_state = store.store["g"]

        skill_check_mock.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertNotIn("attacker_tier", result)
        self.assertNotIn("attacker_roll", result)
        pending = saved_state.pending_checks["u1"]
        self.assertTrue(pending["is_ranged"])
        self.assertNotIn("attacker_tier", pending)
        self.assertNotIn("attacker_roll", pending)
        self.assertEqual(pending["attacker_skill_value"], 55)

    def test_ranged_attack_filters_out_fight_back_option_server_side(self):
        """Code-review regression: is_ranged used to register whatever
        options the LLM passed with no server-side filtering, contrary to
        prompt instructions that ranged attacks never offer Fight Back
        (COC7e allows no such thing against gunfire). If the LLM violated
        that instruction and a player picked "反擊" anyway, the is_ranged
        branch would resolve it as a dive-for-cover Dodge (see
        legacy_commands._resolve_ranged_defense_outcome) and narrate a
        result the player never actually chose — a rule-bypass class the PR
        explicitly guarded against for the melee-critical case but missed
        here."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check") as skill_check_mock:
                result = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                        "attacker_skill_value": 55,
                        "is_ranged": True,
                    },
                    [], [], speaker_role="player",
                )
            saved_state = store.store["g"]

        skill_check_mock.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual([o["label"] for o in result["options"]], ["閃避"])
        self.assertEqual(
            [o["label"] for o in saved_state.pending_checks["u1"]["options"]], ["閃避"]
        )

    def test_ranged_attack_with_only_a_fight_back_option_errors_without_saving(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check") as skill_check_mock:
                result = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "反擊", "skill": "格鬥"}],
                        "attacker_skill_value": 55,
                        "is_ranged": True,
                    },
                    [], [], speaker_role="player",
                )
            self.assertEqual(store.store["g"].pending_checks, {})

        skill_check_mock.assert_not_called()
        self.assertFalse(result["ok"])

    def test_retrying_with_is_ranged_corrected_does_not_reuse_the_stale_melee_entry(self):
        """Code-review regression: the reuse-on-duplicate-request branch
        compared attacker_skill_value/bonus/penalty/options but never
        is_ranged. If a firearm attack was first (mistakenly) registered
        with is_ranged=False — leaving a melee entry with a real
        attacker_roll already persisted — and the caller retries with only
        is_ranged flipped to True (same skill value, bonus/penalty,
        options), every other reuse condition still matched, so the stale
        melee opposed-roll result was silently returned instead of the
        retry actually switching to the ranged dive-for-cover mechanic.
        Must now fall through to the "already pending" rejection instead."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            fake_roll = MagicMock(roll=10, tier="regular")
            with patch("app.keeper.dice.skill_check", return_value=fake_roll):
                first = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}],
                        "attacker_skill_value": 55,
                        "is_ranged": False,
                    },
                    [], [], speaker_role="player",
                )
            self.assertTrue(first["ok"])
            self.assertEqual(first["attacker_tier"], "regular")

            with patch("app.keeper.dice.skill_check") as skill_check_mock:
                retry = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}],
                        "attacker_skill_value": 55,
                        "is_ranged": True,
                    },
                    [], [], speaker_role="player",
                )

        skill_check_mock.assert_not_called()
        self.assertFalse(retry["ok"])
        self.assertNotIn("attacker_tier", retry)

    def test_melee_defaults_is_ranged_to_false_and_still_pre_rolls(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            fake_roll = MagicMock(roll=10, tier="regular")
            with patch("app.keeper.dice.skill_check", return_value=fake_roll):
                result = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                        "attacker_skill_value": 50,
                    },
                    [], [], speaker_role="player",
                )
            saved_state = store.store["g"]

        self.assertFalse(saved_state.pending_checks["u1"]["is_ranged"])
        self.assertEqual(result["attacker_tier"], "regular")

    def test_critical_attacker_filters_out_fight_back_option(self):
        """docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md §4.2: no
        tier beats Critical, so offering Fight Back against it is an option
        that can never win — it must be filtered out server-side (not just
        hidden in the Discord button), since a Dodge tie still favors the
        defender (§1) and remains winnable."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=MagicMock(roll=1, tier="critical")):
                result = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                        "attacker_skill_value": 70,
                    },
                    [], [], speaker_role="player",
                )
            saved_state = store.store["g"]

        self.assertTrue(result["ok"])
        labels = [o["label"] for o in result["options"]]
        self.assertEqual(labels, ["閃避"])
        self.assertEqual([o["label"] for o in saved_state.pending_checks["u1"]["options"]], ["閃避"])

    def test_identical_retry_after_critical_filter_reuses_cached_roll(self):
        """Code-review regression: the dedup/reuse comparison used to check
        the RE-COMPUTED options (unfiltered, e.g. ["閃避","反擊"]) against
        what got PERSISTED (already filtered down to ["閃避"] by the
        critical-tier Fight Back removal above). Those two could never be
        equal, so an identical retry with the same raw request fell through
        to the generic "already pending" rejection instead of gracefully
        reusing the earlier attacker_roll/attacker_tier — defeating the
        whole point of the dedup branch specifically for the one case
        (critical attacker) it's most likely to be hit for."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            raw_call = {
                "investigator": "小明",
                "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                "attacker_skill_value": 70,
            }
            with patch("app.keeper.dice.skill_check", return_value=MagicMock(roll=1, tier="critical")):
                first = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice", raw_call, [], [], speaker_role="player",
                )
            self.assertTrue(first["ok"])

            with patch("app.keeper.dice.skill_check") as skill_check_mock:
                retry = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice", raw_call, [], [], speaker_role="player",
                )

        skill_check_mock.assert_not_called()
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["attacker_tier"], "critical")
        self.assertEqual([o["label"] for o in retry["options"]], ["閃避"])
        self.assertIn("note", retry)

    def test_critical_attacker_with_only_a_fight_back_option_errors_without_saving(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=MagicMock(roll=1, tier="critical")):
                result = keeper._execute_tool(
                    state, "offer_npc_attack_defense_choice",
                    {
                        "investigator": "小明",
                        "options": [{"label": "反擊", "skill": "格鬥"}],
                        "attacker_skill_value": 70,
                    },
                    [], [], speaker_role="player",
                )
            # should_save=False on the error path — the pre-existing (empty)
            # pending_checks must be untouched, not overwritten with a
            # partially-built choice entry.
            self.assertEqual(store.store["g"].pending_checks, {})
        self.assertFalse(result["ok"])

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

    def test_offer_check_choice_filters_fight_back_when_attacker_tier_is_critical(self):
        """Code-review regression: offer_npc_attack_defense_choice filters
        out Fight Back against a Critical attacker (§4.2 — nothing beats
        Critical), but the older two-step flow this tool replaced
        (npc_skill_check, then offer_check_choice with attacker_tier filled
        in by hand) never got the same filter, even though it's still a
        live, documented entry point. A Keeper still using that flow could
        offer a player a Fight Back option that's mathematically guaranteed
        to lose."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state, "offer_check_choice",
                {
                    "investigator": "小明",
                    "options": [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}],
                    "attacker_tier": "critical",
                },
                [], [], speaker_role="player",
            )
            saved_state = store.store["g"]

        self.assertTrue(result["ok"])
        self.assertEqual([o["label"] for o in result["options"]], ["閃避"])
        self.assertEqual(
            [o["label"] for o in saved_state.pending_checks["u1"]["options"]], ["閃避"]
        )

    def test_offer_check_choice_with_only_fight_back_and_critical_tier_errors_without_saving(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state, "offer_check_choice",
                {
                    "investigator": "小明",
                    "options": [{"label": "反擊", "skill": "格鬥"}, {"label": "其他", "skill": "偵查"}],
                    "attacker_tier": "critical",
                },
                [], [], speaker_role="player",
            )
        self.assertTrue(result["ok"])
        # Only "反擊" gets filtered — a non-Fight-Back second option survives.
        self.assertEqual([o["label"] for o in result["options"]], ["其他"])

    def test_offer_check_choice_critical_tier_with_only_fight_back_errors_without_saving(self):
        """offer_check_choice requires >=2 raw options up front, so to reach
        the "filtered down to zero" branch both options have to be Fight
        Back variants (an edge case, but the filter matches on substring
        "反擊" so this is what triggers it — not achievable with a single
        option, which the tool rejects before the filter ever runs)."""
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state, "offer_check_choice",
                {
                    "investigator": "小明",
                    "options": [
                        {"label": "反擊", "skill": "格鬥"},
                        {"label": "反擊（左手）", "skill": "格鬥"},
                    ],
                    "attacker_tier": "critical",
                },
                [], [], speaker_role="player",
            )
            self.assertEqual(store.store["g"].pending_checks, {})
        self.assertFalse(result["ok"])


class RangedDefenseEndToEndTests(unittest.TestCase):
    """docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md §2 end-to-end:
    a ranged offer_npc_attack_defense_choice all the way through /coc check's
    resolution. Ranged combat is never dice.resolve_opposed — the attacker's
    shot is a standalone check, deferred until the defender's own
    dive-for-cover roll is known, with a penalty die added only if the dive
    succeeded."""

    def test_successful_dive_gives_attacker_a_penalty_die(self):
        state = _state_with_investigator()
        state.active = True
        # luck=0 genuinely leaves no affordable buyable_options (real
        # app/luck.py math, not mocked) so the Luck buy-up decision — now
        # offered for ANY affordable tier-improving option, not just
        # near-misses, see docs/specs/enhancement-luck-buyup-always-
        # offered.md — can't trigger and interrupt what this test actually
        # means to exercise: the ranged attacker's penalty-die carry-through.
        state.characters["u1"].luck = 0
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            keeper._execute_tool(
                state, "offer_npc_attack_defense_choice",
                {
                    "investigator": "小明",
                    "options": [{"label": "閃避", "skill": "閃避"}],
                    "attacker_skill_value": 55,
                    "is_ranged": True,
                },
                [], [], speaker_role="player",
            )
            dive_success_roll = dice.SkillCheckResult(
                skill_value=45, roll=35, bonus_dice=0, penalty_dice=0,
                tier="regular", success=True, required_tier="regular",
            )
            attacker_miss_roll = dice.SkillCheckResult(
                skill_value=55, roll=90, bonus_dice=0, penalty_dice=1,
                tier="fail", success=False, required_tier="regular",
            )
            with patch(
                "app.legacy_commands.dice.skill_check",
                side_effect=[dive_success_roll, attacker_miss_roll],
            ) as skill_check_mock, patch("app.legacy_commands.dice.resolve_opposed") as resolve_opposed_mock:
                resolution = legacy_commands._resolve_check_deterministically("g", "u1", "/coc check 閃避")

        resolve_opposed_mock.assert_not_called()
        self.assertEqual(skill_check_mock.call_count, 2)
        # Second call is the attacker's shot — must carry the +1 penalty die
        # earned by the successful dive.
        _, attacker_call_kwargs = skill_check_mock.call_args_list[1]
        self.assertEqual(attacker_call_kwargs.get("penalty_dice"), 1)
        combined_text = resolution.roll_line + resolution.keeper_message
        self.assertIn("撲向掩體成功", combined_text)
        self.assertIn("沒有命中", combined_text)

    def test_failed_dive_gives_attacker_no_penalty_die(self):
        state = _state_with_investigator()
        state.active = True
        # See test_successful_dive_gives_attacker_a_penalty_die above for why
        # luck=0 (leaving no affordable buyable_options for real) is set here.
        state.characters["u1"].luck = 0
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            keeper._execute_tool(
                state, "offer_npc_attack_defense_choice",
                {
                    "investigator": "小明",
                    "options": [{"label": "閃避", "skill": "閃避"}],
                    "attacker_skill_value": 55,
                    "is_ranged": True,
                },
                [], [], speaker_role="player",
            )
            dive_fail_roll = dice.SkillCheckResult(
                skill_value=45, roll=90, bonus_dice=0, penalty_dice=0,
                tier="fail", success=False, required_tier="regular",
            )
            attacker_hit_roll = dice.SkillCheckResult(
                skill_value=55, roll=30, bonus_dice=0, penalty_dice=0,
                tier="regular", success=True, required_tier="regular",
            )
            with patch(
                "app.legacy_commands.dice.skill_check",
                side_effect=[dive_fail_roll, attacker_hit_roll],
            ) as skill_check_mock, patch("app.legacy_commands.dice.resolve_opposed") as resolve_opposed_mock:
                resolution = legacy_commands._resolve_check_deterministically("g", "u1", "/coc check 閃避")

        resolve_opposed_mock.assert_not_called()
        _, attacker_call_kwargs = skill_check_mock.call_args_list[1]
        self.assertEqual(attacker_call_kwargs.get("penalty_dice"), 0)
        combined_text = resolution.roll_line + resolution.keeper_message
        self.assertIn("撲向掩體失敗", combined_text)
        self.assertIn("命中了", combined_text)

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
        timeline_id = state.timeline_id or f"legacy-{state.group_id}"
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
        with StateStorePatch(legacy_commands) as store:
            store.put(state)
            attacker_hit_roll = dice.SkillCheckResult(
                skill_value=55, roll=30, bonus_dice=0, penalty_dice=0,
                tier="regular", success=True, required_tier="regular",
            )
            with patch("app.legacy_commands.dice.skill_check", return_value=attacker_hit_roll):
                resolution = legacy_commands._resolve_luck_decision_deterministically("g", "u1", "skip")

        self.assertIn("遠程攻擊判定", resolution.roll_feedback_text)
        self.assertIn("命中了", resolution.roll_feedback_text)


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
        # tier="extreme", not "critical" — a Critical attacker filters out
        # the Fight Back option entirely server-side (see keeper.py's
        # offer_npc_attack_defense_choice: no tier can beat Critical, so
        # offering Fight Back against it would be an option that can never
        # win). "extreme" still exercises "attacker's tier beats defender's
        # weaker Fight Back roll" without hitting that filter.
        state = _state_with_investigator()
        state.active = True
        # luck=0 genuinely leaves no affordable buyable_options (real
        # app/luck.py math, not mocked) — see RangedDefenseEndToEndTests
        # above — so this test means to exercise the attacker-tier
        # comparison, not the (now much more frequently offered) Luck
        # buy-up decision.
        state.characters["u1"].luck = 0
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=MagicMock(roll=1, tier="extreme")):
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
            self.assertEqual(tool_result["attacker_tier"], "extreme")

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
    """Ordinary checks follow the group's player-owned/autoroll policy."""

    def _options(self):
        return [{"label": "閃避", "skill": "閃避"}, {"label": "反擊", "skill": "格鬥"}]

    def test_skill_check_autoroll_is_immediate_and_does_not_create_pending(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
        with StateStorePatch(keeper) as store:
            store.put(state)
            fake_roll = MagicMock(roll=99, tier="fumble", required_tier="regular", success=False)
            with patch("app.keeper.dice.skill_check", return_value=fake_roll):
                first = keeper._execute_tool(
                    state, "skill_check", {"investigator": "小明", "skill": "閃避"}, [], [], speaker_role="player"
                )
                second = keeper._execute_tool(
                    state, "skill_check", {"investigator": "小明", "skill": "格鬥"}, [], [], speaker_role="player"
                )
            saved_state = store.store["g"]
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(first["resolved"])
        self.assertTrue(second["resolved"])
        self.assertEqual(saved_state.pending_checks, {})

    def test_sanity_check_autoroll_is_immediate_and_does_not_create_pending(self):
        state = _state_with_investigator()
        state.autoroll_checks = True
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
        self.assertTrue(second["ok"])
        self.assertTrue(first["resolved"])
        self.assertTrue(second["resolved"])
        self.assertEqual(saved_state.pending_checks, {})

    def test_character_checks_default_to_player_pending(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check") as roll_mock:
                result = keeper._execute_tool(
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
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.sanity_check") as roll_mock:
                result = keeper._execute_tool(
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

    def test_adjust_character_major_wound_defaults_to_player_pending(self):
        state = _state_with_investigator()
        state.characters["u1"].hp = 10
        state.characters["u1"].hp_max = 10
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check") as roll_mock:
                result = keeper._execute_tool(
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

    def test_adjust_character_major_wound_does_not_falsely_claim_pending_when_one_already_exists(self):
        """Regression: app/combat.py's equivalent _resolve_major_wound_check
        returns None (no major_wound_triggered) when the PC already has a
        pending check in non-autoroll mode, since nothing new gets written
        to pending_checks — adjust_character's own major-wound branch must
        match that, not unconditionally set major_wound=True regardless of
        whether it actually registered anything."""
        state = _state_with_investigator()
        state.characters["u1"].hp = 10
        state.characters["u1"].hp_max = 10
        state.pending_checks["u1"] = {
            "type": "skill", "skill": "偵查", "skill_value": 50,
            "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular", "pushed": False,
        }
        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state,
                "adjust_character",
                {"investigator": "小明", "field": "hp", "delta": -5},
                [],
                [],
                speaker_role="player",
            )
            saved_state = store.store["g"]
        self.assertTrue(result["ok"])
        self.assertNotIn("major_wound", result)
        self.assertNotIn("note", result)
        # The pre-existing pending check must survive untouched — not
        # silently overwritten by the (never-executed) CON registration.
        self.assertEqual(saved_state.pending_checks["u1"]["skill"], "偵查")
        self.assertEqual(saved_state.characters["u1"].hp, 5)

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
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.sanity_check", return_value=san_result), \
                    patch("app.keeper.dice.skill_check", return_value=int_result), \
                    patch("app.keeper.dice.roll_madness", return_value={
                        "roll": 3, "symptom": "暴力衝動", "duration": "3 輪", "guidance": "",
                    }):
                result = keeper._execute_tool(
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
        with StateStorePatch(keeper) as store:
            store.put(state)
            with patch("app.keeper.dice.skill_check", return_value=fake_roll):
                result = keeper._execute_tool(
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
        with StateStorePatch(keeper) as store:
            store.put(state)
            with observability.context(turn_id="turn-retry"), patch(
                "app.keeper.dice.skill_check", return_value=fake_roll
            ) as roll_mock:
                first = keeper._execute_tool(state, "skill_check", tool_input, [], [], speaker_role="player")
                second = keeper._execute_tool(state, "skill_check", tool_input, [], [], speaker_role="player")
        self.assertEqual(first["roll"], second["roll"])
        self.assertEqual(first["check_id"], second["check_id"])
        roll_mock.assert_called_once()

    def test_check_command_does_not_start_a_new_skill_roll(self):
        state = _state_with_investigator()
        state.active = True
        with StateStorePatch(keeper, legacy_commands) as store:
            store.put(state)
            resolution = legacy_commands._resolve_check_deterministically("g", "u1", "/coc check 射擊")
        self.assertFalse(resolution.should_finalize)
        self.assertIn("玩家用 /coc check 或按鈕擲骰", resolution.reply_text)

    def test_offer_check_choice_reuses_identical_pending_request(self):
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
        self.assertTrue(second["ok"])
        self.assertIn("防重複", second["note"])

    def test_offer_check_choice_rejects_different_pending_request(self):
        state = _state_with_investigator()
        with StateStorePatch(keeper) as store:
            store.put(state)
            first = keeper._execute_tool(
                state, "offer_check_choice", {"investigator": "小明", "options": self._options()},
                [], [], speaker_role="player",
            )
            second = keeper._execute_tool(
                state,
                "offer_check_choice",
                {"investigator": "小明", "options": [{"label": "閃避", "skill": "閃避"}, {"label": "射擊", "skill": "射擊"}]},
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
    """clear_pending_check remains an escape hatch for old snapshots."""

    def test_clears_an_existing_pending_check(self):
        state = _state_with_investigator()
        state.pending_checks["u1"] = {"type": "skill", "skill": "閃避", "skill_value": 45}
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
        """The escape hatch remains for pending checks from old snapshots."""
        state = _state_with_investigator()
        state.pending_checks["u1"] = {"type": "skill", "skill": "閃避", "skill_value": 45}
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
