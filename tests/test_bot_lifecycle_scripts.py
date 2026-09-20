from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"


class BotLifecycleScriptTests(unittest.TestCase):
    def _env(self, temporary: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["BOT_LIFECYCLE_PYTHON"] = os.environ.get("PYTHON", "python3")
        env["DATA_DIR"] = str(temporary / "groups")
        env["DB_PATH"] = str(temporary / "state.db")
        env["BACKUP_DIR"] = str(temporary / "backups")
        env["SCENARIO_LIBRARY_DIR"] = str(temporary / "scenarios")
        env["IMPORT_DIR"] = str(temporary / "imports")
        return env

    def test_start_status_and_stop_only_selected_instance(self):
        try:
            probe = subprocess.run(
                ["ps", "-p", str(os.getpid()), "-o", "command="],
                capture_output=True,
                text=True,
            )
        except OSError:
            self.skipTest("ps process inspection is unavailable in this environment")
        if probe.returncode != 0 or not probe.stdout.strip():
            self.skipTest("ps process inspection is unavailable in this environment")
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake = temporary / "fake-bot"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import time\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = self._env(temporary)
            env["BOT_PYTHON"] = str(fake)

            first = subprocess.run(
                [str(SCRIPTS / "start_bot.sh"), "discord", "--name", "test-a"],
                cwd=ROOT, env=env, check=True, capture_output=True, text=True,
            )
            second = subprocess.run(
                [str(SCRIPTS / "start_bot.sh"), "discord", "--name", "test-b"],
                cwd=ROOT, env=env, check=True, capture_output=True, text=True,
            )
            self.assertIn("test-a", first.stdout)
            self.assertIn("test-b", second.stdout)
            manifest_a = ROOT / ".runtime" / "bots" / "test-a.json"
            manifest_b = ROOT / ".runtime" / "bots" / "test-b.json"
            pid_b = json.loads(manifest_b.read_text(encoding="utf-8"))["pid"]
            subprocess.run([str(SCRIPTS / "stop_bot.sh"), "test-a"], cwd=ROOT, env=env, check=True)
            self.assertFalse(manifest_a.exists())
            self.assertTrue(manifest_b.exists())
            os.kill(pid_b, 0)
            subprocess.run([str(SCRIPTS / "stop_bot.sh"), "test-b"], cwd=ROOT, env=env, check=True)
            self.assertFalse(manifest_b.exists())

    def test_clean_requires_yes_and_preserves_env(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            env = self._env(temporary)
            targets = [
                Path(env["DATA_DIR"]), Path(env["DB_PATH"]), Path(env["BACKUP_DIR"]),
                Path(env["SCENARIO_LIBRARY_DIR"]), Path(env["IMPORT_DIR"]),
            ]
            for target in targets:
                if target == Path(env["DB_PATH"]):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("runtime", encoding="utf-8")
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "marker").write_text("runtime", encoding="utf-8")
            env_file = ROOT / ".env"
            existed = env_file.exists()
            if not existed:
                env_file.write_text("BOT_TEST_SECRET=keep\n", encoding="utf-8")
            try:
                rejected = subprocess.run(
                    [str(SCRIPTS / "clean_bot_data.sh")], cwd=ROOT, env=env,
                    capture_output=True, text=True,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertTrue(targets[0].exists())
                subprocess.run([str(SCRIPTS / "clean_bot_data.sh"), "--yes"], cwd=ROOT, env=env, check=True)
                self.assertTrue(env_file.exists())
                self.assertFalse(any(target.exists() for target in targets))
            finally:
                if not existed:
                    env_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
