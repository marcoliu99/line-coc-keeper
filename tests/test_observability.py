import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import observability


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
        with patch("app.config.LOG_ENABLED", False), patch("app.config.LOG_TEXT_ENABLED", True):
            with observability.request_context(conversation_id="channel") as bound:
                self.assertTrue(bound["request_id"].startswith("req_"))

    def test_slow_span_is_promoted_to_warning(self):
        with patch("app.config.LOG_ENABLED", True):
            with self.assertLogs("app.observability", level="INFO") as captured:
                with observability.span("state.load", slow_threshold_ms=0, operation="load"):
                    pass
        self.assertTrue(any("state.load.completed" in line for line in captured.output))
        self.assertTrue(any("WARNING" in line for line in captured.output))

    def test_detached_context_does_not_inherit_request(self):
        with observability.request_context(conversation_id="channel") as bound:
            request_id = bound["request_id"]
            with observability.detached_context(maintenance_id="maintenance_test") as detached:
                self.assertNotEqual(detached.get("request_id"), request_id)
                self.assertEqual(detached["maintenance_id"], "maintenance_test")

    def test_span_includes_status_and_mutable_metrics(self):
        with patch("app.config.LOG_ENABLED", True):
            with self.assertLogs("app.observability", level="INFO") as captured:
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


if __name__ == "__main__":
    unittest.main()
