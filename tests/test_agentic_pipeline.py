import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.models import AgentMessage, MechanicResult, StateDelta
from app.models import Character, GroupState


class ExecutorWrapupGatingTests(unittest.IsolatedAsyncioTestCase):
    """PR #55 review finding: run_executor discards run_conversation's
    return value entirely (only the tool calls' side effects matter to a
    MechanicResult), and supervisor.py always runs a separate Narrator call
    afterward regardless of how the Executor's turn went. A forced wrap-up
    call inside run_conversation would therefore be a real extra API
    request whose output the player could never see — run_executor must
    pass enable_wrapup=False."""

    async def test_run_executor_disables_the_wrapup_call(self):
        from app.agents import executor

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "state": state,
            "text": "你攻擊怪物",
            "user_id": "u1",
            "display_name": "調查員",
            "speaker_role": "player",
        })

        fake_run_conversation = AsyncMock(return_value="ignored")
        with patch.object(executor, "_PROVIDERS", {"openai": type("P", (), {"run_conversation": fake_run_conversation})()}), \
                patch.object(executor, "LLM_PROVIDER", "openai"):
            await executor.run_executor(message)

        self.assertFalse(fake_run_conversation.call_args.kwargs.get("enable_wrapup", True))

    async def test_openai_executor_withholds_status_tool_when_snapshot_is_present(self):
        from app.agents import executor
        from app.models import Combatant, CombatState

        state = GroupState(group_id="g")
        state.combat = CombatState(
            active=True,
            round_number=1,
            order=[Combatant(name="Investigator", dex=70, hp=10, hp_max=10, is_pc=True)],
        )
        message = AgentMessage(payload={
            "state": state,
            "text": "我攻擊怪物",
            "user_id": "u1",
            "display_name": "調查員",
            "speaker_role": "player",
        })
        fake_run_conversation = AsyncMock(return_value="ignored")
        with patch.object(executor, "_PROVIDERS", {
            "openai": type("P", (), {"run_conversation": fake_run_conversation})()
        }), patch.object(executor, "LLM_PROVIDER", "openai"):
            await executor.run_executor(message)

        tools_for_request = fake_run_conversation.call_args.kwargs["tools_for_request"]
        offered_names = {tool["name"] for tool in tools_for_request()}
        self.assertNotIn("get_combat_status", offered_names)

    async def test_openai_executor_keeps_status_tool_without_combat_snapshot(self):
        from app.agents import executor

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "state": state,
            "text": "我攻擊怪物",
            "user_id": "u1",
            "display_name": "調查員",
            "speaker_role": "player",
        })
        fake_run_conversation = AsyncMock(return_value="ignored")
        with patch.object(executor, "_PROVIDERS", {
            "openai": type("P", (), {"run_conversation": fake_run_conversation})()
        }), patch.object(executor, "LLM_PROVIDER", "openai"):
            await executor.run_executor(message)

        tools_for_request = fake_run_conversation.call_args.kwargs["tools_for_request"]
        offered_names = {tool["name"] for tool in tools_for_request()}
        self.assertIn("get_combat_status", offered_names)


