import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, patch

from app.agents import guard
from app.domain.models import AgentMessage


class EnforceNarrativeSafetyTests(unittest.TestCase):
    """docs/specs/enhancement-guard-agent.md: GUARD_ENABLED switch semantics
    and the fail-closed fix for the repair loop's original bug (it used to
    exit after MAX_REPAIR_ATTEMPTS without re-validating the last repair
    attempt, so a still-invalid narrative could slip through unnoticed)."""

    def test_valid_narrative_passes_through_without_any_repair_attempt(self):
        message = AgentMessage(payload={})
        with patch.object(guard, "run_repair", new_callable=AsyncMock) as mock_repair:
            result = asyncio.run(guard.enforce_narrative_safety(message, "你走進了圖書館。"))
        self.assertEqual(result, "你走進了圖書館。")
        mock_repair.assert_not_called()

    def test_guard_disabled_sends_unrepaired_text_without_calling_llm(self):
        message = AgentMessage(payload={})
        with patch.object(guard.config, "GUARD_ENABLED", False), \
                patch.object(guard, "run_repair", new_callable=AsyncMock) as mock_repair:
            result = asyncio.run(
                guard.enforce_narrative_safety(message, "[SYSTEM] 這是系統外洩的文字")
            )
        self.assertEqual(result, "[SYSTEM] 這是系統外洩的文字")
        mock_repair.assert_not_called()

    def test_first_repair_attempt_succeeds(self):
        message = AgentMessage(payload={})
        mock_repair = AsyncMock(return_value="這是修復後的合法敘述。")
        with patch.object(guard.config, "GUARD_ENABLED", True), \
                patch.object(guard, "run_repair", mock_repair):
            result = asyncio.run(
                guard.enforce_narrative_safety(message, "[SYSTEM] 這是系統外洩的文字")
            )
        self.assertEqual(result, "這是修復後的合法敘述。")
        mock_repair.assert_awaited_once()

    def test_repair_loop_exhausted_falls_back_to_neutral_text_not_last_bad_attempt(self):
        """Regression test for the original bug: the loop used to return the
        second repair attempt's text even when it was still invalid. It must
        now fall back to the fixed neutral text instead, and run_repair must
        be called exactly MAX_REPAIR_ATTEMPTS (2) times, not more."""
        message = AgentMessage(payload={})
        # Every repair attempt "fixes" nothing — still contains the leak marker.
        mock_repair = AsyncMock(return_value="[SYSTEM] 還是沒修好")
        with patch.object(guard.config, "GUARD_ENABLED", True), \
                patch.object(guard, "run_repair", mock_repair):
            result = asyncio.run(
                guard.enforce_narrative_safety(message, "[SYSTEM] 這是系統外洩的文字")
            )
        self.assertEqual(result, guard._LOOP_EXHAUSTED_FALLBACK_TEXT)
        self.assertNotIn("[SYSTEM]", result)
        self.assertEqual(mock_repair.await_count, guard.MAX_REPAIR_ATTEMPTS)

    def test_second_repair_attempt_succeeds_after_first_fails(self):
        message = AgentMessage(payload={})
        mock_repair = AsyncMock(
            side_effect=["[SYSTEM] 第一次還是沒修好", "這次終於修好了。"]
        )
        with patch.object(guard.config, "GUARD_ENABLED", True), \
                patch.object(guard, "run_repair", mock_repair):
            result = asyncio.run(
                guard.enforce_narrative_safety(message, "[SYSTEM] 這是系統外洩的文字")
            )
        self.assertEqual(result, "這次終於修好了。")
        self.assertEqual(mock_repair.await_count, 2)

    def test_guard_disabled_logs_warning_with_reason(self):
        message = AgentMessage(payload={})
        with patch.object(guard.config, "GUARD_ENABLED", False), \
                patch.object(guard, "_logger") as mock_logger, \
                patch.object(guard, "observability") as mock_observability:
            asyncio.run(guard.enforce_narrative_safety(message, "[SYSTEM] 洩漏"))
        mock_logger.warning.assert_called_once()
        mock_observability.event.assert_called_once()
        _, kwargs = mock_observability.event.call_args
        self.assertEqual(kwargs.get("level"), logging.WARNING)

    def test_repair_exhausted_logs_error_with_observability_event(self):
        message = AgentMessage(payload={})
        mock_repair = AsyncMock(return_value="[SYSTEM] 還是沒修好")
        with patch.object(guard.config, "GUARD_ENABLED", True), \
                patch.object(guard, "run_repair", mock_repair), \
                patch.object(guard, "_logger") as mock_logger, \
                patch.object(guard, "observability") as mock_observability:
            asyncio.run(guard.enforce_narrative_safety(message, "[SYSTEM] 洩漏"))
        mock_logger.error.assert_called_once()
        mock_observability.event.assert_called_once()
        args, kwargs = mock_observability.event.call_args
        self.assertEqual(args[0], "guard.repair_exhausted")
        self.assertEqual(kwargs.get("level"), logging.ERROR)


if __name__ == "__main__":
    unittest.main()
