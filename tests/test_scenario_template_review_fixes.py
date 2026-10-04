import unittest
from unittest.mock import patch

from app import scenario_rag, scenario_templates
from app.models import GroupState
from app.services import scenario_ingestion


class ScenarioTemplateReviewFixTests(unittest.TestCase):
    def test_pdf_activation_reports_stale_chinese_preference(self):
        state = GroupState(group_id="group")
        state.pending_pdf_upload = {
            "scenario_id": "scenario", "low_text_pages": [], "truncated": False,
        }
        context = {
            "manifest": {"title": "Scenario"}, "text": "scene",
            "indexes": {"npcs": [], "locations": []}, "scene_maps": {}, "pregens": [],
        }
        with patch.object(scenario_ingestion, "load_state", return_value=state), \
                patch("app.repositories.state_transaction.commit_snapshot"), \
                patch.object(scenario_ingestion.scenario_library, "load_context", return_value=context), \
                patch.object(scenario_ingestion, "_apply_new_scenario"), \
                patch.object(scenario_ingestion, "_install_library_context"), \
                patch.object(scenario_ingestion.scenario_activation, "refresh_after_commit", return_value=True), \
                patch.object(scenario_ingestion, "_pdf_upload_confirmation_text", return_value="loaded"), \
                patch.object(scenario_templates, "preference_notice", return_value="已改用原文檢索"):
            response = scenario_ingestion.apply_pdf_upload_choice("group", "new")
        self.assertIn("已改用原文檢索", response)

    def test_record_results_keep_public_and_kp_scopes(self):
        public = scenario_rag._Chunk(1, "public", "public facts", "record-1", "public")
        kp = scenario_rag._Chunk(1, "private", "armor and attacks", "record-1", "kp_only")
        rows = scenario_rag._result_rows([(0.9, public), (0.8, kp)], 5)
        self.assertEqual([row["text"] for row in rows], ["public facts\n\narmor and attacks"])
        self.assertEqual(len(scenario_rag._result_rows([(0.9, public), (0.8, kp)], 1)), 1)