class ContextBuilderScenarioRagGatingTests(unittest.IsolatedAsyncioTestCase):
    """Regression tests for the review finding that context_builder ran
    scenario_rag.get_index/search on every turn regardless of
    SCENARIO_RAG_ENABLED (default off), even though keeper._build_static_prompt
    already embeds the full/chapter scenario text directly in that mode,
    making the proactive search redundant and a real per-turn cost."""

    def _state(self) -> GroupState:
        state = GroupState(group_id="g")
        state.scenario_text = "some scenario text"
        state.scenario_title = "Some Title"
        return state

    async def test_scenario_rag_skipped_when_disabled(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", False), \
                patch.object(scenario_rag, "get_index") as mock_get_index, \
                patch.object(memory_rag, "search_memory", return_value=[]):
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        mock_get_index.assert_not_called()
        self.assertEqual(message.payload["rag_context"], "")

    async def test_scenario_rag_runs_when_enabled(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value="fake-index") as mock_get_index, \
                patch.object(scenario_rag, "search", return_value=["chunk"]) as mock_search, \
                patch.object(scenario_rag, "format_results", return_value="formatted rag context"), \
                patch.object(memory_rag, "search_memory", return_value=[]):
            message = await context_builder.build_context(
                state=self._state(), user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        mock_get_index.assert_called_once()
        mock_search.assert_called_once()
        self.assertEqual(message.payload["rag_context"], "formatted rag context")

    async def test_scenario_and_memory_rag_are_gathered_and_one_failure_is_isolated(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        def slow_scenario_search(*_args, **_kwargs):
            time.sleep(0.08)
            raise RuntimeError("scenario index unavailable")

        def slow_memory_search(*_args, **_kwargs):
            time.sleep(0.08)
            return [{"label": "old", "text": "memory"}]

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value="fake-index"), \
                patch.object(scenario_rag, "search", side_effect=slow_scenario_search), \
                patch.object(memory_rag, "search_memory", side_effect=slow_memory_search), \
                patch.object(scenario_rag, "format_results", return_value=""), \
                patch.object(memory_rag, "format_results", return_value="memory context"):
            started = time.perf_counter()
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.15)
        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["rag_status"], "error")
        self.assertEqual(message.payload["memory_context"], "memory context")
        self.assertEqual(message.payload["memory_status"], "success")

    async def test_proactive_rag_does_not_inject_bm25_fallback_context(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        index = type("Index", (), {"chunks": [1], "has_embeddings": False, "index_cache": "memory"})()

        def memory_search(_group_id, _query, *, metrics, **_kwargs):
            metrics["has_embeddings"] = False
            return [{"label": "old", "text": "fallback memory"}]

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value=index), \
                patch.object(scenario_rag, "search", return_value=[{"page": 1, "text": "fallback scenario"}]), \
                patch.object(scenario_rag, "format_results", return_value="fallback scenario context"), \
                patch.object(memory_rag, "search_memory", side_effect=memory_search), \
                patch.object(memory_rag, "format_results", return_value="fallback memory context"):
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["rag_status"], "fallback")
        self.assertEqual(message.payload["memory_context"], "")
        self.assertEqual(message.payload["memory_status"], "fallback")

    async def test_proactive_rag_does_not_inject_query_embedding_fallback_context(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        index = type("Index", (), {"chunks": [1], "has_embeddings": True, "index_cache": "memory"})()

        def scenario_search(_index, _query, *, top_k, metrics):
            del top_k
            metrics["query_embedding_status"] = "fallback"
            return [{"page": 1, "text": "BM25 fallback"}]

        def memory_search(_group_id, _query, *, metrics, **_kwargs):
            metrics["has_embeddings"] = True
            metrics["query_embedding_status"] = "fallback"
            return [{"label": "old", "text": "BM25 fallback"}]

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value=index), \
                patch.object(scenario_rag, "search", side_effect=scenario_search), \
                patch.object(scenario_rag, "format_results", return_value="scenario fallback"), \
                patch.object(memory_rag, "search_memory", side_effect=memory_search), \
                patch.object(memory_rag, "format_results", return_value="memory fallback"):
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["rag_status"], "fallback")
        self.assertEqual(message.payload["memory_context"], "")
        self.assertEqual(message.payload["memory_status"], "fallback")

    async def test_rag_timeout_observes_shielded_worker_until_it_finishes(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        started = threading.Event()
        released = threading.Event()

        def slow_search(*_args, **_kwargs):
            started.set()
            released.wait(timeout=1)
            return []

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(context_builder, "EMBEDDING_REQUEST_TIMEOUT_SECONDS", 0.001), \
                patch.object(scenario_rag, "get_index", return_value="fake-index"), \
                patch.object(scenario_rag, "search", side_effect=slow_search), \
                patch.object(memory_rag, "search_memory", return_value=[]):
            message = await context_builder.build_context(
                state=self._state(), user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        self.assertEqual(message.payload["rag_status"], "timeout")
        released.set()
        await asyncio.sleep(0.02)

    async def test_detached_rag_worker_is_waited_by_shutdown_registry(self):
        from app import async_utils

        started = threading.Event()
        released = threading.Event()

        def blocking_worker():
            started.set()
            released.wait(timeout=1)

        task = asyncio.create_task(asyncio.to_thread(blocking_worker))
        async_utils.observe_background_task(task, operation="test.rag")
        self.assertTrue(await asyncio.to_thread(started.wait, 1))

        waiter = asyncio.create_task(async_utils.wait_for_background_tasks(0.5))
        await asyncio.sleep(0.01)
        self.assertFalse(waiter.done())
        released.set()
        await waiter
        self.assertTrue(task.done())

    async def test_recovery_marker_persistence_is_bounded_and_observed(self):
        from app import async_utils, keeper

        release = asyncio.Event()

        async def blocked_marker(*_args):
            await release.wait()

        with patch.object(keeper, "record_tool_recovery_marker", side_effect=blocked_marker), \
                patch.object(keeper, "PROVIDER_SHUTDOWN_GRACE_SECONDS", 0.001):
            started_at = time.perf_counter()
            await keeper.record_tool_recovery_marker_bounded(
                GroupState(group_id="marker-test"), "apply_combat_damage", {"damage": 1}
            )
            elapsed = time.perf_counter() - started_at

        self.assertLess(elapsed, 0.5)
        release.set()
        await async_utils.wait_for_background_tasks(0.5)

    async def test_build_context_cancellation_propagates_and_observes_worker(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        started = threading.Event()
        released = threading.Event()

        def blocking_search(*_args, **_kwargs):
            started.set()
            released.wait(timeout=1)
            return []

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value="fake-index"), \
                patch.object(scenario_rag, "search", side_effect=blocking_search), \
                patch.object(memory_rag, "search_memory", return_value=[]):
            task = asyncio.create_task(context_builder.build_context(
                state=self._state(), user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            ))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        released.set()
        await asyncio.sleep(0.02)

    async def test_proactive_rag_empty_results_are_not_formatted_into_prompt(self):
        from app import memory_rag, scenario_rag
        from app.agents import context_builder

        with patch.object(context_builder, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "get_index", return_value="fake-index"), \
                patch.object(scenario_rag, "search", return_value=[]), \
                patch.object(scenario_rag, "format_results", return_value="should not be used"), \
                patch.object(memory_rag, "search_memory", return_value=[]), \
                patch.object(memory_rag, "format_results", return_value="should not be used"):
            state = self._state()
            state.characters["u1"] = Character(name="P1", owner_id="u1")
            message = await context_builder.build_context(
                state=state, user_id="u1", display_name="P1", text="hi",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertEqual(message.payload["rag_context"], "")
        self.assertEqual(message.payload["rag_status"], "empty")
        self.assertEqual(message.payload["memory_context"], "")
        self.assertEqual(message.payload["memory_status"], "empty")

    def test_scenario_context_budget_keeps_structural_boundaries(self):
        from app import keeper

        scenario = "--- 第 1 頁 ---\n第一頁完整內容\n--- 第 2 頁 ---\n第二頁完整內容"
        with patch.object(keeper, "MAX_SCENARIO_CHARS", 30):
            bounded = keeper._bounded_scenario_context(scenario)

        self.assertLessEqual(len(bounded), 30)
        self.assertIn("第一頁完整內容", bounded)
        self.assertNotIn("第二頁完整內容", bounded)


class SupervisorMechanicResultPayloadTests(unittest.IsolatedAsyncioTestCase):
    """Regression test for the review finding that supervisor.run_turn
    computed mechanic_result from the Executor's tool calls but never wrote
    it back into message.payload — narrator.py falls back to
    PURE_ROLEPLAY_BLOCK whenever it's missing, so every GAMEPLAY_ACTION turn
    was silently narrating as if nothing mechanical had happened."""

    async def test_mechanic_result_is_written_to_payload_for_gameplay_action(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "P1", "text": "attack",
            "resolved_location": None, "speaker_role": "player", "state": state,
            "character": None, "rag_context": "", "memory_context": "",
        })
        fake_result = MechanicResult(
            success=True, action_type="skill_check", narrative_facts=["rolled a 42, success"],
            state_delta=StateDelta(),
        )
        captured_payload: dict = {}

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_executor(msg):
            return fake_result

        async def fake_run_narrator(msg):
            captured_payload.update(msg.payload)
            return "narration", [], []

        with patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
                patch.object(supervisor.executor, "run_executor", fake_run_executor), \
                patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            await supervisor.run_turn(
                state=state, user_id="u1", display_name="P1", text="attack",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertIs(captured_payload.get("mechanic_result"), fake_result)

    async def test_supervisor_corrects_narration_that_requests_roll_without_pending_check(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "P1",
            "text": "嘗試撬門", "resolved_location": None, "speaker_role": "player",
            "state": state, "character": None, "rag_context": "", "memory_context": "",
        })
        fake_result = MechanicResult(
            success=True, action_type="none", narrative_facts=["search_scenario 成功"],
            state_delta=StateDelta(), check_status={"tool_called": False, "pending": None},
        )

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_narrator(_msg):
            return "請使用 /coc check 擲骰。", [], []

        with patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
                patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=fake_result)), \
                patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            reply, _, _ = await supervisor.run_turn(
                state=state, user_id="u1", display_name="P1", text="嘗試撬門",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertIn("沒有建立待處理檢定", reply)
        self.assertNotIn("請使用 /coc check 擲骰", reply)

    async def test_supervisor_replaces_denial_of_registered_pending_check(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g")
        state.pending_checks["u1"] = {"investigator": "P1", "skill": "STR"}
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "P1",
            "text": "嘗試撬門", "resolved_location": None, "speaker_role": "player",
            "state": state, "character": None, "rag_context": "", "memory_context": "",
        })
        fake_result = MechanicResult(
            success=True, action_type="tool_calls", narrative_facts=["skill_check 成功：pending=True"],
            state_delta=StateDelta(),
            check_status={"tool_called": True, "pending": {"investigator": "P1", "skill": "STR"}},
        )

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_narrator(_msg):
            return "STR 檢定尚未建立，請守密人重新建立。", [], []

        with patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
                patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=fake_result)), \
                patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            reply, _, _ = await supervisor.run_turn(
                state=state, user_id="u1", display_name="P1", text="嘗試撬門",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertIn("P1", reply)
        self.assertIn("已建立", reply)
        self.assertIn("/coc check", reply)

    async def test_supervisor_passes_existing_pending_luck_and_suppresses_new_roll(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g")
        state.pending_luck_decisions["u1"] = {
            "skill_name": "STR", "value": 40, "roll": 69,
            "original_tier": "failure", "difficulty": "regular",
            "options": [{"tier": "regular", "cost": 29}], "decision_id": "decision-1",
        }
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "Mick",
            "text": "再用大槌敲牆", "resolved_location": None, "speaker_role": "player",
            "state": state, "character": None, "rag_context": "", "memory_context": "",
        })
        fake_result = MechanicResult(
            success=True, action_type="tool_calls",
            narrative_facts=["skill_check 失敗：仍在等待 Luck 決定"],
            state_delta=StateDelta(),
            check_status={"tool_called": True, "pending": None, "pending_luck": None, "resolved": None},
        )

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_narrator(msg):
            self.assertEqual(
                msg.payload["mechanic_result"].check_status["pending_luck"]["roll"], 69
            )
            return "這次檢定尚未建立，請使用 /coc check。", [], []

        with patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
                patch.object(supervisor.executor, "run_executor", AsyncMock(return_value=fake_result)), \
                patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            reply, _, _ = await supervisor.run_turn(
                state=state, user_id="u1", display_name="Mick", text="再用大槌敲牆",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertIn("仍等待 Luck 決定", reply)
        self.assertIn("/coc luck skip", reply)
        self.assertIn("/coc luck regular", reply)
        self.assertNotIn("/coc check", reply)

    async def test_supervisor_uses_new_pending_check_owned_by_another_player(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "speaker", "display_name": "Speaker",
            "text": "攻擊者造成重傷", "resolved_location": None, "speaker_role": "player",
            "state": state, "character": None, "rag_context": "", "memory_context": "",
        })
        fake_result = MechanicResult(
            success=True, action_type="tool_calls", narrative_facts=["CON check pending"],
            state_delta=StateDelta(), check_status={"tool_called": True, "pending": None},
        )

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_executor(_msg):
            state.pending_checks["target"] = {
                "investigator": "Target", "skill": "CON", "difficulty": "regular",
            }
            return fake_result

        async def fake_run_narrator(msg):
            self.assertEqual(msg.payload["mechanic_result"].check_status["pending"]["investigator"], "Target")
            return "Target 的 CON 檢定尚未建立。", [], []

        with patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-test"), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION"), \
                patch.object(supervisor.executor, "run_executor", fake_run_executor), \
                patch.object(supervisor.state_reducer, "apply_mechanic_result", lambda *a, **k: None), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            reply, _, _ = await supervisor.run_turn(
                state=state, user_id="speaker", display_name="Speaker", text="攻擊者造成重傷",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertIn("Target", reply)
        self.assertIn("已建立", reply)

    async def test_supervisor_passes_captured_timeline_to_canonical_commit(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g", game_started=True)
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "P1", "text": "attack",
            "resolved_location": None, "speaker_role": "player", "state": state,
            "character": None, "rag_context": "", "memory_context": "",
        })
        commit_kwargs: dict = {}

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_narrator(msg):
            return "narration", [], []

        def fake_commit(*args, **kwargs):
            commit_kwargs.update(kwargs)
            return True

        with patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-captured"), \
                patch.object(supervisor.keeper, "_commit_turn_result", side_effect=fake_commit), \
                patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="PURE_ROLEPLAY"), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            result = await supervisor.run_turn(
                state=state, user_id="u1", display_name="P1", text="attack",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertEqual(result, ("narration", [], []))
        self.assertEqual(commit_kwargs["timeline_id"], "timeline-captured")

    async def test_supervisor_suppresses_stale_reply_when_canonical_commit_is_rejected(self):
        from app.agents import supervisor
        from app.domain.models import AgentMessage

        state = GroupState(group_id="g", game_started=True)
        message = AgentMessage(payload={
            "conversation_id": "g", "user_id": "u1", "display_name": "P1", "text": "attack",
            "resolved_location": None, "speaker_role": "player", "state": state,
            "character": None, "rag_context": "", "memory_context": "",
        })

        async def fake_build_context(**kwargs):
            return message

        async def fake_run_narrator(msg):
            return "stale narration", [("p2", "private")], [(None, 1)]

        with patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="timeline-captured"), \
                patch.object(supervisor.keeper, "_commit_turn_result", return_value=False), \
                patch.object(supervisor.context_builder, "build_context", fake_build_context), \
                patch.object(supervisor.intent_router, "classify_intent", return_value="PURE_ROLEPLAY"), \
                patch.object(supervisor.narrator, "run_narrator", fake_run_narrator), \
                patch.object(supervisor.guard, "enforce_narrative_safety", AsyncMock(side_effect=lambda _msg, text: text)):
            result = await supervisor.run_turn(
                state=state, user_id="u1", display_name="P1", text="attack",
                resolved_location=None, speaker_role="player", conversation_id="g",
            )

        self.assertEqual(result[1:], ([], []))
        self.assertIn("時間線已經更新", result[0])


if __name__ == "__main__":
    unittest.main()
