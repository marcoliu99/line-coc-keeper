"""Regression and concurrency tests for timeline/state-loss hardening."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, dice, keeper, memory_rag
from app.models import Character, GroupState
from app.repositories import group_state


class StateLossAmnesiaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "state.db"
        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        db._ensure_tables()
        memory_rag._index_cache.clear()

    def tearDown(self) -> None:
        memory_rag._index_cache.clear()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_provider_chain_round_trip_and_legacy_chain_is_not_trusted(self) -> None:
        state = GroupState(
            "timeline-chain",
            timeline_id="timeline-a",
            openai_previous_response_id="response-a",
            openai_previous_response_timeline_id="timeline-a",
        )
        group_state.save_state(state)
        loaded = group_state.load_state(state.group_id)
        self.assertEqual(loaded.openai_previous_response_timeline_id, "timeline-a")

        legacy = GroupState.from_dict({
            "group_id": "legacy-chain",
            "schema_version": 1,
            "timeline_id": "legacy-chain",
            "openai_previous_response_id": "old-response",
        })
        self.assertEqual(legacy.openai_previous_response_timeline_id, "")

        # Serializing a legacy state must not manufacture chain metadata from
        # its current timeline; doing so would make a previously unverified
        # provider response reusable after an unrelated state save.
        legacy.openai_previous_response_timeline_id = ""
        self.assertEqual(legacy.to_dict()["openai_previous_response_timeline_id"], "")

    def test_missing_timeline_is_initialized_before_a_turn(self) -> None:
        state = GroupState("legacy-turn", openai_previous_response_id="old-response")
        db.set_json("group_states", state.group_id, state.to_dict())
        loaded = group_state.load_state(state.group_id)

        timeline_id = keeper._ensure_turn_timeline(loaded)

        self.assertTrue(timeline_id.startswith("timeline-"))
        persisted = group_state.load_state(state.group_id)
        self.assertEqual(persisted.timeline_id, timeline_id)
        self.assertEqual(persisted.openai_previous_response_timeline_id, "")

    def test_memory_isolated_by_timeline_and_retry_is_idempotent(self) -> None:
        self.assertTrue(memory_rag.append_memory(
            "memory-group", "舊時間線的地下室紙條", timeline_id="timeline-old",
            idempotency_key="old-key", embedding=None,
        ))
        self.assertFalse(memory_rag.append_memory(
            "memory-group", "舊時間線的地下室紙條", timeline_id="timeline-old",
            idempotency_key="old-key", embedding=None,
        ))
        self.assertTrue(memory_rag.append_memory(
            "memory-group", "新時間線的閣樓鑰匙", timeline_id="timeline-new",
            idempotency_key="new-key", embedding=None,
        ))
        self.assertTrue(memory_rag.append_memory(
            "memory-group", "未綁定時間線的遺留片段", embedding=None,
        ))

        old = memory_rag.search_memory("memory-group", "地下室紙條", timeline_id="timeline-old")
        new = memory_rag.search_memory("memory-group", "地下室紙條", timeline_id="timeline-new")
        legacy = memory_rag.search_memory("memory-group", "遺留片段", timeline_id="legacy-memory-group")
        self.assertEqual(len(old), 1)
        self.assertEqual(new, [])
        self.assertEqual(len(legacy), 1)

    def test_stale_maintenance_after_newgame_does_not_write_memory(self) -> None:
        state = GroupState("maintenance-race", timeline_id="timeline-old")
        state.log = [{"role": "user", "content": f"old-{i}"} for i in range(5)]
        group_state.save_state(state)

        def newgame_during_summary(_base: str, _chunk: list[dict[str, str]]) -> str:
            replacement = GroupState("maintenance-race")
            group_state.save_state(replacement, reason="newgame")
            return "不應該提交的舊摘要"

        with patch.object(keeper, "MAX_LOG_TURNS", 1), \
                patch.object(keeper, "run_scene_digest_maintenance"), \
                patch.object(keeper, "summarize_log_chunk", side_effect=newgame_during_summary), \
                patch.object(keeper.memory_rag, "prepare_memory_embedding", return_value=None):
            result = keeper.run_post_turn_maintenance(state.group_id)

        self.assertEqual(result["commit_status"], "stale_timeline")
        self.assertFalse(result["state_saved"])
        current = group_state.load_state(state.group_id)
        self.assertNotEqual(current.timeline_id, "timeline-old")
        self.assertEqual(current.log, [])
        self.assertIsNone(db.get_json("memory_chunks", state.group_id))

    def test_maintenance_commits_state_and_memory_together(self) -> None:
        state = GroupState("maintenance-atomic", timeline_id="timeline-a")
        state.log = [{"role": "user", "content": f"event-{i}"} for i in range(5)]
        group_state.save_state(state)

        with patch.object(keeper, "MAX_LOG_TURNS", 1), \
                patch.object(keeper, "run_scene_digest_maintenance"), \
                patch.object(keeper, "summarize_log_chunk", return_value="摘要 A"), \
                patch.object(keeper.memory_rag, "prepare_memory_embedding", return_value=None):
            result = keeper.run_post_turn_maintenance(state.group_id)

        self.assertEqual(result["commit_status"], "committed")
        self.assertTrue(result["state_saved"])
        current = group_state.load_state(state.group_id)
        self.assertEqual(current.log, state.log[-2:])
        self.assertEqual(current.campaign_summary, "摘要 A")
        chunks = db.get_json("memory_chunks", state.group_id)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["timeline_id"], "timeline-a")
        self.assertTrue(chunks[0]["idempotency_key"])

    def test_deterministic_check_has_identity_and_origin_context(self) -> None:
        state = GroupState("deterministic-identity")
        state.autoroll_checks = True
        state.characters["p1"] = Character(name="P1", owner_id="p1")
        group_state.save_state(state)

        result = keeper._execute_tool(
            state,
            "skill_check",
            {"investigator": "P1", "skill": "偵查", "action_context": "在醫院地下室檢查血跡"},
            [],
            [],
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["resolved"])
        self.assertTrue(result["check_id"].startswith("check-"))
        self.assertEqual(result["timeline_id"], state.timeline_id)
        self.assertEqual(result["action_context"], "在醫院地下室檢查血跡")
        self.assertEqual(group_state.load_state(state.group_id).pending_checks, {})

    def test_stale_check_button_identity_cannot_match_a_replacement(self) -> None:
        try:
            from app.check_identity import compact_identity_token
            from app.discord_bot import (
                _check_button_matches_pending,
                _luck_button_matches_pending,
            )
        except ImportError:
            self.skipTest("discord.py is not installed")

        pending = {
            "type": "skill", "check_id": "check-new", "skill": "偵查",
            "timeline_id": "timeline-a",
        }
        self.assertTrue(_check_button_matches_pending("p1", pending, "check-new", "timeline-a"))
        compact_check_id = compact_identity_token("check", "p1", "check-new", "timeline-a")
        self.assertTrue(_check_button_matches_pending("p1", pending, compact_check_id, "timeline-a"))
        self.assertFalse(_check_button_matches_pending("p1", pending, "check-old", "timeline-a"))
        self.assertFalse(_check_button_matches_pending("p1", pending, compact_check_id, "timeline-b"))
        self.assertFalse(_check_button_matches_pending("p1", {"type": "skill", "skill": "偵查"}, "", "legacy-g"))
        self.assertFalse(_check_button_matches_pending("p1", None, "check-new", "timeline-a"))

        decision = {"decision_id": "decision-new", "timeline_id": "timeline-a"}
        compact_decision_id = compact_identity_token("decision", "p1", "decision-new", "timeline-a")
        self.assertTrue(_luck_button_matches_pending("p1", decision, compact_decision_id, "timeline-a"))
        self.assertFalse(_luck_button_matches_pending("p1", decision, compact_decision_id, "timeline-b"))

    def test_discord_identity_token_keeps_component_id_under_limit(self) -> None:
        try:
            from app.check_identity import compact_identity_token
            from app.discord_bot import CheckButton, LuckSpendButton
        except ImportError:
            self.skipTest("discord.py is not installed")

        conversation_id = "discord-channel-" + "9" * 18
        owner_id = "8" * 19
        timeline_id = "timeline-" + "a" * 32
        check_token = compact_identity_token("check", owner_id, "check-" + "b" * 64, timeline_id)
        decision_token = compact_identity_token("decision", owner_id, "decision-" + "c" * 64, timeline_id)
        check_button = CheckButton(conversation_id, owner_id, "選擇並擲 閃避", option="#0", check_id=check_token)
        luck_button = LuckSpendButton(
            conversation_id, owner_id, "維持目前結果", "skip", decision_id=decision_token
        )
        self.assertLessEqual(len(check_button.item.custom_id), 100)
        self.assertLessEqual(len(luck_button.item.custom_id), 100)

    def test_corrupt_group_state_does_not_abort_memory_maintenance(self) -> None:
        group_id = "maintenance-corrupt-state"
        state = GroupState(group_id, timeline_id="timeline-a")
        group_state.save_state(state)
        with db.transaction() as conn:
            conn.execute("UPDATE group_states SET data = ? WHERE key = ?", ("not-json", group_id))

        result = keeper._persist_memory_maintenance_state(
            group_id,
            "摘要不應提交",
            [{"role": "user", "content": "old"}],
            timeline_id="timeline-a",
            base_summary="",
            source_revision=1,
            idempotency_key="corrupt-state-key",
            embedding=None,
        )

        self.assertEqual(result, "corrupt_group_state")
        self.assertIsNone(db.get_json("memory_chunks", group_id))

    def test_new_scenario_invalidates_pending_decisions_and_check_cache(self) -> None:
        from app.legacy_commands import _apply_new_scenario

        state = GroupState("scenario-reset", timeline_id="timeline-old")
        state.pending_checks["p1"] = {"type": "skill", "timeline_id": "timeline-old"}
        state.pending_luck_decisions["p1"] = {"timeline_id": "timeline-old"}
        state.deterministic_check_results["old-result"] = {"timeline_id": "timeline-old"}

        _apply_new_scenario(state, "new text", "New", {"npcs": [], "locations": []}, {}, [])

        self.assertNotEqual(state.timeline_id, "timeline-old")
        self.assertEqual(state.pending_checks, {})
        self.assertEqual(state.pending_luck_decisions, {})
        self.assertEqual(state.deterministic_check_results, {})

    def test_stale_timeline_check_is_consumed_without_a_roll(self) -> None:
        state = GroupState("stale-check", timeline_id="timeline-current", active=True)
        state.characters["p1"] = Character(name="P1", owner_id="p1", skills={"偵查": 60})
        state.pending_checks["p1"] = {
            "type": "skill", "skill": "偵查", "skill_value": 60,
            "bonus_dice": 0, "penalty_dice": 0, "timeline_id": "timeline-old",
        }
        group_state.save_state(state)

        with patch.object(dice, "skill_check") as roll:
            from app.legacy_commands import _resolve_check_deterministically

            result = _resolve_check_deterministically("stale-check", "p1", "/coc check 偵查")

        self.assertIn("時間線已經失效", result.reply_text)
        roll.assert_not_called()
        self.assertEqual(group_state.load_state(state.group_id).pending_checks, {})

    def test_mismatched_skill_does_not_reuse_pending_identity_or_context(self) -> None:
        state = GroupState("mismatched-check", timeline_id="timeline-current", active=True)
        state.characters["p1"] = Character(name="P1", owner_id="p1", skills={"偵查": 60, "聆聽": 50})
        pending = {
            "type": "skill", "skill": "偵查", "skill_value": 60,
            "bonus_dice": 0, "penalty_dice": 0, "check_id": "check-old",
            "timeline_id": "timeline-current", "action_context": "調查血跡",
        }
        state.pending_checks["p1"] = pending
        group_state.save_state(state)

        with patch.object(dice, "skill_check") as roll:
            from app.legacy_commands import _resolve_check_deterministically

            result = _resolve_check_deterministically("mismatched-check", "p1", "/coc check 聆聽")

        self.assertIn("正確的選項名稱", result.reply_text)
        roll.assert_not_called()
        self.assertEqual(group_state.load_state(state.group_id).pending_checks["p1"], pending)


class MultiUserDeterministicCheckStressTests(unittest.IsolatedAsyncioTestCase):
    """Six player/KP-assistant check calls resolve without lost state."""

    async def test_six_users_and_kp_assistant_do_not_double_roll(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "stress.db"
            with patch.object(db, "DB_PATH", db_path):
                db._ensure_tables()
                state = GroupState("stress-group", timeline_id="timeline-stress", active=True)
                state.autoroll_checks = True
                for index in range(1, 7):
                    owner_id = f"p{index}"
                    state.characters[owner_id] = Character(
                        name=f"Player {index}", owner_id=owner_id, skills={"偵查": 60}
                    )
                group_state.save_state(state)
                roll = dice.SkillCheckResult(
                    skill_value=60, roll=99, bonus_dice=0, penalty_dice=0,
                    tier="fail", success=False, required_tier="regular",
                )
                with patch.object(keeper.dice, "skill_check", return_value=roll) as roll_mock:
                    results = await asyncio.gather(*(
                        asyncio.to_thread(
                            keeper._execute_tool,
                            GroupState.from_dict(state.to_dict()),
                            "skill_check",
                            {
                                "investigator": f"Player {index}",
                                "skill": "偵查",
                                "action_context": f"Player {index} 在現場調查",
                            },
                            [],
                            [],
                            speaker_role="kp_assistant" if index == 6 else "player",
                        )
                        for index in range(1, 7)
                    ))

                self.assertEqual(len(results), 6)
                self.assertTrue(all(result["ok"] and result["resolved"] for result in results))
                self.assertEqual(roll_mock.call_count, 6)
                final_state = group_state.load_state(state.group_id)
                self.assertEqual(final_state.pending_checks, {})
                self.assertEqual(final_state.state_revision, state.state_revision + 6)
                self.assertEqual(final_state.timeline_id, "timeline-stress")
