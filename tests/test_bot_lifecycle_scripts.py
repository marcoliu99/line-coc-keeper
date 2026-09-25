from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import bot_lifecycle

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

    def test_profiler_is_opt_in_and_selects_a_known_tool(self):
        self.assertIsNone(bot_lifecycle._profiler_mode({}))
        self.assertIsNone(bot_lifecycle._profiler_mode({"BOT_PROFILER": "off"}))
        self.assertEqual(
            bot_lifecycle._profiler_mode({"BOT_PROFILER": "py-spy"}),
            "py-spy",
        )
        self.assertEqual(
            bot_lifecycle._profiler_mode({"BOT_PROFILER": "pyinstrument"}),
            "pyinstrument",
        )
        with self.assertRaises(SystemExit):
            bot_lifecycle._profiler_mode({"BOT_PROFILER": "unexpected"})

    def test_profiler_commands_write_distinct_artifacts(self):
        output = ROOT / ".runtime" / "bots" / "profile.pyinstrument.html"
        command = bot_lifecycle._command(
            "discord",
            {"BOT_PYTHON": "python3"},
            profiler="pyinstrument",
            profile_output=output,
        )
        self.assertEqual(command[:3], ["python3", "-m", "pyinstrument"])
        self.assertIn(str(output), command)
        svg = output.with_suffix(".svg")
        self.assertEqual(
            bot_lifecycle._py_spy_command("py-spy", 123, svg),
            ["py-spy", "record", "--pid", "123", "--output", str(svg)],
        )

    def test_start_help_documents_profiler_usage(self):
        result = subprocess.run(
            [str(SCRIPTS / "start_bot.sh"), "--help"],
            cwd=ROOT,
            env=self._env(ROOT / ".runtime" / "test-help"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BOT_PROFILER", result.stdout)
        self.assertIn("pyinstrument", result.stdout)
        self.assertIn("py-spy", result.stdout)
        self.assertIn(".runtime/bots/", result.stdout)

    def test_start_status_and_stop_only_selected_instance(self):
        try:
            probe = subprocess.run(
                ["ps", "-p", str(os.getpid()), "-o", "command="],
                check=False,
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
                    check=False,
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


class ArchiveStoppedInstanceArtifactsTests(unittest.TestCase):
    def test_archives_runtime_log_structured_log_and_pyinstrument_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_log = root / "profile-async.log"
            runtime_log.write_text("bot output\n", encoding="utf-8")
            structured_log = root / "structured.jsonl"
            structured_log.write_text("{}\n", encoding="utf-8")
            html_path = root / "instance.pyinstrument.html"
            html_path.write_text("<html></html>", encoding="utf-8")
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}
            manifest = {
                "log_path": str(runtime_log),
                "log_file_path": str(structured_log),
                "profiler": {"tool": "pyinstrument", "output_path": str(html_path)},
            }

            with patch.object(bot_lifecycle, "RUNTIME_DIR", root / "runtime"):
                bot_lifecycle._archive_stopped_instance_artifacts("inst", settings, manifest)

            self.assertFalse(runtime_log.exists())
            self.assertFalse(structured_log.exists())
            self.assertFalse(html_path.exists())
            archived = sorted(p.name for p in archive_dir.iterdir())
            self.assertEqual(len(archived), 3)
            self.assertTrue(any(name.endswith("_runtime_profile-async.log") for name in archived))
            self.assertTrue(any(name.endswith("_structured.jsonl") for name in archived))
            self.assertTrue(any(name.endswith("_instance.pyinstrument.html") for name in archived))
            # Timestamp and artifact labels distinguish the archived copies.
            for name in archived:
                self.assertNotEqual(name, "structured.jsonl")
                self.assertNotEqual(name, "instance.pyinstrument.html")

    def test_waits_for_nonempty_stable_html_before_archiving_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_log = root / "profile-async.log"
            runtime_log.write_text("bot output\n", encoding="utf-8")
            html_path = root / "instance.pyinstrument.html"
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}
            manifest = {
                "log_path": str(runtime_log),
                "log_file_path": "",
                "profiler": {"tool": "pyinstrument", "output_path": str(html_path)},
            }
            original_archive = bot_lifecycle._archive_and_remove
            archived_sources = []

            def write_html_later():
                time.sleep(0.04)
                html_path.write_text("<html>complete</html>", encoding="utf-8")

            def verify_ready_before_archive(src, dest_dir, timestamp, *, label=""):
                self.assertTrue(html_path.is_file())
                self.assertGreater(html_path.stat().st_size, 0)
                archived_sources.append(src)
                return original_archive(src, dest_dir, timestamp, label=label)

            writer = threading.Thread(target=write_html_later)
            writer.start()
            try:
                with patch.object(bot_lifecycle, "RUNTIME_DIR", root / "runtime"), \
                        patch.object(bot_lifecycle, "_PROFILE_WAIT_TIMEOUT_SECONDS", 1), \
                        patch.object(bot_lifecycle, "_PROFILE_POLL_INTERVAL_SECONDS", 0.01), \
                        patch.object(bot_lifecycle, "_archive_and_remove", side_effect=verify_ready_before_archive):
                    bot_lifecycle._archive_stopped_instance_artifacts("inst", settings, manifest)
            finally:
                writer.join(timeout=1)

            self.assertFalse(writer.is_alive())
            self.assertCountEqual(archived_sources, [runtime_log, html_path])
            self.assertFalse(runtime_log.exists())
            self.assertFalse(html_path.exists())

    def test_no_log_file_path_in_manifest_is_a_silent_noop_for_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}
            manifest: dict = {}
            with patch.object(bot_lifecycle, "RUNTIME_DIR", root / "runtime"):
                bot_lifecycle._archive_stopped_instance_artifacts("inst", settings, manifest)
            self.assertFalse(archive_dir.exists())

    def test_non_pyinstrument_profiler_does_not_touch_its_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svg_path = root / "instance.py-spy.svg"
            svg_path.write_text("<svg></svg>", encoding="utf-8")
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}
            manifest = {"profiler": {"tool": "py-spy", "output_path": str(svg_path)}}
            with patch.object(bot_lifecycle, "RUNTIME_DIR", root / "runtime"):
                bot_lifecycle._archive_stopped_instance_artifacts("inst", settings, manifest)
            self.assertTrue(svg_path.exists())

    def test_missing_pyinstrument_output_warns_but_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "runtime.log"
            log_path.write_text("bot output\n", encoding="utf-8")
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}
            manifest = {
                "log_path": str(log_path),
                "profiler": {"tool": "pyinstrument", "output_path": str(root / "never-written.html")},
            }
            with patch.object(bot_lifecycle, "RUNTIME_DIR", root / "runtime"), \
                    patch.object(bot_lifecycle, "_PROFILE_WAIT_TIMEOUT_SECONDS", 0.01), \
                    patch.object(bot_lifecycle, "_PROFILE_POLL_INTERVAL_SECONDS", 0.001):
                bot_lifecycle._archive_stopped_instance_artifacts("inst", settings, manifest)  # must not raise
            self.assertFalse(log_path.exists())
            self.assertTrue(any(path.name.endswith("_runtime_runtime.log") for path in archive_dir.iterdir()))

    def test_two_calls_produce_distinctly_named_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_dir = root / "archive"
            log_path = root / "profile-async.log"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}
            with patch.object(bot_lifecycle, "RUNTIME_DIR", root / "runtime"):
                for i in range(2):
                    log_path.write_text(f"run {i}\n", encoding="utf-8")
                    bot_lifecycle._archive_stopped_instance_artifacts("inst", settings, {"log_file_path": str(log_path)})
            archived = sorted(p.name for p in archive_dir.iterdir())
            self.assertEqual(len(archived), 2)
            self.assertNotEqual(archived[0], archived[1])

    def test_archival_io_failure_is_caught_and_warned_not_raised(self):
        """P2 review finding: if the archive dir is unwritable (or the disk
        is full), this must not propagate — stop() still needs to remove
        the instance manifest afterward, and a crash here would leave that
        cleanup undone even though the bot process already exited."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "profile-async.log"
            log_path.write_text("{}\n", encoding="utf-8")
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}
            manifest = {"log_file_path": str(log_path)}

            with patch.object(bot_lifecycle, "RUNTIME_DIR", root / "runtime"), \
                    patch.object(bot_lifecycle.shutil, "copy2", side_effect=OSError("disk full")):
                bot_lifecycle._archive_stopped_instance_artifacts("inst", settings, manifest)  # must not raise

            # Left in place since the copy failed before the unlink step.
            self.assertTrue(log_path.exists())

    def test_shared_log_still_used_by_another_live_instance_is_left_alone(self):
        """P1 review finding: two instances can be configured with the same
        LOG_FILE (it's a plain environment setting, not instance-scoped).
        Deleting it out from under a still-running instance would silently
        orphan that instance's own log output."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            log_path = root / "shared.log"
            log_path.write_text("{}\n", encoding="utf-8")
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}

            other_manifest = {"pid": os.getpid(), "log_file_path": str(log_path)}
            (runtime_dir / "other-instance.json").write_text(json.dumps(other_manifest), encoding="utf-8")

            manifest = {"log_file_path": str(log_path)}
            with patch.object(bot_lifecycle, "RUNTIME_DIR", runtime_dir):
                bot_lifecycle._archive_stopped_instance_artifacts("this-instance", settings, manifest)

            self.assertTrue(log_path.exists())
            self.assertFalse(archive_dir.exists())

    def test_shared_log_with_no_other_live_instance_is_archived_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            log_path = root / "shared.log"
            log_path.write_text("{}\n", encoding="utf-8")
            archive_dir = root / "archive"
            settings = {"BOT_LOG_ARCHIVE_DIR": str(archive_dir)}

            # A dead pid (0 is never a valid process id to signal) — this
            # other instance's manifest is stale, not actually still running.
            other_manifest = {"pid": 999999999, "log_file_path": str(log_path)}
            (runtime_dir / "other-instance.json").write_text(json.dumps(other_manifest), encoding="utf-8")

            manifest = {"log_file_path": str(log_path)}
            with patch.object(bot_lifecycle, "RUNTIME_DIR", runtime_dir):
                bot_lifecycle._archive_stopped_instance_artifacts("this-instance", settings, manifest)

            self.assertFalse(log_path.exists())
            self.assertEqual(len(list(archive_dir.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
