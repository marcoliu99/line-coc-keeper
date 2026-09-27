"""Guard the test sandbox: the suite must never touch a real checkout's data."""
import os
import unittest
from pathlib import Path

from app import config


class StorageSandboxTests(unittest.TestCase):
    def paths(self):
        return {
            "DATA_DIR": config.DATA_DIR,
            "DB_PATH": config.DB_PATH,
            "BACKUP_DIR": config.BACKUP_DIR,
            "SCENARIO_LIBRARY_DIR": Path(config.SCENARIO_LIBRARY_DIR).resolve(),
            "IMPORT_DIR": config.IMPORT_DIR,
        }

    def test_no_storage_path_sits_inside_the_checkout(self):
        checkout = Path(__file__).resolve().parent.parent
        for name, path in self.paths().items():
            with self.subTest(name):
                self.assertFalse(path.is_relative_to(checkout),
                                 f"{name}={path} would write into the checkout at {checkout}")

    def test_every_storage_path_shares_one_sandbox_root(self):
        # A path left on its default while the others move is the failure mode
        # this guards: their common ancestor would climb out of the sandbox.
        common = Path(os.path.commonpath([str(p) for p in self.paths().values()]))
        self.assertTrue(common.name.startswith("coc-tests-"),
                        f"storage paths do not share one sandbox root; common ancestor is {common}")

    def test_the_live_database_filename_is_not_opened_from_the_checkout(self):
        self.assertNotEqual(config.DB_PATH, Path("data/coc_bot.db").resolve())


if __name__ == "__main__":
    unittest.main()
