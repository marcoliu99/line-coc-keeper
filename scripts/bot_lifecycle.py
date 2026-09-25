#!/usr/bin/env python3
"""Process-safe lifecycle commands for local bot instances."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - normal installs include python-dotenv
    dotenv_values = None


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime" / "bots"
INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PROFILER_OFF_VALUES = frozenset({"", "0", "false", "none", "off", "disabled"})
_PROFILER_TOOLS = frozenset({"py-spy", "pyinstrument"})


def _settings() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if dotenv_values is not None and env_file.is_file():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update({k: v for k, v in os.environ.items() if v is not None})
    return values


def _path_setting(settings: dict[str, str], name: str, default: str) -> Path:
    path = Path(settings.get(name, default)).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def _resolve_log_file_path(settings: dict[str, str]) -> Path | None:
    """Resolve LOG_FILE (app/config.py's structured-log setting) the same
    way the bot process itself would: relative to ROOT, since start() always
    launches it with cwd=ROOT (see the subprocess.Popen call below) — not
    relative to whatever directory happens to invoke this script. Returns
    None when LOG_FILE isn't set (structured file logging is opt-in)."""
    raw = settings.get("LOG_FILE", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def _manifest_path(instance: str) -> Path:
    if not INSTANCE_RE.fullmatch(instance):
        raise SystemExit(f"invalid instance name: {instance!r}")
    return RUNTIME_DIR / f"{instance}.json"


def _load_manifest(instance: str) -> tuple[Path, dict]:
    path = _manifest_path(instance)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"instance not found: {instance}")
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read manifest {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"invalid manifest: {path}")
    return path, data


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return result.stdout.strip()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _verified_process(manifest: dict) -> tuple[int, int, str]:
    try:
        pid = int(manifest["pid"])
        pgid = int(manifest["pgid"])
        command = [str(item) for item in manifest["command"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"invalid process identity in manifest: {exc}")
    actual = _process_command(pid)
    expected_tokens = [token for token in command if token.startswith("app.")]
    if not _alive(pid):
        raise SystemExit(f"instance is not running (stale manifest, pid={pid})")
    if not actual or any(token not in actual for token in expected_tokens):
        raise SystemExit(f"refusing to signal pid {pid}: command identity mismatch ({actual or 'unknown'})")
    try:
        actual_pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError) as exc:
        raise SystemExit(f"cannot verify process group for pid {pid}: {exc}")
    if actual_pgid != pgid:
        raise SystemExit(f"refusing to signal pid {pid}: process group identity mismatch")
    return pid, pgid, actual


def _python_executable() -> str:
    candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.is_file() else sys.executable


def _profiler_mode(settings: dict[str, str]) -> str | None:
    value = settings.get("BOT_PROFILER", "off").strip().lower()
    if value in _PROFILER_OFF_VALUES:
        return None
    if value not in _PROFILER_TOOLS:
        choices = ", ".join(sorted(_PROFILER_TOOLS | {"off"}))
        raise SystemExit(f"invalid BOT_PROFILER={value!r}; choose one of: {choices}")
    return value


def _profiler_output(instance: str, profiler: str | None) -> Path | None:
    if profiler is None:
        return None
    suffix = "svg" if profiler == "py-spy" else "html"
    return RUNTIME_DIR / f"{instance}.{profiler}.{suffix}"


def _py_spy_executable(python: str) -> str | None:
    virtualenv_candidate = Path(python).resolve().parent / "py-spy"
    if virtualenv_candidate.is_file() and os.access(virtualenv_candidate, os.X_OK):
        return str(virtualenv_candidate)
    return shutil.which("py-spy")


def _validate_profiler(profiler: str | None, python: str) -> str | None:
    if profiler is None:
        return None
    if profiler == "py-spy":
        executable = _py_spy_executable(python)
        if executable is None:
            raise SystemExit("BOT_PROFILER=py-spy requires the py-spy executable in PATH or the selected virtualenv")
        return executable
    try:
        probe = subprocess.run(
            [python, "-c", "import pyinstrument"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise SystemExit(f"cannot run BOT_PYTHON={python!r} to validate pyinstrument: {exc}") from exc
    if probe.returncode != 0:
        raise SystemExit(
            f"BOT_PROFILER=pyinstrument requires pyinstrument in {python}; install requirements-dev.txt"
        )
    return None


def _command(
    bot: str,
    settings: dict[str, str],
    *,
    profiler: str | None = None,
    profile_output: Path | None = None,
) -> list[str]:
    python = settings.get("BOT_PYTHON", _python_executable())
    if bot == "discord":
        if profiler == "pyinstrument":
            if profile_output is None:
                raise SystemExit("pyinstrument profile output path was not initialized")
            return [
                python,
                "-m",
                "pyinstrument",
                "-o",
                str(profile_output),
                "-r",
                "html",
                "-m",
                "app.discord_bot",
            ]
        return [python, "-m", "app.discord_bot"]
    raise SystemExit("usage: start_bot.sh discord [--name INSTANCE]")


def _py_spy_command(executable: str, pid: int, output: Path) -> list[str]:
    return [executable, "record", "--pid", str(pid), "--output", str(output)]


def _terminate_process_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _start_py_spy(
    executable: str,
    bot_pid: int,
    output: Path,
    log_path: Path,
) -> tuple[subprocess.Popen, list[str]]:
    command = _py_spy_command(executable, bot_pid, output)
    try:
        with log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        raise SystemExit(f"failed to start py-spy: {exc}") from exc
    time.sleep(0.1)
    if process.poll() is not None:
        raise SystemExit(f"py-spy exited immediately with code {process.returncode}; see {log_path}")
    return process, command


def _profiler_manifest(
    profiler: str | None,
    output: Path | None,
    bot_process: subprocess.Popen,
    bot_command: list[str],
    bot_pgid: int,
    attached_process: subprocess.Popen | None = None,
    attached_command: list[str] | None = None,
) -> dict | None:
    if profiler is None or output is None:
        return None
    if attached_process is None or attached_command is None:
        pid = bot_process.pid
        pgid = bot_pgid
        command = bot_command
    else:
        pid = attached_process.pid
        pgid = os.getpgid(pid)
        command = attached_command
    return {
        "tool": profiler,
        "pid": pid,
        "pgid": pgid,
        "command": command,
        "output_path": str(output),
    }


def start(bot: str, name: str | None) -> int:
    settings = _settings()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    instance = name or f"{bot}-{timestamp}-{secrets.token_hex(3)}"
    manifest_path = _manifest_path(instance)
    if manifest_path.exists():
        raise SystemExit(f"instance already exists: {instance}")
    python = settings.get("BOT_PYTHON", _python_executable())
    profiler = _profiler_mode(settings)
    profiler_executable = _validate_profiler(profiler, python)
    profile_output = _profiler_output(instance, profiler)
    command = _command(
        bot,
        settings,
        profiler=profiler,
        profile_output=profile_output,
    )
    log_path = RUNTIME_DIR / f"{instance}.log"
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")
    process: subprocess.Popen | None = None
    profiler_process: subprocess.Popen | None = None
    profiler_command: list[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_handle.close()
        log_path.unlink(missing_ok=True)
        raise SystemExit(f"failed to start {bot}: {exc}")
    finally:
        log_handle.close()
    assert process is not None
    bot_pgid = os.getpgid(process.pid)
    time.sleep(0.15)
    if process.poll() is not None:
        log_path.unlink(missing_ok=True)
        raise SystemExit(f"{bot} exited immediately; see {log_path}")
    if profiler == "py-spy":
        assert profiler_executable is not None
        assert profile_output is not None
        try:
            profiler_process, profiler_command = _start_py_spy(
                profiler_executable, process.pid, profile_output, log_path
            )
        except SystemExit:
            _terminate_process_group(bot_pgid, signal.SIGTERM)
            raise
    log_file_path = _resolve_log_file_path(settings)
    manifest = {
        "instance": instance,
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(ROOT),
        "bot": bot,
        "command": command,
        "log_path": str(log_path),
        "log_file_path": str(log_file_path) if log_file_path is not None else "",
    }
    profiler_data = _profiler_manifest(
        profiler,
        profile_output,
        process,
        command,
        bot_pgid,
        profiler_process,
        profiler_command,
    )
    if profiler_data is not None:
        manifest["profiler"] = profiler_data
    try:
        _write_json_atomic(manifest_path, manifest)
    except OSError as exc:
        if profiler_process is not None:
            try:
                profiler_pgid = os.getpgid(profiler_process.pid)
            except ProcessLookupError:
                profiler_pgid = None
            if profiler_pgid is not None:
                _terminate_process_group(profiler_pgid, signal.SIGTERM)
        _terminate_process_group(bot_pgid, signal.SIGTERM)
        log_path.unlink(missing_ok=True)
        raise SystemExit(f"failed to record {instance}: {exc}")
    profile_note = f" profile={profile_output}" if profile_output is not None else ""
    print(f"started {instance} pid={process.pid} log={log_path}{profile_note}")
    return 0


def _verified_attached_profiler(profiler: dict) -> tuple[int, int, str]:
    try:
        pid = int(profiler["pid"])
        pgid = int(profiler["pgid"])
        command = [str(item) for item in profiler["command"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"invalid profiler identity in manifest: {exc}") from exc
    if not _alive(pid):
        return pid, pgid, ""
    actual = _process_command(pid)
    expected_executable = Path(command[0]).name if command else "py-spy"
    if not actual or "py-spy" not in actual or expected_executable not in actual:
        raise SystemExit(f"refusing to signal profiler pid {pid}: command identity mismatch ({actual or 'unknown'})")
    try:
        actual_pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError) as exc:
        raise SystemExit(f"cannot verify profiler process group for pid {pid}: {exc}") from exc
    if actual_pgid != pgid:
        raise SystemExit("refusing to signal profiler pid: process group identity mismatch")
    return pid, pgid, actual


def _archive_and_remove(src: Path, archive_dir: Path, timestamp: str) -> Path:
    dest = archive_dir / f"{timestamp}_{src.name}"
    shutil.copy2(src, dest)
    src.unlink()
    return dest


def _other_live_instance_shares_log(log_path: Path, instance: str) -> bool:
    """True if some OTHER instance's manifest still points at log_path and
    that instance's process is still alive. Nothing stops two concurrently-
    running instances from being configured with the same LOG_FILE (it's a
    plain environment setting, not instance-scoped) — deleting it out from
    under a still-writing process would silently orphan that process's own
    log output for the rest of its life, and either corrupt or vanish
    entirely once it also exits without a file left to write into."""
    for other_path in RUNTIME_DIR.glob("*.json"):
        if other_path.stem == instance:
            continue
        try:
            other_manifest = json.loads(other_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(other_manifest, dict):
            continue
        if other_manifest.get("log_file_path") != str(log_path):
            continue
        other_pid = other_manifest.get("pid")
        if isinstance(other_pid, int) and _alive(other_pid):
            return True
    return False


def _archive_stopped_instance_artifacts(instance: str, settings: dict[str, str], manifest: dict) -> None:
    """Copy the structured JSON log and (if profiling with pyinstrument)
    the profile HTML into BOT_LOG_ARCHIVE_DIR under a timestamp-prefixed
    name, then remove the originals — called only after stop() has
    confirmed the instance's process (and any attached profiler) has
    fully exited, so both files are done being written. Each artifact is
    independent and optional: a missing log_file_path or a non-pyinstrument
    profiler is a silent no-op for that artifact. Archiving is best-effort
    and must never raise — the bot process has already exited by the time
    this runs, and stop() still needs to remove the instance manifest
    afterward regardless of whether an unwritable/full archive disk made
    this fail."""
    archive_dir = Path(settings.get("BOT_LOG_ARCHIVE_DIR", "~/coc_v2_log")).expanduser()
    # Microsecond precision, not just seconds — a quick stop/start cycle
    # during dev testing (this project has seen several within the same
    # minute) must not produce two archives that collide on the same
    # second-granularity name and silently overwrite each other.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")

    log_file = str(manifest.get("log_file_path", "")).strip()
    if log_file:
        log_path = Path(log_file)
        if _other_live_instance_shares_log(log_path, instance):
            print(f"log {log_path} is still in use by another running instance; leaving it for that instance's own stop to archive")
        elif log_path.is_file():
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = _archive_and_remove(log_path, archive_dir, timestamp)
                print(f"archived log -> {dest}")
            except OSError as exc:
                print(f"warning: failed to archive log {log_path}: {exc}")

    profiler = manifest.get("profiler")
    if isinstance(profiler, dict) and profiler.get("tool") == "pyinstrument":
        output_path = Path(str(profiler.get("output_path", ""))).expanduser()
        # The SIGINT stop() already sent (and waited out) is what makes
        # pyinstrument flush this file — by now the profiled process is
        # confirmed dead, so it should already exist; poll briefly anyway
        # as a safety margin against a slow filesystem flush rather than
        # assuming zero latency.
        deadline = time.monotonic() + 10
        while not output_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.2)
        if output_path.is_file():
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = _archive_and_remove(output_path, archive_dir, timestamp)
                print(f"archived profile -> {dest}")
            except OSError as exc:
                print(f"warning: failed to archive profile {output_path}: {exc}")
        else:
            print(f"warning: pyinstrument output not found at {output_path}, nothing archived")


def _stop_attached_profiler(profiler: dict, timeout: float) -> None:
    pid, pgid, _actual = _verified_attached_profiler(profiler)
    if not _alive(pid):
        return
    _terminate_process_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):
        _terminate_process_group(pgid, signal.SIGKILL)


def stop(instance: str) -> int:
    manifest_path, manifest = _load_manifest(instance)
    pid, pgid, actual = _verified_process(manifest)
    timeout = max(1.0, float(_settings().get("BOT_STOP_TIMEOUT", "10")))
    print(f"stopping {instance} pid={pid} ({actual})")
    profiler = manifest.get("profiler")
    graceful_signal = signal.SIGINT if isinstance(profiler, dict) and profiler.get("tool") == "pyinstrument" else signal.SIGTERM
    os.killpg(pgid, graceful_signal)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):
        os.killpg(pgid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.1)
    if _alive(pid):
        raise SystemExit(f"failed to stop {instance}; manifest retained: {manifest_path}")
    if isinstance(profiler, dict) and profiler.get("tool") == "py-spy":
        _stop_attached_profiler(profiler, timeout)
    _archive_stopped_instance_artifacts(instance, _settings(), manifest)
    manifest_path.unlink(missing_ok=True)
    print(f"stopped {instance}; log retained at {manifest.get('log_path', '')}")
    return 0


def status(instance: str | None) -> int:
    manifests = [_manifest_path(instance)] if instance else sorted(RUNTIME_DIR.glob("*.json"))
    if not manifests:
        print("no bot instances")
        return 0
    for path in manifests:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data["pid"])
            state = "running" if _alive(pid) else "stale"
            started_at = data.get("started_at", "")
            profiler = data.get("profiler")
            profile_note = (
                f" profiler={profiler.get('tool')} profile={profiler.get('output_path')}"
                if isinstance(profiler, dict)
                else ""
            )
            print(
                f"{data.get('instance', path.stem)} bot={data.get('bot', '?')} pid={pid} "
                f"{state} started={started_at} log={data.get('log_path', '')}{profile_note}"
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"{path.name}: invalid ({exc})")
    return 0


def _clean_targets(settings: dict[str, str]) -> list[Path]:
    data_dir = _path_setting(settings, "DATA_DIR", "data/groups")
    db_path = _path_setting(settings, "DB_PATH", str(data_dir.parent / "coc_bot.db"))
    backup_dir = _path_setting(settings, "BACKUP_DIR", str(db_path.parent / "backups"))
    scenario_dir = _path_setting(settings, "SCENARIO_LIBRARY_DIR", str(data_dir.parent / "scenarios"))
    import_dir = _path_setting(settings, "IMPORT_DIR", "imports")
    targets = [data_dir, db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), Path(f"{db_path}-journal"), backup_dir, scenario_dir, import_dir]
    unique: list[Path] = []
    for target in targets:
        if target not in unique:
            unique.append(target)
    return unique


