"""Guard the test sandbox: the suite must never touch a real checkout's data."""
import os
import unittest
from pathlib import Path

from app import config


class DeploymentEnvIsolationTests(unittest.TestCase):
    def test_a_checkout_env_file_cannot_reach_config(self):
        import dotenv

        # Restoring the real loader here would let the deployment .env decide
        # what "default" means for every test that asserts one.
        self.assertEqual(dotenv.load_dotenv.__name__, "_load_dotenv_disabled")

    def test_config_reports_code_defaults_not_deployment_values(self):
        # These two are overridden in the deployment .env (12 and 7), so they
        # fail in the checkout the bot runs from unless dotenv is neutralized.
        self.assertEqual(config.MAX_TOOL_ITERATIONS, 5)
        self.assertEqual(config.HIGH_ITERATION_WATERMARK, 4)


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
