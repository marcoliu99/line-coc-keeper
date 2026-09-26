import asyncio
import unittest
from unittest.mock import patch

from app import scenario_rag, scenario_templates


class ScenarioTemplateReviewFixTests(unittest.TestCase):
    def test_record_results_keep_public_and_kp_scopes(self):
        public = scenario_rag._Chunk(1, "public", "public facts", "record-1", "public")
        kp = scenario_rag._Chunk(1, "private", "armor and attacks", "record-1", "kp_only")
        rows = scenario_rag._result_rows([(0.9, public), (0.8, kp)], 5)
        self.assertEqual([row["text"] for row in rows], ["public facts", "armor and attacks"])
        self.assertEqual(len(scenario_rag._result_rows([(0.9, public), (0.8, kp)], 1)), 1)

    def test_old_translation_task_does_not_remove_or_overwrite_replacement(self):
        async def run():
            jobs = {}
            replacement = asyncio.create_task(asyncio.sleep(0.1))

            async def generate(*_args):
                scenario_templates._tasks["scenario"] = replacement
                jobs["scenario"] = {"status": "queued", "source_hash": "new"}
                return "old-variant"

            try:
                with patch.object(scenario_templates.db, "set_json", side_effect=lambda _table, key, value: jobs.__setitem__(key, value)), \
                        patch.object(scenario_templates.asyncio, "to_thread", side_effect=generate):
                    old = asyncio.create_task(scenario_templates._run_job("scenario", "old", "old-chapters"))
                    scenario_templates._tasks["scenario"] = old
                    await old
                self.assertIs(scenario_templates._tasks["scenario"], replacement)
                self.assertEqual(jobs["scenario"], {"status": "queued", "source_hash": "new"})
            finally:
                scenario_templates._tasks.pop("scenario", None)
                replacement.cancel()
                await asyncio.gather(replacement, return_exceptions=True)

        asyncio.run(run())
