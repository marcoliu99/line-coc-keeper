import importlib.util
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import config, keeper, memory_rag, observability, scenario_rag
from app.domain.models import AgentMessage
from app.models import GroupState


DISCORD_AVAILABLE = importlib.util.find_spec("discord") is not None


class LoggingCompletionTests(unittest.TestCase):
    def tearDown(self):
        observability._CONTEXT.set({})
        observability._METRICS.set({})

    def test_legacy_keeper_turn_has_one_complete_turn_lifecycle(self):
        provider = SimpleNamespace(
            ANTHROPIC_MODEL="test-model",
            run_conversation=lambda *args, **kwargs: "keeper reply",
        )
        state = GroupState(group_id="g")
        with patch.object(config, "LOG_ENABLED", True), patch.object(config, "KEEPER_REASONING_EFFORT", "high"), \
                patch.object(keeper, "LLM_PROVIDER", "anthropic"), patch.object(keeper, "_PROVIDERS", {"anthropic": provider}), \
                self.assertLogs("app.observability", level="INFO") as captured:
            keeper.run_turn(state, "u1", "Player", "look around")

        turn_started = [record for record in captured.records if record.getMessage() == "llm.turn.started"]
        turn_completed = [record for record in captured.records if record.getMessage() == "llm.turn.completed"]
        self.assertEqual(len(turn_started), 1)
        self.assertEqual(len(turn_completed), 1)
        event = turn_completed[0].structured_event
        self.assertEqual(event["agent"], "keeper")
        self.assertIsNone(event["reasoning_effort"])
        self.assertTrue(event["turn_id"].startswith("turn_"))

    def test_reasoning_effort_is_provider_specific(self):
        with patch.object(config, "KEEPER_REASONING_EFFORT", "xhigh"):
            self.assertEqual(observability.llm_reasoning_effort("openai"), "xhigh")
            self.assertIsNone(observability.llm_reasoning_effort("anthropic"))
            self.assertIsNone(observability.llm_reasoning_effort("gemini"))

    def test_memory_index_metrics_report_empty_rebuilt_and_cached(self):
        raw_chunks = [{"label": "記憶片段 #1", "text": "秘密房間有一把鑰匙"}]
        memory_rag._index_cache.clear()
        with patch.object(memory_rag, "_load_raw_chunks", return_value=raw_chunks):
            first_metrics = {}
            first = memory_rag.search_memory("g", "秘密房間", metrics=first_metrics)
            second_metrics = {}
            second = memory_rag.search_memory("g", "秘密房間", metrics=second_metrics)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first_metrics["index_cache"], "rebuilt")
        self.assertEqual(second_metrics["index_cache"], "memory")
        self.assertEqual(first_metrics["candidate_count"], 1)
        self.assertEqual(first_metrics["result_count"], 1)

        with patch.object(memory_rag, "_load_raw_chunks", return_value=[]):
            empty_metrics = {}
            self.assertEqual(memory_rag.search_memory("empty", "anything", metrics=empty_metrics), [])
        self.assertEqual(empty_metrics, {
            "index_cache": "empty", "candidate_count": 0,
            "has_embeddings": False, "result_count": 0,
        })

    def test_incomplete_embedding_response_emits_fallback_for_memory_and_scenario(self):
        client = SimpleNamespace(
            embeddings=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])]
                )
            )
        )
        fake_openai = types.SimpleNamespace(OpenAI=lambda **kwargs: client)
        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(memory_rag, "OPENAI_API_KEY", "test-key"), \
                patch.object(scenario_rag, "OPENAI_API_KEY", "test-key"), \
                patch.object(memory_rag.observability, "event") as event:
            self.assertIsNone(memory_rag._embed_texts(["one", "two"]))
            self.assertIsNone(scenario_rag._embed_texts(["one", "two"]))

        self.assertEqual(event.call_count, 2)
        self.assertTrue(all(call.args[0] == "rag.embedding_fallback" for call in event.call_args_list))
        self.assertTrue(all(
            call.kwargs["error_type"] == "incomplete_embedding_response"
            for call in event.call_args_list
        ))
        self.assertEqual(
            {call.kwargs["rag_kind"] for call in event.call_args_list}, {"memory", "scenario"}
        )


