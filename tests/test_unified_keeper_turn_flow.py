"""The three player story entries share Supervisor without a second Keeper loop."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app import db, keeper, legacy_commands
from app.agents import assistant, narrator, supervisor
from app.commands.handlers import system
from app.domain.models import AgentMessage
from app.models import Character, GroupState
from app.repositories.group_state import load_state, save_state


def _context(state: GroupState, text: str) -> AgentMessage:
    return AgentMessage(payload={
        "state": state, "user_id": "player", "display_name": "調查員",
        "speaker_role": "player", "text": text, "resolved_location": None,
        "rag_context": "", "memory_context": "", "resolved_check_events": [],
    })


class UnifiedKeeperTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_kp_assistant_runs_without_legacy_keeper_turn(self):
        class Provider:
            async def run_conversation(self, *_args, **kwargs):
                self.previous_response_id = kwargs.get("previous_response_id")
                return "幕後備註"

        provider = Provider()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DB_PATH", Path(directory) / "state.db"
        ):
            db._ensure_tables()
            state = GroupState(group_id="assistant-ooc", timeline_id="current",
                               openai_previous_response_id="formal-chain",
                               openai_previous_response_timeline_id="current")
            save_state(state)
            message = AgentMessage(payload={
                "state": state, "user_id": "kp", "display_name": "KP",
                "text": "這個線索稍後再揭露", "resolved_location": None,
            })
            with (
                patch.object(keeper, "_PROVIDERS", {"openai": provider}),
                patch.object(keeper, "LLM_PROVIDER", "openai"),
                patch.object(keeper, "run_turn", side_effect=AssertionError("legacy Keeper loop")),
            ):
                reply, private, images = await assistant.run_assistant(message)
            persisted = load_state("assistant-ooc")
        self.assertEqual((reply, private, images), ("幕後備註", [], []))
        self.assertEqual(provider.previous_response_id, "formal-chain")
        self.assertEqual(persisted.log, [])
        self.assertEqual(persisted.kp_ooc_log[-2]["content"], "這個線索稍後再揭露")
        self.assertEqual(persisted.kp_ooc_log[-1]["content"], "幕後備註")
        self.assertEqual(persisted.openai_previous_response_id, "formal-chain")

    async def test_kp_assistant_successful_game_tool_promotes_canon_without_legacy_loop(self):
        class Provider:
            async def run_conversation(self, *_args, **_kwargs):
                execute_tool = _args[5]
                self.blocked = await execute_tool("forbidden_tool", {})
                self.rolled = await execute_tool(
                    "roll_dice", {"expression": "1d6", "roll_context": "game_resolution"}
                )
                return "骰出了 4。"

        provider = Provider()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DB_PATH", Path(directory) / "state.db"
        ):
            db._ensure_tables()
            state = GroupState(group_id="assistant-canon", timeline_id="current")
            save_state(state)
            message = AgentMessage(payload={
                "state": state, "user_id": "kp", "display_name": "KP",
                "text": "替怪物擲骰", "resolved_location": None,
            })
            with (
                patch.object(keeper, "_PROVIDERS", {"openai": provider}),
                patch.object(keeper, "LLM_PROVIDER", "openai"),
                patch.object(keeper, "run_turn", side_effect=AssertionError("legacy Keeper loop")),
                patch.object(keeper, "_execute_tool", return_value={"ok": True, "result": 4}) as execute,
            ):
                reply, _, _ = await assistant.run_assistant(message)
            persisted = load_state("assistant-canon")
        self.assertEqual(reply, "骰出了 4。")
        self.assertFalse(provider.blocked["ok"])
        self.assertEqual(provider.rolled["result"], 4)
        execute.assert_called_once()
        self.assertEqual(persisted.kp_ooc_log, [])
        self.assertEqual(len(persisted.log), 2)
        self.assertIn("DETERMINISTIC GAME WORKFLOW", persisted.log[0]["content"])

    async def test_kp_assistant_explicit_canon_uses_public_guard_and_timeline(self):
        class Provider:
            async def run_conversation(self, *_args, **_kwargs):
                return "門後沒有第二隻怪物。"

        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DB_PATH", Path(directory) / "state.db"
        ):
            db._ensure_tables()
            state = GroupState(group_id="assistant-manual", timeline_id="current")
            save_state(state)
            message = AgentMessage(payload={
                "state": state, "user_id": "kp", "display_name": "KP",
                "text": "!門後沒有第二隻怪物", "resolved_location": None,
            })
            with (
                patch.object(keeper, "_PROVIDERS", {"openai": Provider()}),
                patch.object(keeper, "LLM_PROVIDER", "openai"),
                patch.object(keeper, "run_turn", side_effect=AssertionError("legacy Keeper loop")),
            ):
                reply, _, _ = await assistant.run_assistant(message)
            persisted = load_state("assistant-manual")
        self.assertEqual(reply, "門後沒有第二隻怪物。")
        self.assertEqual(persisted.log[0]["content"], "[KP Assistant] 門後沒有第二隻怪物")
        self.assertEqual(persisted.log[1]["content"], reply)
        self.assertEqual(persisted.kp_ooc_log, [])

    async def test_opening_pipeline_persists_scene_and_started_flag_together(self):
        class Provider:
            calls = 0

            async def run_conversation(self, *_args, **_kwargs):
                self.calls += 1
                return "你站在書房門前。"

        provider = Provider()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DB_PATH", Path(directory) / "state.db"
        ):
            db._ensure_tables()
            state = GroupState(group_id="opening-pipeline", active=True,
                               timeline_id="timeline-current", scenario_text="書房是故事起點。",
                               openai_previous_response_id="old-chain",
                               openai_previous_response_timeline_id="timeline-current")
            save_state(state)
            with patch.object(narrator, "_PROVIDERS", {"openai": provider}), \
                    patch.object(narrator, "LLM_PROVIDER", "openai"), \
                    patch.object(supervisor.keeper, "run_turn", side_effect=AssertionError("old player loop")):
                reply, _private, _images = await supervisor.run_turn(
                    state, "player", "守密人", "依劇本生成開場", None,
                    "player", "opening-pipeline", turn_kind="opening_fallback",
                )
            persisted = load_state("opening-pipeline")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(reply, "你站在書房門前。")
        self.assertTrue(persisted.game_started)
        self.assertEqual(len(persisted.log), 2)
        self.assertEqual(persisted.openai_previous_response_id, "")

    async def test_check_tail_calls_supervisor_with_authoritative_result(self):
        character = Character(name="調查員", owner_id="player")
        state = GroupState(group_id="check-entry", timeline_id="timeline-current",
                           game_started=True, characters={"player": character})
        followup = AsyncMock(return_value=("後續敘事", [], []))
        maintenance = AsyncMock()
        reply = AsyncMock()
        with (
            patch.object(legacy_commands.keeper, "_refresh_state_snapshot", return_value=state),
            patch.object(legacy_commands, "supervisor") as pipeline,
            patch.object(legacy_commands, "_run_post_turn_maintenance_after_output", maintenance),
            patch.object(legacy_commands, "_persist_resolved_check_event"),
            patch.object(legacy_commands.keeper, "run_turn", side_effect=AssertionError("old player loop")),
        ):
            pipeline.run_turn = followup
            await legacy_commands._finalize_check_result(
                "check-entry", "player", state, character,
                "🎲 調查員 STR 擲出 32 → 成功", "（已結算 STR 成功）",
                reply, AsyncMock(), AsyncMock(), AsyncMock(),
                timeline_id="timeline-current", action_context="推開木門",
                resolved_event={
                    "investigator": "調查員", "skill": "STR", "skill_value": 45,
                    "roll": 32, "difficulty": "regular", "outcome": "成功",
                    "action_context": "推開木門", "check_id": "check-1",
                    "timeline_id": "timeline-current", "state_before": {},
                },
            )
        self.assertEqual(followup.await_args.kwargs["turn_kind"], "resolved_check_followup")
        self.assertEqual(followup.await_args.kwargs["resolved_check_context"]["roll"], 32)
        self.assertIsNone(followup.await_args.kwargs["resolved_location"])
        maintenance.assert_awaited_once()

    async def test_start_fallback_enters_supervisor_and_extracted_opening_does_not(self):
        character = Character(name="調查員", owner_id="player")
        state = GroupState(group_id="start-entry", active=True, scenario_text="書房是起點。",
                           characters={"player": character})
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        pipeline = AsyncMock(return_value=("你站在書房門前。", [], []))
        with (
            patch.object(system, "load_state", return_value=state),
            patch.object(system, "_heal_character", return_value=[]),
            patch.object(system, "_build_readiness_roster", return_value="名冊"),
            patch.object(system.scenario_intro, "extract_opening_narration", return_value={"found": False}),
            patch.object(system.keeper, "_refresh_state_snapshot", return_value=state),
            patch.object(system.supervisor, "run_turn", pipeline),
            patch.object(system, "_run_post_turn_maintenance_after_output", new_callable=AsyncMock),
            patch.object(system.keeper, "run_turn", side_effect=AssertionError("old player loop")),
        ):
            await system.handle_system_command(
                "start-entry", "player", reply, AsyncMock(), AsyncMock(), AsyncMock(),
                ["/coc", "start"],
            )
        self.assertEqual(pipeline.await_args.kwargs["turn_kind"], "opening_fallback")
        self.assertEqual(replies, ["名冊"])

    async def test_resolved_check_uses_one_narrative_conversation_and_cannot_reroll(self):
        state = GroupState(group_id="unified-check", game_started=True,
                           timeline_id="timeline-current")
        result = {
            "investigator": "調查員", "skill": "STR", "skill_value": 45,
            "roll": 32, "difficulty": "regular", "outcome": "成功",
            "action_context": "推開木門",
        }

        class Provider:
            calls = 0

            async def run_conversation(self, _static, dynamic, tools, _history,
                                       _message, execute_tool, _iterations, **_kwargs):
                self.calls += 1
                self.dynamic = dynamic
                self.offered = {tool["name"] for tool in tools}
                self.rejected = await execute_tool("skill_check", {"skill": "STR"})
                self.damage = await execute_tool("apply_combat_damage", {"target": "魚人", "raw_damage": 3})
                return "木門應聲而開，魚人受到傷害。"

        provider = Provider()
        execute = Mock(return_value={"ok": True, "damage": 3})
        commit = Mock(return_value=True)
        with (
            patch.object(supervisor.context_builder, "build_context", new_callable=AsyncMock,
                         return_value=_context(state, "檢定結果")),
            patch.object(supervisor.intent_router, "classify_intent", side_effect=AssertionError("special turn was classified")),
            patch.object(supervisor.executor, "run_executor", side_effect=AssertionError("check was rerolled")),
            patch.object(supervisor.keeper, "run_turn", side_effect=AssertionError("legacy Keeper was called")),
            patch.object(supervisor.keeper, "_commit_turn_result", commit),
            patch.object(narrator.keeper, "_execute_tool", execute),
            patch.object(narrator, "_PROVIDERS", {"openai": provider}),
            patch.object(narrator, "LLM_PROVIDER", "openai"),
        ):
            reply, _private, _images = await supervisor.run_turn(
                state, "player", "調查員", "檢定結果", None, "player", "unified-check",
                turn_kind="resolved_check_followup", resolved_check_context=result,
            )

        self.assertEqual(provider.calls, 1)
        self.assertIn("擲出 32", provider.dynamic)
        self.assertNotIn("skill_check", provider.offered)
        self.assertIn("apply_combat_damage", provider.offered)
        self.assertEqual(provider.rejected["error"], "tool_not_allowed_for_turn")
        execute.assert_called_once()
        self.assertIn("魚人受到傷害", reply)
        self.assertEqual(commit.call_count, 1)

    async def test_opening_fallback_uses_same_pipeline_and_atomic_start_commit(self):
        state = GroupState(group_id="unified-start", active=True,
                           timeline_id="timeline-current", scenario_text="書房是故事起點。")

        class Provider:
            calls = 0

            async def run_conversation(self, _static, _dynamic, tools, _history,
                                       _message, execute_tool, _iterations, **_kwargs):
                self.calls += 1
                self.offered = {tool["name"] for tool in tools}
                self.rejected = await execute_tool("skill_check", {"skill": "偵查"})
                self.search = await execute_tool("search_scenario", {"query": "起點"})
                return "你站在書房門前。"

        provider = Provider()
        execute = Mock(return_value={"ok": True, "results": []})
        commit = Mock(return_value=True)
        with (
            patch.object(supervisor.context_builder, "build_context", new_callable=AsyncMock,
                         return_value=_context(state, "開場後備")),
            patch.object(supervisor.intent_router, "classify_intent", side_effect=AssertionError("opening was classified")),
            patch.object(supervisor.executor, "run_executor", side_effect=AssertionError("opening used Executor")),
            patch.object(supervisor.keeper, "run_turn", side_effect=AssertionError("legacy Keeper was called")),
            patch.object(supervisor.keeper, "_commit_turn_result", commit),
            patch.object(narrator, "tools_for_speaker_role", return_value=[
                {"name": "search_scenario"}, {"name": "skill_check"},
            ]),
            patch.object(narrator.keeper, "_execute_tool", execute),
            patch.object(narrator, "_PROVIDERS", {"openai": provider}),
            patch.object(narrator, "LLM_PROVIDER", "openai"),
        ):
            reply, _private, _images = await supervisor.run_turn(
                state, "player", "守密人", "開場後備", None, "player", "unified-start",
                turn_kind="opening_fallback",
            )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.offered, {"search_scenario"})
        self.assertEqual(provider.rejected["error"], "tool_not_allowed_for_turn")
        execute.assert_called_once()
        self.assertEqual(reply, "你站在書房門前。")
        self.assertTrue(commit.call_args.kwargs["start_game"])

    async def test_failed_opening_does_not_commit_or_start_game(self):
        state = GroupState(group_id="unified-failed-start", active=True,
                           timeline_id="timeline-current", scenario_text="書房。")
        provider = Mock(run_conversation=AsyncMock(side_effect=RuntimeError("provider failed")))
        commit = Mock(return_value=True)
        with (
            patch.object(supervisor.context_builder, "build_context", new_callable=AsyncMock,
                         return_value=_context(state, "開場後備")),
            patch.object(supervisor.keeper, "_commit_turn_result", commit),
            patch.object(narrator, "_PROVIDERS", {"openai": provider}),
            patch.object(narrator, "LLM_PROVIDER", "openai"),
        ):
            reply, _private, _images = await supervisor.run_turn(
                state, "player", "守密人", "開場後備", None, "player", "unified-failed-start",
                turn_kind="opening_fallback",
            )
        self.assertIn("遊戲尚未開始", reply)
        self.assertFalse(state.game_started)
        commit.assert_not_called()


class OpeningCommitTests(unittest.TestCase):
    def test_opening_log_and_started_flag_are_one_transaction(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DB_PATH", Path(directory) / "state.db"
        ):
            db._ensure_tables()
            state = GroupState(group_id="start-commit", active=True,
                               timeline_id="timeline-current", scenario_text="書房。")
            save_state(state)
            entries = [
                {"role": "user", "content": "守密人：開始遊戲"},
                {"role": "assistant", "content": "你站在書房門前。"},
            ]
            self.assertTrue(keeper._commit_turn_result(
                state, entries, timeline_id="timeline-current", start_game=True,
            ))
            saved = load_state("start-commit")
            self.assertTrue(saved.game_started)
            self.assertEqual(saved.log, entries)
            self.assertFalse(keeper._commit_turn_result(
                state, entries, timeline_id="timeline-current", start_game=True,
            ))
            self.assertEqual(load_state("start-commit").log, entries)
