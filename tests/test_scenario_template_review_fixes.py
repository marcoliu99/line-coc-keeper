import asyncio
import unittest
from unittest.mock import patch

from app import scenario_rag
from app.services import scenario_ingestion, scenario_lifecycle


class ScenarioTemplateReviewFixTests(unittest.TestCase):
    def test_pdf_activation_reports_stale_chinese_preference(self):
        async def resolved(*_args, **_kwargs):
            return scenario_lifecycle.LifecycleResult(
                "activated", title="Scenario", text="scene",
                variant_notice="已改用原文檢索",
            )
        with patch.object(scenario_lifecycle, "resolve_pending_submission", side_effect=resolved):
            response = asyncio.run(scenario_ingestion.apply_pdf_upload_choice("group", "new"))
        self.assertIn("已改用原文檢索", response)

    def test_record_results_keep_public_and_kp_scopes(self):
        public = scenario_rag._Chunk(1, "public", "public facts", "record-1", "public")
        kp = scenario_rag._Chunk(1, "private", "armor and attacks", "record-1", "kp_only")
        rows = scenario_rag._result_rows([(0.9, public), (0.8, kp)], 5)
        self.assertEqual([row["text"] for row in rows], ["public facts\n\narmor and attacks"])
        self.assertEqual(len(scenario_rag._result_rows([(0.9, public), (0.8, kp)], 1)), 1)