@unittest.skipUnless(DISCORD_AVAILABLE, "discord.py is not installed")
class DiscordOutputLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        observability._CONTEXT.set({})
        observability._METRICS.set({})

    async def asyncTearDown(self):
        observability._CONTEXT.set({})
        observability._METRICS.set({})

    async def test_direct_message_has_reply_span_and_new_message_metrics(self):
        from app.discord_bot import _send_direct_message

        channel = SimpleNamespace(send=AsyncMock())
        metrics = {}
        with patch.object(config, "LOG_ENABLED", True), patch.object(config, "LOG_SLOW_OPERATION_MS", 10), \
                self.assertLogs("app.observability", level="INFO") as captured:
            with observability.metrics_context(metrics):
                await _send_direct_message(channel, "按鈕提示", view="view")

        channel.send.assert_awaited_once_with("按鈕提示", view="view")
        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_chunk_count"], 1)
        self.assertEqual(metrics["reply_edit_count"], 0)
        self.assertTrue(any("discord.reply.completed" in line for line in captured.output))

    async def test_help_edit_counts_edit_not_new_message(self):
        from app.discord_bot import _edit_interaction_message

        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        metrics = {}
        with patch.object(config, "LOG_ENABLED", True):
            with observability.metrics_context(metrics):
                await _edit_interaction_message(interaction, "Help 內容", view="view")

        interaction.response.edit_message.assert_awaited_once_with(content="Help 內容", view="view")
        self.assertEqual(metrics["reply_edit_count"], 1)
        self.assertEqual(metrics["reply_message_count"], 0)
        self.assertEqual(metrics["reply_chunk_count"], 0)

    async def test_direct_output_failure_does_not_count_successful_output(self):
        from app.discord_bot import _send_direct_message

        channel = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("discord unavailable")))
        metrics = {}
        with patch.object(config, "LOG_ENABLED", True):
            with observability.metrics_context(metrics):
                with self.assertRaises(RuntimeError):
                    await _send_direct_message(channel, "not sent")
        self.assertEqual(metrics, {})

    async def test_partial_chunk_failure_counts_only_successful_chunks(self):
        from app.discord_bot import _make_reply

        channel = SimpleNamespace(send=AsyncMock(side_effect=[None, RuntimeError("discord unavailable")]))
        metrics = {}
        text = "a" * 1900 + "b"
        with patch.object(config, "LOG_ENABLED", True):
            with observability.metrics_context(metrics):
                with self.assertRaises(RuntimeError):
                    await _make_reply(channel)(text)

        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_chunk_count"], 1)
        self.assertEqual(metrics["reply_bytes"], 1900)

    async def test_request_metrics_have_stable_zero_defaults(self):
        from app.discord_bot import _request_metrics

        with patch.object(config, "LOG_ENABLED", True):
            self.assertEqual(_request_metrics(), {
                "reply_message_count": 0,
                "reply_edit_count": 0,
                "reply_chunk_count": 0,
                "reply_bytes": 0,
            })


class AgentLifecycleLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_and_narrator_emit_turn_lifecycles(self):
        from app.agents import executor, narrator

        provider = SimpleNamespace(
            ANTHROPIC_MODEL="agent-model",
            run_conversation=lambda *args, **kwargs: "agent response",
        )
        state = GroupState(group_id="g")
        executor_message = AgentMessage(payload={
            "state": state, "text": "look", "user_id": "u1", "display_name": "P1",
            "speaker_role": "player", "resolved_location": None,
            "rag_context": "", "memory_context": "",
        })
        narrator_message = AgentMessage(payload={
            "state": state, "text": "look", "user_id": "u1", "display_name": "P1",
            "speaker_role": "player", "resolved_location": None,
            "intent": "PURE_ROLEPLAY", "rag_context": "", "memory_context": "",
        })
        with patch.object(config, "LOG_ENABLED", True), patch.object(executor, "LLM_PROVIDER", "anthropic"), \
                patch.object(executor, "_PROVIDERS", {"anthropic": provider}), \
                patch.object(narrator, "LLM_PROVIDER", "anthropic"), \
                patch.object(narrator, "_PROVIDERS", {"anthropic": provider}), \
                self.assertLogs("app.observability", level="INFO") as captured:
            await executor.run_executor(executor_message)
            await narrator.run_narrator(narrator_message)

        completed = [
            record.structured_event for record in captured.records
            if record.getMessage() == "llm.turn.completed"
        ]
        self.assertEqual({event["agent"] for event in completed}, {"executor", "narrator"})
        self.assertTrue(all("reasoning_effort" in event for event in completed))

    async def test_guard_emits_turn_lifecycle_and_reasoning_field(self):
        from app.agents import guard

        provider = SimpleNamespace(
            ANTHROPIC_MODEL="guard-model",
            run_conversation=lambda *args, **kwargs: "repaired",
        )
        message = AgentMessage(payload={"state": GroupState(group_id="g")})
        with patch.object(config, "LOG_ENABLED", True), patch.object(config, "KEEPER_REASONING_EFFORT", "high"), \
                patch.object(guard, "LLM_PROVIDER", "anthropic"), patch.object(guard, "_PROVIDERS", {"anthropic": provider}), \
                self.assertLogs("app.observability", level="INFO") as captured:
            result = await guard.run_repair(message, "原始敘述", "缺少規則結果")

        self.assertEqual(result, "repaired")
        completed = [record for record in captured.records if record.getMessage() == "llm.turn.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].structured_event["agent"], "guard")
        self.assertIsNone(completed[0].structured_event["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
