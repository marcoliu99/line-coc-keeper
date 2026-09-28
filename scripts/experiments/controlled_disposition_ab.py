#!/usr/bin/env python3
"""Compare turn dispositions under both prompt compositions from identical state.

WP2 moved the per-turn block out of `instructions`, where it took precedence,
into a developer message after the player's line. Cache and delivery were
verified; judgement was not. A round-robin session then produced no `deferred`
turns under one composition and three under the other — but those arms had
diverged in state, so nothing was isolated.

This isolates it. The database is restored to the same snapshot before every
single turn, so both arms see identical state, identical pending items and the
same message. Repeats absorb some of the model's own variance.

Costs real tokens. Writes nothing to live data: storage paths are redirected
before app.config is imported and the database is a copy.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SANDBOX = Path(tempfile.mkdtemp(prefix="coc-controlled-ab-"))
os.environ["DATA_DIR"] = str(SANDBOX / "groups")
os.environ["DB_PATH"] = str(SANDBOX / "coc_bot.db")
os.environ["BACKUP_DIR"] = str(SANDBOX / "backups")
os.environ["SCENARIO_LIBRARY_DIR"] = str(SANDBOX / "scenarios")
os.environ["IMPORT_DIR"] = str(SANDBOX / "imports")

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

from app import config, logging_config
from app.agents import supervisor
from app.repositories.group_state import load_state

logging_config.configure_logging(force=True)

MESSAGE = "我走到書桌旁邊，看看抽屜裡有什麼"

# Each case names the pending state another investigator is left holding, which
# is what the round-robin run suggested the two compositions disagreed about.
CASES = ("clean", "other_has_check", "other_has_luck", "self_has_check")


def _pending_check(owner_name: str, timeline: str) -> dict:
    return {"type": "skill", "skill": "偵查", "check_id": f"check-{owner_name}",
            "timeline_id": timeline, "investigator": owner_name,
            "action_context": "查看地下室"}


def _pending_luck(owner_name: str, timeline: str) -> dict:
    return {"skill_name": "偵查", "value": 55, "roll": 51, "original_tier": "failure",
            "difficulty": "regular", "decision_id": f"decision-{owner_name}",
            "timeline_id": timeline, "investigator": owner_name,
            "options": [{"tier": "regular", "cost": 14}]}


def prepare(pristine: Path, group: str, case: str) -> tuple[str, str]:
    """Restore the snapshot, seed the case, and return (speaker owner, name)."""
    for suffix in ("", "-wal", "-shm"):
        target = Path(os.environ["DB_PATH"] + suffix)
        if target.exists():
            target.unlink()
    shutil.copy2(pristine, os.environ["DB_PATH"])

    connection = sqlite3.connect(os.environ["DB_PATH"])
    payload = json.loads(
        connection.execute("select data from group_states where key = ?", (group,)).fetchone()[0])
    timeline = payload.get("timeline_id") or "timeline-test"
    payload["timeline_id"] = timeline
    payload["pending_checks"] = {}
    payload["pending_luck_decisions"] = {}

    owners = [(owner, sheet["name"]) for owner, sheet in payload["characters"].items()]
    speaker, speaker_name = owners[0]
    other, other_name = owners[1] if len(owners) > 1 else owners[0]

    if case == "other_has_check":
        payload["pending_checks"][other] = _pending_check(other_name, timeline)
    elif case == "other_has_luck":
        payload["pending_luck_decisions"][other] = _pending_luck(other_name, timeline)
    elif case == "self_has_check":
        payload["pending_checks"][speaker] = _pending_check(speaker_name, timeline)

    connection.execute("update group_states set data = ? where key = ?",
                       (json.dumps(payload, ensure_ascii=False), group))
    connection.commit()
    connection.close()
    return speaker, speaker_name


async def one_turn(group: str, speaker: str, name: str, after_input: bool) -> dict:
    config.OPENAI_DYNAMIC_PROMPT_AFTER_INPUT = after_input
    from app.providers import openai_provider
    openai_provider.config.OPENAI_DYNAMIC_PROMPT_AFTER_INPUT = after_input

    state = load_state(group)
    started = time.monotonic()
    try:
        reply, _private, _images = await supervisor.run_turn(
            state, speaker, name, MESSAGE, None, "player", group)
    except Exception as exc:  # noqa: BLE001 - a failed turn is a result
        reply = f"[raised {type(exc).__name__}: {exc}]"
    return {"reply": reply, "seconds": time.monotonic() - started}


def dispositions(log_path: Path, seen: int) -> list[str]:
    """Dispositions appended to the log since the previous read."""
    found: list[str] = []
    for line in log_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "executor.resolution":
            found.append(str(event.get("disposition")))
        elif event.get("event") == "turn.short_circuit":
            found.append("short_circuit")
    return found[seen:]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--group", default="discord-channel-1550744273060765719")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", nargs="*", default=list(CASES))
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2
    log_path = Path(config.LOG_FILE) if config.LOG_FILE else None
    if log_path is None:
        print("set LOG_FILE so dispositions can be read back", file=sys.stderr)
        return 2

    pristine = SANDBOX / "pristine.db"
    shutil.copy2(args.source_db, pristine)
    seen = len(dispositions(log_path, 0)) if log_path.exists() else 0

    results: dict[tuple[str, bool], list[str]] = collections.defaultdict(list)
    replies: dict[tuple[str, bool], list[str]] = collections.defaultdict(list)
    print(f"sandbox {SANDBOX}\nmessage {MESSAGE!r}\nrepeats {args.repeats}\n")
    for case in args.cases:
        for after_input in (False, True):
            for _ in range(args.repeats):
                speaker, name = prepare(pristine, args.group, case)
                outcome = await one_turn(args.group, speaker, name, after_input)
                fresh = dispositions(log_path, seen)
                seen += len(fresh)
                verdict = fresh[-1] if fresh else "none"
                results[(case, after_input)].append(verdict)
                replies[(case, after_input)].append(outcome["reply"])
                print(f"  {case:<16} after_input={after_input!s:<5} "
                      f"{verdict:<16} {outcome['seconds']:5.1f}s  {outcome['reply'][:56]}",
                      flush=True)

    print(f"\n{'case':<18}{'today':<34}{'WP2':<34}")
    for case in args.cases:
        today = collections.Counter(results[(case, False)])
        moved = collections.Counter(results[(case, True)])
        same = "same" if today == moved else "DIFFERS"
        print(f"  {case:<16}{dict(today)!s:<34}{dict(moved)!s:<30}{same}")
    print(f"\nlive data untouched; sandbox left at {SANDBOX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
