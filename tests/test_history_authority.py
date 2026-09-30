"""Conversation provenance must not turn old prose into game-world authority."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import db, keeper, memory_rag
from app.models import GroupState
from app.providers.conversation_session import ConversationSession
from app.repositories.group_state import load_state, save_state
from app.services import history_authority


class HistoryAuthorityTests(unittest.TestCase):
    def test_provider_history_preserves_sdk_shape_and_marks_legacy_prose(self) -> None:
        history = [
            {"role": "assistant", "content": "櫥櫃下有教會紀錄"},
            history_authority.annotate_entry(
                {"role": "user", "content": "我猜有地下室"},
                turn_id="turn-1", timeline_id="timeline-1",
            ),
            history_authority.annotate_entry(
                {"role": "user", "content": "日記只有一本", "record_kind": "kp_canon",
                 "authority": "authoritative"},
                turn_id="turn-2", timeline_id="timeline-1",
            ),
        ]
        projected = ConversationSession("codex", Mock()).history(history)
        self.assertEqual([set(row) for row in projected], [{"role", "content"}] * 3)
        self.assertIn("unverified earlier conversation", projected[0]["content"])
        self.assertIn("claim", projected[1]["content"])
        self.assertIn("explicit human KP canon", projected[2]["content"])
        self.assertEqual(history[0]["content"], "櫥櫃下有教會紀錄")

    def test_summary_separates_unverified_narration_and_player_claims(self) -> None:
        history = [
            {"role": "assistant", "content": "錯誤的教會紀錄"},
            {"role": "user", "content": "我認為有鑰匙", "record_kind": "player_claim"},
            {"role": "user", "content": "這次是四本", "record_kind": "kp_canon"},
        ]
        provider = Mock()
        provider.analyze_text.return_value = {"summary": "已標明來源的摘要"}
        with patch.object(keeper, "conversation_provider", return_value=provider):
            self.assertEqual(keeper.summarize_log_chunk("舊摘要", history), "已標明來源的摘要")
        formatted_history, _, prompt = provider.analyze_text.call_args.args
        self.assertIn("presentation only", formatted_history)
        self.assertIn("錯誤的教會紀錄", formatted_history)
        self.assertIn("not established facts", formatted_history)
        self.assertIn("我認為有鑰匙", formatted_history)
        self.assertIn("Explicit human KP canon", formatted_history)
        self.assertIn("現有摘要（同樣未經驗證）", prompt)

    def test_old_memory_is_labeled_as_conversation_in_lexical_results(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.object(db, "DB_PATH", Path(root) / "state.db"):
            db._ensure_tables()
            memory_rag._index_cache.clear()
            self.assertTrue(memory_rag.append_memory(
                "history-authority", "教會紀錄在櫥櫃下", timeline_id="timeline-1",
                source_revision=4, embedding=None,
                source_messages=[{"record_kind": "narrative", "authority": "presentation",
                                  "turn_id": "turn-1", "timeline_id": "timeline-1"}],
            ))
            results = memory_rag.search_memory("history-authority", "教會紀錄", timeline_id="timeline-1")
            self.assertEqual(results[0]["authority"], "mixed")
            self.assertEqual(results[0]["source_messages"][0]["record_kind"], "narrative")
            formatted = memory_rag.format_results(results)
            self.assertIn("不自動證明世界事實", formatted)
            self.assertNotIn("這些是過去發生過的事", formatted)
            memory_rag._index_cache.clear()

    def test_turn_commit_stamps_claim_and_narration_without_promoting_either(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.object(db, "DB_PATH", Path(root) / "state.db"):
            db._ensure_tables()
            state = GroupState(group_id="authority-commit", timeline_id="timeline-1")
            save_state(state)
            self.assertTrue(keeper._commit_turn_result(
                state,
                [{"role": "user", "content": "我有一把鑰匙"},
                 {"role": "assistant", "content": "你看見一本日記"}],
                timeline_id="timeline-1",
            ))
            entries = load_state(state.group_id).log
            self.assertEqual([e["authority"] for e in entries], ["claim", "presentation"])
            self.assertEqual([e["record_kind"] for e in entries], ["player_claim", "narrative"])
            self.assertEqual({e["timeline_id"] for e in entries}, {"timeline-1"})
            self.assertEqual(len({e["turn_id"] for e in entries}), 1)


if __name__ == "__main__":
    unittest.main()


def test_unknown_persisted_provenance_is_not_promoted():
    from app.services.history_authority import provider_history
    result = provider_history([{'role': 'assistant', 'content': 'x', 'record_kind': 'kp_cannon'}])
    assert 'unverified earlier conversation' in result[0]['content']


def test_new_invalid_provenance_is_rejected():
    import pytest

    from app.services.history_authority import annotate_entry
    with pytest.raises(ValueError):
        annotate_entry({'role': 'assistant', 'authority': 'authoritativ'}, turn_id='t', timeline_id='l')
