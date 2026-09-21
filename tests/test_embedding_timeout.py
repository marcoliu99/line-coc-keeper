import asyncio
import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import memory_rag, scenario_rag


class EmbeddingClientTimeoutTests(unittest.TestCase):
    def test_scenario_embedding_client_has_real_timeout_and_no_sdk_retry(self):
        response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1, 0.2])])
        client = SimpleNamespace(embeddings=SimpleNamespace(create=MagicMock(return_value=response)))
        constructor = MagicMock(return_value=client)
        fake_openai = types.SimpleNamespace(OpenAI=constructor)

        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(scenario_rag, "OPENAI_API_KEY", "key"):
            result = scenario_rag._embed_texts(["scenario"], rag_kind="scenario")

        self.assertEqual(result, [[0.1, 0.2]])
        constructor.assert_called_once_with(
            api_key="key",
            timeout=scenario_rag.EMBEDDING_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    def test_memory_embedding_client_has_real_timeout_and_no_sdk_retry(self):
        response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.3, 0.4])])
        client = SimpleNamespace(embeddings=SimpleNamespace(create=MagicMock(return_value=response)))
        constructor = MagicMock(return_value=client)
        fake_openai = types.SimpleNamespace(OpenAI=constructor)

        with patch.dict(sys.modules, {"openai": fake_openai}), \
                patch.object(memory_rag, "OPENAI_API_KEY", "key"):
            result = memory_rag._embed_texts(["memory"], rag_kind="memory")

        self.assertEqual(result, [[0.3, 0.4]])
        constructor.assert_called_once_with(
            api_key="key",
            timeout=memory_rag.EMBEDDING_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )


class PrewarmLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_waits_boundedly_for_previously_started_worker(self):
        started = threading.Event()
        release = threading.Event()

        def blocking_index(*_args):
            started.set()
            release.wait(timeout=2)

        with patch.object(scenario_rag, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_ENABLED", True), \
                patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_MAX_CONCURRENT", 1), \
                patch.object(scenario_rag, "PROVIDER_SHUTDOWN_GRACE_SECONDS", 0.001), \
                patch.object(scenario_rag, "get_index", side_effect=blocking_index):
            scenario_rag.schedule_index_prewarm("g", "scenario")
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            started_at = time.perf_counter()
            await scenario_rag.shutdown_prewarm()
            elapsed = time.perf_counter() - started_at
            self.assertLess(elapsed, 0.5)
            release.set()
            await asyncio.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
