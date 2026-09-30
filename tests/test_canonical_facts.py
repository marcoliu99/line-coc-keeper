"""Verified scenario facts remain conditional and audience scoped."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, scenario_templates
from app.keeper_tools.registry import ToolCall
from app.keeper_tools.scenario import record_fact_or_clue
from app.models import GroupState
from app.repositories.group_state import load_state, save_state
from app.services import canonical_facts


class CanonicalFactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "facts.db")
        self.db_patch.start()
        db._ensure_tables()
        self.state = GroupState(group_id="facts", timeline_id="t1", scenario_text="櫥櫃裡有教會紀錄。")
        save_state(self.state)

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp.cleanup()

    def _record(self, text: str, *, visibility: str = "kp_only", **source: str) -> dict:
        call = ToolCall(
            state=self.state, input={"fact": text, "visibility": visibility, **source},
            private_messages=[], image_requests=[], speaker_role="player",
            name="record_established_fact",
        )
        return record_fact_or_clue(call)

    def test_source_ref_binds_record_digest_and_revalidates_after_source_change(self) -> None:
        source = scenario_templates.match_fact_source(
            self.state, "scenario-text", "櫥櫃裡有教會紀錄。"
        )
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source["record_id"], "scenario-text")
        self.assertEqual(len(source["content_digest"]), 64)
        result = self._record("櫥櫃裡有教會紀錄。", source_record_id="scenario-text",
                              source_quote="櫥櫃裡有教會紀錄。", source_condition="unconditional")
        self.assertEqual(result["record"]["verification_status"], "verified")
        state = load_state("facts")
        self.assertEqual(canonical_facts.project(state, speaker_role="player"), [])
        self.assertEqual(len(canonical_facts.project(state, speaker_role="kp_assistant")), 1)
        state.scenario_text = "櫥櫃是空的。"
        self.assertEqual(canonical_facts.project(state, speaker_role="kp_assistant"), [])

    def test_unverified_record_remains_visible_but_cannot_enter_authority(self) -> None:
        result = self._record("櫥櫃裡有三把鑰匙")
        self.assertEqual(result["record"]["verification_status"], "unverified")
        self.assertEqual(len(load_state("facts").established_facts), 1)
        self.assertEqual(canonical_facts.project(load_state("facts"), speaker_role="kp_assistant"), [])

    def test_exact_source_quote_without_reveal_does_not_publish_hidden_fact(self) -> None:
        text = "櫥櫃裡有教會紀錄。"
        result = self._record(text, visibility="public", source_record_id="scenario-text",
                              source_quote=text, source_condition="unconditional")
        self.assertEqual(result["record"]["verification_status"], "unverified")
        self.assertEqual(canonical_facts.project(load_state("facts"), recipient_id="u1"), [])

    def test_conditional_public_discovery_requires_resolved_success_receipt(self) -> None:
        text = "櫥櫃裡有教會紀錄。"
        source = {"source_record_id": "scenario-text", "source_quote": text,
                  "source_condition": "resolved_check", "trigger_event_id": "check-1"}
        self.assertEqual(self._record(text, visibility="public", **source)["record"]["verification_status"],
                         "unverified")
        state = load_state("facts")
        state.resolved_check_events.append({"event_id": "check-1", "timeline_id": "t1",
                                            "outcome": "regular 成功"})
        save_state(state)
        self.state = state
        promoted = self._record(text, visibility="public", **source)
        self.assertTrue(promoted["promoted"])
        facts = canonical_facts.project(load_state("facts"), speaker_role="player")
        self.assertEqual([fact.text for fact in facts], [text])
        self.assertEqual(facts[0].source_ref["trigger_event_id"], "check-1")

    def test_explicit_kp_canon_is_separate_from_ai_reply(self) -> None:
        state = load_state("facts")
        state.log = [
            {"role": "user", "content": "[KP Assistant] 日記只是普通筆記本",
             "record_kind": "kp_canon", "authority": "authoritative",
             "turn_id": "kp-1", "timeline_id": "t1", "audience": "public"},
            {"role": "assistant", "content": "日記藏著祕密鑰匙",
             "record_kind": "narrative", "authority": "presentation",
             "turn_id": "kp-1", "timeline_id": "t1", "audience": "public"},
        ]
        requirements = canonical_facts.requirements(state, recipient_id="u1")
        self.assertEqual([fact.text for fact in requirements.authoritative_facts],
                         ["日記只是普通筆記本"])
        self.assertNotIn("祕密鑰匙", canonical_facts.prompt_block(requirements))

    def test_private_fact_reaches_only_its_recipient(self) -> None:
        state = load_state("facts")
        source_ref = scenario_templates.match_fact_source(state, "scenario-text", state.scenario_text)
        assert source_ref is not None
        state.known_clues.append({
            "fact_id": "fact:private", "text": state.scenario_text,
            "verification_status": "verified", "source_kind": "scenario",
            "source_ref": source_ref, "timeline_id": "t1",
            "visibility": "private", "recipient_ids": ["u1"],
        })
        self.assertEqual(len(canonical_facts.project(state, recipient_id="u1")), 1)
        self.assertEqual(canonical_facts.project(state, recipient_id="u2"), [])

    def test_explicit_human_kp_correction_projects_but_ai_ruling_does_not(self) -> None:
        state = load_state("facts")
        state.narrative_corrections = [
            {"id": "human-1", "status": "approved", "adjudicated_by": "kp_assistant",
             "timeline_id": "t1", "resolution": "日記只是普通筆記本。"},
            {"id": "ai-1", "status": "approved", "adjudicated_by": "keeper",
             "timeline_id": "t1", "resolution": "旁邊還有一把神祕鑰匙。"},
        ]
        facts = canonical_facts.project(state)
        self.assertEqual([fact.text for fact in facts], ["日記只是普通筆記本。"])


if __name__ == "__main__":
    unittest.main()
