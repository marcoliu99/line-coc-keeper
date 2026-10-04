import asyncio
import multiprocessing
import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import memory_rag, scenario_rag


def _sleep_in_worker(seconds: float) -> None:
    time.sleep(seconds)


def _wait_in_worker(started) -> None:
    started.set()
    time.sleep(10)


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
    async def test_prewarm_worker_can_terminate_a_stuck_child(self):
        worker = scenario_rag._PrewarmWorker("g", "scenario")
        worker.process = multiprocessing.get_context("spawn").Process(
            target=_sleep_in_worker, args=(10.0,)
        )
        try:
            worker.start()
            wait_task = asyncio.create_task(worker.wait())
            _, pending = await asyncio.wait({wait_task}, timeout=0.05)
            self.assertIn(wait_task, pending)
            worker.stop()
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(wait_task, timeout=0.5)
            self.assertIsNotNone(worker.exitcode)
        finally:
            worker.stop()

    async def test_completed_prewarm_worker_is_not_terminated(self):
        worker = scenario_rag._PrewarmWorker("g", "scenario")
        worker.process = multiprocessing.get_context("spawn").Process(
            target=_sleep_in_worker, args=(0.01,)
        )
        worker.start()
        await asyncio.wait_for(worker.wait(), timeout=2)
        with patch.object(worker.process, "terminate") as terminate, \
                patch.object(worker.process, "kill") as kill:
            worker.stop()
        terminate.assert_not_called()
        kill.assert_not_called()
        self.assertEqual(worker.exitcode, 0)

    async def test_completed_prewarm_releases_process_handle_before_shutdown(self):
        worker = scenario_rag._PrewarmWorker("g", "scenario")
        worker.process = multiprocessing.get_context("spawn").Process(
            target=_sleep_in_worker, args=(0.01,)
        )
        with patch.object(scenario_rag, "_PrewarmWorker", return_value=worker), \
                patch.object(scenario_rag, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_ENABLED", True):
            wrapper = scenario_rag.schedule_index_prewarm("g", "scenario")
            self.assertIsNotNone(wrapper)
            await asyncio.wait_for(wrapper, timeout=2)
            await asyncio.sleep(0)
            state = scenario_rag._prewarm_state(asyncio.get_running_loop())
            self.assertNotIn(worker, state.workers)
            self.assertEqual(worker.exitcode, 0)
            with self.assertRaises(ValueError):
                _ = worker.process.exitcode  # Process.close() released the handle.
            await scenario_rag.shutdown_prewarm()

    def test_prewarm_worker_kills_child_if_terminate_does_not_finish(self):
        worker = scenario_rag._PrewarmWorker("g", "scenario")

        class StubbornProcess:
            pid = 123
            exitcode = None

            def __init__(self):
                self.terminate_calls = 0
                self.kill_calls = 0

            def terminate(self):
                self.terminate_calls += 1

            def join(self, timeout=None):
                return None

            def kill(self):
                self.kill_calls += 1
                self.exitcode = -9

            def close(self):
                return None

        process = StubbornProcess()
        worker.process = process
        worker.stop()
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.exitcode, -9)

    async def test_shutdown_stays_bounded_if_kill_cannot_finish(self):
        worker = scenario_rag._PrewarmWorker("g", "scenario")

        class UnkillableProcess:
            pid = 123
            exitcode = None

            def terminate(self):
                return None

            def join(self, timeout=None):
                return None

            def kill(self):
                return None

        worker.process = UnkillableProcess()
        state = scenario_rag._prewarm_state(asyncio.get_running_loop())
        state.workers.add(worker)
        state.worker_tasks.add(asyncio.create_task(worker.wait()))
        with patch.object(scenario_rag, "PROVIDER_SHUTDOWN_GRACE_SECONDS", 0.001):
            await asyncio.wait_for(scenario_rag.shutdown_prewarm(), timeout=1)
        self.assertFalse(scenario_rag._prewarm_states.get(asyncio.get_running_loop()))

    async def test_shutdown_waits_boundedly_for_previously_started_worker(self):
        context = multiprocessing.get_context("spawn")
        started = context.Event()
        worker = scenario_rag._PrewarmWorker("g", "scenario")
        worker.process = context.Process(target=_wait_in_worker, args=(started,))
        with patch.object(scenario_rag, "_PrewarmWorker", return_value=worker), \
                patch.object(scenario_rag, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_ENABLED", True), \
                patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_MAX_CONCURRENT", 1), \
                patch.object(scenario_rag, "PROVIDER_SHUTDOWN_GRACE_SECONDS", 0.001):
            scenario_rag.schedule_index_prewarm("g", "scenario")
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            started_at = time.perf_counter()
            await scenario_rag.shutdown_prewarm()
            elapsed = time.perf_counter() - started_at
            self.assertLess(elapsed, 0.5)
            self.assertIsNotNone(worker.exitcode)

    async def test_cancelled_wrapper_leaves_child_for_shutdown_cleanup(self):
        context = multiprocessing.get_context("spawn")
        started = context.Event()
        worker = scenario_rag._PrewarmWorker("g", "scenario")
        worker.process = context.Process(target=_wait_in_worker, args=(started,))
        with patch.object(scenario_rag, "_PrewarmWorker", return_value=worker), \
                patch.object(scenario_rag, "SCENARIO_RAG_ENABLED", True), \
                patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_ENABLED", True), \
                patch.object(scenario_rag, "PROVIDER_SHUTDOWN_GRACE_SECONDS", 0.001):
            wrapper = scenario_rag.schedule_index_prewarm("g", "scenario")
            self.assertIsNotNone(wrapper)
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            wrapper.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await wrapper
            self.assertIsNone(worker.exitcode)
            await scenario_rag.shutdown_prewarm()
            self.assertIsNotNone(worker.exitcode)

    async def test_slow_process_start_does_not_block_event_loop_or_orphan_child(self):
        worker = scenario_rag._PrewarmWorker("g", "scenario")
        worker.process = multiprocessing.get_context("spawn").Process(
            target=_sleep_in_worker, args=(10.0,)
        )
        entered = threading.Event()
        release = threading.Event()

        def slow_start():
            with worker._start_stop_lock:
                entered.set()
                release.wait(timeout=2)
                worker.process.start()
                return True

        try:
            with patch.object(scenario_rag, "_PrewarmWorker", return_value=worker), \
                    patch.object(worker, "start", side_effect=slow_start), \
                    patch.object(scenario_rag, "SCENARIO_RAG_ENABLED", True), \
                    patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_ENABLED", True), \
                    patch.object(scenario_rag, "PROVIDER_SHUTDOWN_GRACE_SECONDS", 0.001):
                scenario_rag.schedule_index_prewarm("g", "scenario")
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.1)
                shutdown = asyncio.create_task(scenario_rag.shutdown_prewarm())
                await asyncio.sleep(0.01)
                self.assertFalse(shutdown.done())
                release.set()
                await asyncio.wait_for(shutdown, timeout=2)
                self.assertIsNotNone(worker.exitcode)
        finally:
            release.set()
            worker.stop()

    async def test_shutdown_before_start_lock_prevents_late_child(self):
        worker = scenario_rag._PrewarmWorker("g", "scenario")
        worker.process = multiprocessing.get_context("spawn").Process(
            target=_sleep_in_worker, args=(10.0,)
        )
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        original_start = worker.start

        def delayed_before_lock():
            entered.set()
            try:
                release.wait(timeout=2)
                return original_start()
            finally:
                finished.set()

        try:
            with patch.object(scenario_rag, "_PrewarmWorker", return_value=worker), \
                    patch.object(worker, "start", side_effect=delayed_before_lock), \
                    patch.object(scenario_rag, "SCENARIO_RAG_ENABLED", True), \
                    patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_ENABLED", True):
                scenario_rag.schedule_index_prewarm("g", "scenario")
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                await asyncio.wait_for(scenario_rag.shutdown_prewarm(), timeout=1)
                release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 1))
                self.assertIsNone(worker.process.pid)
                self.assertIsNone(worker.exitcode)
        finally:
            release.set()
            worker.stop()

    async def test_disabled_prewarm_starts_no_child(self):
        with patch.object(scenario_rag, "SCENARIO_RAG_PREWARM_ENABLED", False), \
                patch.object(scenario_rag, "_PrewarmWorker") as create_worker:
            self.assertIsNone(scenario_rag.schedule_index_prewarm("g", "scenario"))
            create_worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
