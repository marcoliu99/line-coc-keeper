#!/usr/bin/env python3
"""Process-safe lifecycle commands for local bot instances."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - normal installs include python-dotenv
    dotenv_values = None


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime" / "bots"
INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


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
    expected_tokens = [token for token in command if token.startswith("app.") or token == "uvicorn"]
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


def _command(bot: str, settings: dict[str, str]) -> list[str]:
    python = settings.get("BOT_PYTHON", _python_executable())
    if bot == "discord":
        return [python, "-m", "app.discord_bot"]
    if bot == "line":
        uvicorn = settings.get("BOT_UVICORN", "")
        if not uvicorn:
            candidate = ROOT / ".venv" / "bin" / "uvicorn"
            uvicorn = str(candidate) if candidate.is_file() else "uvicorn"
        return [uvicorn, "app.main:app", "--host", settings.get("BOT_HOST", "127.0.0.1"), "--port", settings.get("BOT_PORT", "8000")]
    raise SystemExit("usage: start_bot.sh line|discord [--name INSTANCE]")


def start(bot: str, name: str | None) -> int:
    settings = _settings()
    command = _command(bot, settings)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    instance = name or f"{bot}-{timestamp}-{secrets.token_hex(3)}"
    manifest_path = _manifest_path(instance)
    if manifest_path.exists():
        raise SystemExit(f"instance already exists: {instance}")
    log_path = RUNTIME_DIR / f"{instance}.log"
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")
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
    time.sleep(0.15)
    if process.poll() is not None:
        log_path.unlink(missing_ok=True)
        raise SystemExit(f"{bot} exited immediately; see {log_path}")
    manifest = {
        "instance": instance,
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(ROOT),
        "bot": bot,
        "command": command,
        "log_path": str(log_path),
    }
    try:
        _write_json_atomic(manifest_path, manifest)
    except OSError as exc:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        log_path.unlink(missing_ok=True)
        raise SystemExit(f"failed to record {instance}: {exc}")
    print(f"started {instance} pid={process.pid} log={log_path}")
    return 0


def stop(instance: str) -> int:
    manifest_path, manifest = _load_manifest(instance)
    pid, pgid, actual = _verified_process(manifest)
    timeout = max(1.0, float(_settings().get("BOT_STOP_TIMEOUT", "10")))
    print(f"stopping {instance} pid={pid} ({actual})")
    os.killpg(pgid, signal.SIGTERM)
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
            print(f"{data.get('instance', path.stem)} bot={data.get('bot', '?')} pid={pid} {state} log={data.get('log_path', '')}")
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
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("bot", choices=("line", "discord"))
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
