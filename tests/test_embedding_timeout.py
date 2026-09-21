import asyncio
import sys
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import memory_rag, scenario_rag


def _sleep_in_worker(seconds: float) -> None:
    time.sleep(seconds)


class EmbeddingClientTimeoutTests(unittest.TestCase):
    def test_scenario_embedding_client_has_real_timeout_and_no_sdk_retry(self):
        response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1, 0.2])])
        close = MagicMock()
        client = SimpleNamespace(embeddings=SimpleNamespace(create=MagicMock(return_value=response)), close=close)
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
        close.assert_called_once_with()

    def test_memory_embedding_client_has_real_timeout_and_no_sdk_retry(self):
        response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.3, 0.4])])
        close = MagicMock()
        client = SimpleNamespace(embeddings=SimpleNamespace(create=MagicMock(return_value=response)), close=close)
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
        close.assert_called_once_with()


class PrewarmLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_prewarm_executor_can_terminate_a_stuck_worker(self):
        executor = scenario_rag._create_prewarm_executor()
        try:
            loop = asyncio.get_running_loop()
            worker = loop.run_in_executor(executor, _sleep_in_worker, 10.0)
            _, pending = await asyncio.wait({worker}, timeout=0.05)
            self.assertIn(worker, pending)
            scenario_rag._stop_prewarm_executor(executor, terminate=True)
            await asyncio.wait({worker}, timeout=0.5)
            self.assertTrue(worker.done())
            self.assertTrue(worker.cancelled() or worker.exception() is not None)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    async def test_shutdown_waits_boundedly_for_previously_started_worker(self):
        started = threading.Event()
        release = threading.Event()

        def blocking_index(*_args):
            started.set()
            release.wait(timeout=2)

        with ThreadPoolExecutor(max_workers=1) as executor, \
                patch.object(scenario_rag, "_create_prewarm_executor", return_value=executor), \
                patch.object(scenario_rag, "SCENARIO_RAG_ENABLED", True), \
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
