import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import scenario_library


def _hold_then_release(library_dir: str, started, release) -> None:
    from app import scenario_library as library
    library.SCENARIO_LIBRARY_DIR = Path(library_dir)
    with library.publication_lock():
        started.set()
        release.wait(10)


def _try_acquire(library_dir: str, acquired) -> None:
    from app import scenario_library as library
    library.SCENARIO_LIBRARY_DIR = Path(library_dir)
    with library.publication_lock():
        acquired.set()


class PublicationLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "library"
        patcher = patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nested_acquisition_does_not_deadlock(self) -> None:
        """scenario_source_review.publish() holds the lock and publish_derived() takes it again."""
        result: list[str] = []

        def nested() -> None:
            with (
                scenario_library.publication_lock(),
                scenario_library.publication_lock(),
                scenario_library.publication_target("nested-case") as target,
            ):
                result.append(target.name)

        worker = threading.Thread(target=nested, daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "nested publication_lock() blocked")
        self.assertEqual(result, ["nested-case"])

    def test_lock_is_released_after_the_outermost_exit(self) -> None:
        with scenario_library.publication_lock(), scenario_library.publication_lock():
            pass
        self.assertEqual(getattr(scenario_library._LOCK_STATE, "depth", 0), 0)
        acquired = threading.Event()
        threading.Thread(target=lambda: (scenario_library.publication_lock().__enter__(), acquired.set()), daemon=True).start()
        self.assertTrue(acquired.wait(5))

    def test_release_happens_when_the_body_raises(self) -> None:
        with self.assertRaises(RuntimeError), scenario_library.publication_lock():
            raise RuntimeError("boom")
        self.assertEqual(getattr(scenario_library._LOCK_STATE, "depth", 0), 0)

    def test_second_process_waits_for_the_first(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        started, release, acquired = ctx.Event(), ctx.Event(), ctx.Event()
        self.root.mkdir(parents=True)
        holder = ctx.Process(target=_hold_then_release, args=(str(self.root), started, release))
        waiter = ctx.Process(target=_try_acquire, args=(str(self.root), acquired))
        holder.start()
        try:
            self.assertTrue(started.wait(20), "holder never took the lock")
            waiter.start()
            time.sleep(1.0)
            self.assertFalse(acquired.is_set(), "a second process got the lock while it was held")
            release.set()
            self.assertTrue(acquired.wait(20), "the second process never got the lock after release")
        finally:
            release.set()
            for process in (holder, waiter):
                process.join(10)
                if process.is_alive():
                    process.terminate()

    def test_save_and_clean_take_the_publication_lock(self) -> None:
        """Both mutation paths used to take the private RLock directly, bypassing the cross-process flock."""
        depths: list[int] = []
        original = scenario_library._LOCK_STATE

        class Spy(threading.local):
            depth = 0

        spy = Spy()
        with patch.object(scenario_library, "_LOCK_STATE", spy):
            real = scenario_library.publication_lock

            def watch():
                manager = real()

                class Wrapper:
                    def __enter__(self_inner):
                        manager.__enter__()
                        depths.append(spy.depth)

                    def __exit__(self_inner, *exc):
                        return manager.__exit__(*exc)

                return Wrapper()

            with patch.object(scenario_library, "publication_lock", watch):
                sid = scenario_library.save_markdown_scenario(
                    b"--- \xe7\xac\xac 1 \xe9\xa0\x81 ---\nbody\n", title="Lock Case", filename="lock.md",
                    preview="body", text="--- 第 1 頁 ---\nbody\n", indexes={}, pregens=[],
                )
                scenario_library.clean_scenario(sid)
        self.assertGreaterEqual(len(depths), 2, "save and clean must both go through publication_lock()")
        self.assertTrue(all(d >= 1 for d in depths))
        self.assertIs(original, scenario_library._LOCK_STATE)


if __name__ == "__main__":
    unittest.main()
