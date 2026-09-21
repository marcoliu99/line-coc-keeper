import io
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import config, logging_config, observability


class ObservabilityTests(unittest.TestCase):
    def tearDown(self):
        observability._CONTEXT.set({})

    def test_request_context_provides_stable_correlations_and_hashes_identity(self):
        with patch("app.config.LOG_ENABLED", True), patch("app.config.LOG_TEXT_ENABLED", True), patch(
            "app.config.LOG_HASH_IDENTIFIERS", True
        ):
            with observability.request_context(conversation_id="discord-channel-123") as bound:
                self.assertTrue(bound["request_id"].startswith("req_"))
                self.assertEqual(len(bound["conversation_id"]), 12)
                self.assertEqual(bound, observability.current_context())

                with observability.context(turn_id="turn_test"):
                    self.assertEqual(observability.current_context()["request_id"], bound["request_id"])
                    self.assertEqual(observability.current_context()["turn_id"], "turn_test")

            self.assertEqual(observability.current_context(), {})

    def test_structured_event_is_noop_when_performance_logging_disabled(self):
        with patch("app.config.LOG_ENABLED", False), self.assertNoLogs("app.observability"):
            observability.event("request.completed", duration_ms=123)

    def test_text_logging_can_remain_enabled_when_performance_logging_disabled(self):
        with (
            patch("app.config.LOG_ENABLED", False),
            patch("app.config.LOG_TEXT_ENABLED", True),
            observability.request_context(conversation_id="channel") as bound,
        ):
            self.assertTrue(bound["request_id"].startswith("req_"))

    def test_slow_span_is_promoted_to_warning(self):
        with (
            patch("app.config.LOG_ENABLED", True),
            self.assertLogs("app.observability", level="INFO") as captured,
            observability.span("state.load", slow_threshold_ms=0, operation="load"),
        ):
            pass
        self.assertTrue(any("state.load.completed" in line for line in captured.output))
        self.assertTrue(any("WARNING" in line for line in captured.output))

    def test_detached_context_does_not_inherit_request(self):
        with observability.request_context(conversation_id="channel") as bound:
            request_id = bound["request_id"]
            with observability.detached_context(maintenance_id="maintenance_test") as detached:
                self.assertNotEqual(detached.get("request_id"), request_id)
                self.assertEqual(detached["maintenance_id"], "maintenance_test")

    def test_detached_context_redacts_conversation_identifier(self):
        with patch.object(config, "LOG_HASH_IDENTIFIERS", True), observability.detached_context(
            conversation_id="discord-channel-123"
        ) as detached:
            self.assertEqual(len(detached["conversation_id"]), 12)
            self.assertNotIn("discord-channel-123", detached["conversation_id"])

    def test_queue_listener_preserves_context_captured_before_queueing(self):
        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        output = io.StringIO()
        for handler in saved_handlers:
            root.removeHandler(handler)

        try:
            with patch.object(config, "LOG_ENABLED", False), patch.object(config, "LOG_TEXT_ENABLED", True), \
                    patch.object(config, "LOG_FORMAT", "json"), patch.object(config, "LOG_FILE", ""), \
                    patch.object(config, "LOG_LEVEL", "INFO"), patch.object(logging_config.sys, "stderr", output):
                logging_config.configure_logging()
                with observability.request_context(conversation_id="discord-channel-123") as bound:
                    logging.getLogger("app.queue-context-test").info("diagnostic")
                logging_config._stop_listener()

            rendered = output.getvalue()
            self.assertIn(bound["request_id"], rendered)
            self.assertIn(bound["conversation_id"], rendered)
        finally:
            logging_config._stop_listener()
            for handler in root.handlers[:]:
                root.removeHandler(handler)
                handler.close()
            for handler in saved_handlers:
                root.addHandler(handler)
            root.setLevel(saved_level)

    def test_span_includes_status_and_mutable_metrics(self):
        with (
            patch("app.config.LOG_ENABLED", True),
            self.assertLogs("app.observability", level="INFO") as captured,
        ):
            metrics = {}
            with observability.span("rag.search", metrics=metrics):
                metrics["result_count"] = 2
        self.assertTrue(any("rag.search.completed" in line for line in captured.output))

    def test_usage_fields_normalize_openai_usage_shapes(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                input_tokens_details=SimpleNamespace(cached_tokens=80),
                output_tokens=40,
                output_tokens_details=SimpleNamespace(reasoning_tokens=15),
            )
        )
        self.assertEqual(
            observability.usage_fields(response),
            {
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 40,
                "reasoning_tokens": 15,
            },
        )

    def test_structured_formatter_keeps_event_fields_without_prompt_content(self):
        formatter_module = __import__("app.logging_config", fromlist=["StructuredFormatter"])
        record = logging.LogRecord(
            "app.test", logging.INFO, __file__, 1, "request.completed", (), None
        )
        record.structured_event = {
            "event": "request.completed",
            "duration_ms": 12.5,
            "status": "success",
        }
        rendered = formatter_module.StructuredFormatter().format(record)
        self.assertIn('"event":"request.completed"', rendered)
        self.assertIn('"duration_ms":12.5', rendered)
        self.assertNotIn("prompt", rendered)

    def test_context_filter_captures_context_before_queue_listener_formatting(self):
        formatter_module = __import__("app.logging_config", fromlist=["_ContextFilter", "TextFormatter"])
        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", (), None)
        with observability.request_context(conversation_id="channel") as bound:
            self.assertTrue(formatter_module._ContextFilter().filter(record))
            rendered = formatter_module.TextFormatter().format(record)
        self.assertIn(bound["request_id"], rendered)
        self.assertIn("conversation_id=", rendered)


if __name__ == "__main__":
    unittest.main()
