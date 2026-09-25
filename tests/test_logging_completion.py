import asyncio
import contextlib
import importlib.util
import logging
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
            run_conversation=AsyncMock(return_value="keeper reply"),
        )
        state = GroupState(group_id="g")
        with patch.object(config, "LOG_ENABLED", True), patch.object(config, "KEEPER_REASONING_EFFORT", "high"), \
                patch.object(keeper, "LLM_PROVIDER", "anthropic"), patch.object(keeper, "_PROVIDERS", {"anthropic": provider}), \
                self.assertLogs("app.observability", level="INFO") as captured:
            asyncio.run(keeper.run_turn(state, "u1", "Player", "look around"))

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

    def test_cancelled_span_is_recorded_as_cancelled_not_failed(self):
        with patch.object(config, "LOG_ENABLED", True), \
                self.assertLogs("app.observability", level="WARNING") as captured, \
                self.assertRaises(asyncio.CancelledError), \
                observability.span("test.operation", level=logging.WARNING):
            raise asyncio.CancelledError

        cancelled = [record for record in captured.records if record.getMessage() == "test.operation.cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0].structured_event["status"], "cancelled")

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
            "query_embedding_status": "not_used",
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

    def test_anthropic_usage_can_be_disabled(self):
        from app.providers import anthropic_provider

        response = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=4, output_tokens=6),
            content=[SimpleNamespace(type="text", text="done")],
        )
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
        client.messages.create = AsyncMock(return_value=response)
        fake_anthropic = types.SimpleNamespace(AsyncAnthropic=lambda **kwargs: client)
        captured_metrics: list[dict] = []

        @contextlib.contextmanager
        def fake_span(name, **kwargs):
            captured_metrics.append(kwargs.get("metrics", {}))
            yield

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}), \
                patch.object(anthropic_provider, "ANTHROPIC_API_KEY", "test-key"), \
                patch.object(anthropic_provider, "LOG_INCLUDE_USAGE", False), \
                patch.object(anthropic_provider.observability, "span", fake_span), \
                patch.object(anthropic_provider.observability, "event") as event:
            async def execute_tool(_name, _args):
                return {}

            result = asyncio.run(anthropic_provider.run_conversation(
                "static", "dynamic", [], [], "hello", execute_tool, 1
            ))
            asyncio.run(anthropic_provider.shutdown_async_client())

        self.assertEqual(result, "done")
        # One aggregate request span plus its per-attempt span.
        self.assertEqual(captured_metrics, [{}, {}])
        self.assertFalse(any(call.args[0] == "llm.usage" for call in event.call_args_list))

    def test_gemini_usage_can_be_disabled(self):
        from app.providers import gemini_provider

        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                cached_content_token_count=4,
                candidates_token_count=6,
                thoughts_token_count=2,
            ),
            candidates=[SimpleNamespace(content=SimpleNamespace())],
            function_calls=[],
            text="done",
        )
        client = SimpleNamespace(
            models=SimpleNamespace(generate_content=AsyncMock(return_value=response))
        )
        client.aio = client
        fake_genai = types.SimpleNamespace(Client=lambda **kwargs: client)
        fake_types = types.SimpleNamespace(
            FunctionDeclaration=lambda **kwargs: SimpleNamespace(**kwargs),
            Tool=lambda **kwargs: SimpleNamespace(**kwargs),
            GenerateContentConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            Content=lambda **kwargs: SimpleNamespace(**kwargs),
            Part=lambda **kwargs: SimpleNamespace(**kwargs),
        )
        fake_google = types.ModuleType("google")
        fake_google.genai = fake_genai
        fake_google_genai = types.ModuleType("google.genai")
        fake_google_genai.types = fake_types
        captured_metrics: list[dict] = []

        @contextlib.contextmanager
        def fake_span(name, **kwargs):
            captured_metrics.append(kwargs.get("metrics", {}))
            yield

        with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_google_genai}), \
                patch.object(gemini_provider, "GEMINI_API_KEY", "test-key"), \
                patch.object(gemini_provider, "LOG_INCLUDE_USAGE", False), \
                patch.object(gemini_provider.observability, "span", fake_span), \
                patch.object(gemini_provider.observability, "event") as event:
            async def execute_tool(_name, _args):
                return {}

            result = asyncio.run(gemini_provider.run_conversation(
                "static", "dynamic", [], [], "hello", execute_tool, 1
            ))
            asyncio.run(gemini_provider.shutdown_async_client())

        self.assertEqual(result, "done")
        self.assertEqual(captured_metrics, [{}, {}])
        self.assertFalse(any(call.args[0] == "llm.usage" for call in event.call_args_list))


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
        with (
            patch.object(config, "LOG_ENABLED", True),
            patch.object(config, "LOG_SLOW_OPERATION_MS", 10),
            self.assertLogs("app.observability", level="INFO") as captured,
            observability.metrics_context(metrics),
        ):
            await _send_direct_message(channel, "按鈕提示", view="view")

        channel.send.assert_awaited_once_with("按鈕提示", view="view")
        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_chunk_count"], 1)
        self.assertEqual(metrics["reply_edit_count"], 0)
        self.assertTrue(any("discord.reply.completed" in line for line in captured.output))

    async def test_direct_image_has_complete_reply_metrics(self):
        """Regression guard: _record_reply_binary only ever incremented
        reply_message_count/reply_bytes — same gap as the two tests above
        had for _record_reply_output/_record_reply_edit, just not covered
        by any test until now."""
        from app.discord_bot import _send_direct_image

        channel = SimpleNamespace(send=AsyncMock())
        metrics = {}
        with patch.object(config, "LOG_ENABLED", True), observability.metrics_context(metrics):
            await _send_direct_image(channel, b"\x89PNG", 1)

        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_bytes"], 4)
        self.assertEqual(metrics["reply_chunk_count"], 0)
        self.assertEqual(metrics["reply_edit_count"], 0)

    async def test_help_edit_counts_edit_not_new_message(self):
        from app.discord_bot import _edit_interaction_message

        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        metrics = {}
        with patch.object(config, "LOG_ENABLED", True), observability.metrics_context(metrics):
            await _edit_interaction_message(interaction, "Help 內容", view="view")

        interaction.response.edit_message.assert_awaited_once_with(content="Help 內容", view="view")
        self.assertEqual(metrics["reply_edit_count"], 1)
        self.assertEqual(metrics["reply_message_count"], 0)
        self.assertEqual(metrics["reply_chunk_count"], 0)

    async def test_direct_output_failure_does_not_count_successful_output(self):
        from app.discord_bot import _send_direct_message

        channel = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("discord unavailable")))
        metrics = {}
        with (
            patch.object(config, "LOG_ENABLED", True),
            observability.metrics_context(metrics),
            self.assertRaises(RuntimeError),
        ):
            await _send_direct_message(channel, "not sent")
        self.assertEqual(metrics, {})

    async def test_discord_operation_timeout_is_logged_without_retrying(self):
        from app.discord_bot import _send_direct_message

        async def slow_send(*_args, **_kwargs):
            await asyncio.sleep(0.05)

        channel = SimpleNamespace(send=slow_send)
        metrics = {}
        with (
            patch.object(config, "LOG_ENABLED", True),
            patch.object(config, "DISCORD_REQUEST_TIMEOUT_SECONDS", 0.001),
            observability.metrics_context(metrics),
            self.assertRaises(asyncio.TimeoutError),
            self.assertLogs("app.observability", level="ERROR") as captured,
        ):
            await _send_direct_message(channel, "timeout")

        self.assertTrue(any("discord.request.timeout" in line for line in captured.output))
        self.assertEqual(metrics, {})

    async def test_partial_chunk_failure_counts_only_successful_chunks(self):
        from app.discord_bot import _make_reply

        channel = SimpleNamespace(send=AsyncMock(side_effect=[None, RuntimeError("discord unavailable")]))
        metrics = {}
        text = "a" * 1900 + "b"
        with (
            patch.object(config, "LOG_ENABLED", True),
            observability.metrics_context(metrics),
            self.assertRaises(RuntimeError),
        ):
            await _make_reply(channel)(text)

        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_chunk_count"], 1)
        self.assertEqual(metrics["reply_bytes"], 1900)
        self.assertEqual(metrics["reply_edit_count"], 0)

    async def test_interaction_followup_partial_failure_counts_only_successful_chunks(self):
        from app.discord_bot import _make_interaction_reply

        interaction = SimpleNamespace(
            followup=SimpleNamespace(
                send=AsyncMock(side_effect=[None, RuntimeError("discord unavailable")])
            )
        )
        metrics = {}
        text = "a" * 1900 + "b"
        with (
            patch.object(config, "LOG_ENABLED", True),
            observability.metrics_context(metrics),
            self.assertRaises(RuntimeError),
        ):
            await _make_interaction_reply(interaction)(text)

        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_chunk_count"], 1)
        self.assertEqual(metrics["reply_bytes"], 1900)
        self.assertEqual(metrics["reply_edit_count"], 0)

    async def test_interaction_followup_output_has_a_latency_span(self):
        from app.discord_bot import _make_interaction_reply

        interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
        metrics = {}
        with (
            patch.object(config, "LOG_ENABLED", True),
            observability.metrics_context(metrics),
            self.assertLogs("app.observability", level="INFO") as captured,
        ):
            await _make_interaction_reply(interaction)("follow-up")

        self.assertTrue(any("discord.reply.completed" in line for line in captured.output))
        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_edit_count"], 0)

    async def test_direct_dm_output_has_a_latency_span_and_metrics(self):
        from app import discord_bot

        user = SimpleNamespace(send=AsyncMock())
        metrics = {}
        with (
            patch.object(config, "LOG_ENABLED", True),
            patch.object(discord_bot.client, "get_user", return_value=user),
            observability.metrics_context(metrics),
            self.assertLogs("app.observability", level="INFO") as captured,
        ):
            await discord_bot._send_dm("123", "private reply")

        self.assertTrue(any("discord.reply.completed" in line for line in captured.output))
        self.assertEqual(metrics["reply_message_count"], 1)
        self.assertEqual(metrics["reply_chunk_count"], 1)
        self.assertEqual(metrics["reply_edit_count"], 0)

    async def test_bot_shutdown_runs_all_cleanup_when_discord_close_fails(self):
        from app import discord_bot

        fake_client = SimpleNamespace(
            start=AsyncMock(side_effect=RuntimeError("gateway failed")),
            is_closed=lambda: False,
            close=AsyncMock(side_effect=RuntimeError("close failed")),
        )
        with patch.object(discord_bot, "client", fake_client), \
                patch.object(discord_bot.scenario_rag, "shutdown_prewarm", new_callable=AsyncMock) as shutdown_prewarm, \
                patch.object(discord_bot.async_utils, "wait_for_background_tasks", new_callable=AsyncMock) as wait_background_tasks, \
                patch.object(discord_bot.providers, "shutdown_async_clients", new_callable=AsyncMock) as shutdown_providers, \
                self.assertRaises(RuntimeError):
            await discord_bot._run_bot()

        shutdown_prewarm.assert_awaited_once()
        wait_background_tasks.assert_awaited_once()
        shutdown_providers.assert_awaited_once()

    async def test_request_metrics_have_stable_zero_defaults(self):
        from app.discord_bot import _request_metrics

        with patch.object(config, "LOG_ENABLED", True):
            self.assertEqual(_request_metrics(), {
                "reply_message_count": 0,
                "reply_edit_count": 0,
                "reply_chunk_count": 0,
                "reply_bytes": 0,
            })

    async def test_luck_buttons_include_every_affordable_option(self):
        from app import discord_bot

        class FakeView:
            def __init__(self, timeout=None):
                self.items = []

            def add_item(self, item):
                self.items.append(item)

        class FakeButton:
            def __init__(self, conversation_id, owner_id, label, choice, danger=False, decision_id=""):
                self.args = (conversation_id, owner_id, label, choice, danger, decision_id)

        state = GroupState(group_id="g")
        state.pending_luck_decisions["123"] = {
            "options": [
                {"cost": 1, "tier": "regular"},
                {"cost": 2, "tier": "hard"},
                {"cost": 3, "tier": "extreme"},
            ]
        }
        channel = SimpleNamespace()
        before_pending = {}
        send = AsyncMock()

        with patch.object(discord_bot.discord.ui, "View", FakeView), \
                patch.object(discord_bot, "LuckSpendButton", FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send), \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", MagicMock()):
            await discord_bot._post_luck_buttons(
                channel,
                "discord-channel-1",
                state,
                before_pending,
                "【KP Assistant 代操作：小明】",
            )

        send.assert_awaited_once()
        self.assertTrue(send.await_args.args[1].startswith("【KP Assistant 代操作：小明】\n"))
        view = send.await_args.kwargs["view"]
        self.assertEqual([item.args[3] for item in view.items], ["regular", "hard", "extreme", "skip"])

    async def test_luck_button_posted_only_once_when_two_overlapping_calls_race(self):
        """Real-incident finding: every caller of _post_pending_buttons
        (CheckButton/LuckSpendButton callbacks, on_message) snapshots its
        own before_pending locally and only diffs against that — with no
        cross-call marker, two overlapping request-handling flows for the
        same conversation (e.g. one player's button click still in flight
        when another player's message finishes processing) could each
        independently conclude "this decision is new to me" and both post
        a button for the same Luck decision. See docs/specs/bug-duplicate-
        luck-button-prompt.md for the real Discord transcript this
        reproduces."""
        from app import discord_bot

        class FakeView:
            def __init__(self, timeout=None):
                self.items = []

            def add_item(self, item):
                self.items.append(item)

        class FakeButton:
            def __init__(self, conversation_id, owner_id, label, choice, danger=False, decision_id=""):
                pass

        state = GroupState(group_id="g")
        state.pending_luck_decisions["123"] = {
            "options": [{"cost": 1, "tier": "regular"}],
        }
        channel = SimpleNamespace()
        send = AsyncMock()
        save = MagicMock()

        with patch.object(discord_bot.discord.ui, "View", FakeView), \
                patch.object(discord_bot, "LuckSpendButton", FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send), \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", save):
            # Two overlapping callers, each with its own stale before-
            # snapshot captured before the decision existed — exactly what
            # a genuine race between two concurrent request-handling flows
            # looks like from _post_luck_buttons' point of view.
            await discord_bot._post_luck_buttons(channel, "discord-channel-1", state, {})
            await discord_bot._post_luck_buttons(channel, "discord-channel-1", state, {})

        send.assert_awaited_once()
        save.assert_called_once()

    async def test_check_button_posted_only_once_when_two_overlapping_calls_race(self):
        from app import discord_bot

        class FakeView:
            def __init__(self, timeout=None):
                self.items = []

            def add_item(self, item):
                self.items.append(item)

        class FakeButton:
            def __init__(self, conversation_id, owner_id, label, danger, option, check_id):
                pass

        state = GroupState(group_id="g")
        state.pending_checks["123"] = {"type": "skill", "skill": "閃避", "skill_value": 30}
        channel = SimpleNamespace()
        send = AsyncMock()
        save = MagicMock()

        with patch.object(discord_bot.discord.ui, "View", FakeView), \
                patch.object(discord_bot, "CheckButton", FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send), \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", save):
            await discord_bot._post_check_buttons(channel, "discord-channel-1", state, {})
            await discord_bot._post_check_buttons(channel, "discord-channel-1", state, {})

        send.assert_awaited_once()
        save.assert_called_once()

    async def test_luck_button_still_posts_normally_for_a_single_non_overlapping_call(self):
        """Regression guard: the durable marker must not break the plain,
        common case of exactly one caller posting exactly one button."""
        from app import discord_bot

        class FakeView:
            def __init__(self, timeout=None):
                self.items = []

            def add_item(self, item):
                self.items.append(item)

        class FakeButton:
            def __init__(self, conversation_id, owner_id, label, choice, danger=False, decision_id=""):
                pass

        state = GroupState(group_id="g")
        state.pending_luck_decisions["123"] = {
            "options": [{"cost": 1, "tier": "regular"}],
        }
        channel = SimpleNamespace()
        send = AsyncMock()
        save = MagicMock()

        with patch.object(discord_bot.discord.ui, "View", FakeView), \
                patch.object(discord_bot, "LuckSpendButton", FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send), \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", save):
            await discord_bot._post_luck_buttons(channel, "discord-channel-1", state, {})

        send.assert_awaited_once()

    async def test_legacy_check_button_identity_survives_being_marked_posted(self):
        """Review finding on PR #78: for entries without an explicit
        check_id (legacy persisted checks, and the opening-scene checks
        app/commands/handlers/system.py registers without one — see
        :627-641 there), the button's identity token must stay derivable
        from the persisted (now-marked-posted) entry. _legacy_id's hash
        used to include "_buttons_posted", so the token computed before
        the entry was marked (what the button was actually built with)
        differed from the token recomputed after (what a callback
        verifies against) — every such button would be rejected as
        expired on the very first click."""
        from app import check_identity, discord_bot

        captured_check_id: dict[str, str] = {}

        class FakeView:
            def __init__(self, timeout=None):
                self.items = []

            def add_item(self, item):
                self.items.append(item)

        class FakeButton:
            def __init__(self, conversation_id, owner_id, label, danger, option, check_id):
                captured_check_id["value"] = check_id

        state = GroupState(group_id="g")
        state.pending_checks["123"] = {"type": "skill", "skill": "閃避", "skill_value": 30}
        channel = SimpleNamespace()
        send = AsyncMock()
        save = MagicMock()
        conversation_id = "discord-channel-1"

        with patch.object(discord_bot.discord.ui, "View", FakeView), \
                patch.object(discord_bot, "CheckButton", FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send), \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", save):
            await discord_bot._post_check_buttons(channel, conversation_id, state, {})

        marked_entry = state.pending_checks["123"]
        self.assertTrue(marked_entry.get("_buttons_posted"))
        timeline_id = state.timeline_id or f"legacy-{conversation_id}"
        recomputed_full_id = check_identity.effective_check_id("123", marked_entry, timeline_id)
        recomputed_token = check_identity.compact_identity_token("check", "123", recomputed_full_id, timeline_id)
        self.assertEqual(recomputed_token, captured_check_id["value"])

    async def test_stranded_posting_claim_is_released_so_a_later_call_can_repost(self):
        """Review finding on PR #78: if _send_direct_message raises after
        the _buttons_posted claim was already saved (an exhausted rate-
        limit/network retry, or the process exiting between the save and
        the send), the entry must not be permanently stranded — a later
        call must still be able to post it."""
        from app import discord_bot

        class FakeView:
            def __init__(self, timeout=None):
                self.items = []

            def add_item(self, item):
                self.items.append(item)

        class FakeButton:
            def __init__(self, conversation_id, owner_id, label, choice, danger=False, decision_id=""):
                pass

        state = GroupState(group_id="g")
        state.pending_luck_decisions["123"] = {
            "options": [{"cost": 1, "tier": "regular"}],
        }
        channel = SimpleNamespace()
        send = AsyncMock(side_effect=[RuntimeError("simulated Discord send failure"), None])
        save = MagicMock()

        with patch.object(discord_bot.discord.ui, "View", FakeView), \
                patch.object(discord_bot, "LuckSpendButton", FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send), \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", save):
            # First attempt: claim gets saved, then the send fails.
            await discord_bot._post_luck_buttons(channel, "discord-channel-1", state, {})
            self.assertNotIn("_buttons_posted", state.pending_luck_decisions.get("123", {}))

            # A later, independent call (its own fresh before_pending) must
            # still be able to post it — not permanently skipped.
            await discord_bot._post_luck_buttons(channel, "discord-channel-1", state, {})

        self.assertEqual(send.await_count, 2)
        self.assertTrue(state.pending_luck_decisions["123"].get("_buttons_posted"))


class AgentLifecycleLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_and_narrator_emit_turn_lifecycles(self):
        from app.agents import executor, narrator

        provider = SimpleNamespace(
            ANTHROPIC_MODEL="agent-model",
            run_conversation=AsyncMock(return_value="agent response"),
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
            run_conversation=AsyncMock(return_value="repaired"),
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