def _validate_clean_target(target: Path) -> None:
    root = ROOT.resolve()
    home = Path.home().resolve()
    if target in {root, home} or target.name.startswith(".env") or target == RUNTIME_DIR.resolve():
        raise SystemExit(f"refusing unsafe clean target: {target}")
    if target == Path("/") or len(target.parts) < 2:
        raise SystemExit(f"refusing unsafe clean target: {target}")


def clean(confirmed: bool) -> int:
    if not confirmed:
        raise SystemExit("clean is destructive; re-run with --yes")
    settings = _settings()
    targets = _clean_targets(settings)
    for target in targets:
        _validate_clean_target(target)
    print("cleaning:")
    for target in targets:
        print(f"  {target}")
        if target.is_dir() and not target.is_symlink():
            import shutil
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    start_parser = subparsers.add_parser(
        "start",
        description="Start one bot instance and record its runtime manifest.",
        epilog=(
            "Profiler is opt-in (default: off):\n"
            "  BOT_PROFILER=pyinstrument ./scripts/start_bot.sh discord --name profile-async\n"
            "  BOT_PROFILER=py-spy ./scripts/start_bot.sh discord --name profile-live\n"
            "Allowed values: off, pyinstrument, py-spy. Artifacts are written under "
            ".runtime/bots/. py-spy may require root or process-attach permission on macOS."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    start_parser.add_argument("bot", choices=("discord",))
    start_parser.add_argument("--name")
    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("instance")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("instance", nargs="?")
    clean_parser = subparsers.add_parser("clean")
    clean_parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.action == "start":
        return start(args.bot, args.name)
    if args.action == "stop":
        return stop(args.instance)
    if args.action == "status":
        return status(args.instance)
    return clean(args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
