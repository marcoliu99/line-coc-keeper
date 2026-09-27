#!/usr/bin/env python3
"""Play the same turns twice through the real pipeline, with the dynamic
prompt block placed each way, and print both narrations for comparison.

WP2 moves a block the model used to read as `instructions` into a developer
message after the player's line. Offline tests show it is delivered; they
cannot show the model still follows it. This runs Executor, Narrator, Guard
and the spoiler scan for real against a copy of the live database, so the
narration can be read side by side.

Costs real tokens and really calls the model. It never touches live data:
storage paths are redirected before app.config is imported and the database
is a copy, restored between arms so both start from the same state.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SANDBOX = Path(tempfile.mkdtemp(prefix="coc-live-ab-"))
os.environ["DATA_DIR"] = str(SANDBOX / "groups")
os.environ["DB_PATH"] = str(SANDBOX / "coc_bot.db")
os.environ["BACKUP_DIR"] = str(SANDBOX / "backups")
os.environ["SCENARIO_LIBRARY_DIR"] = str(SANDBOX / "scenarios")
os.environ["IMPORT_DIR"] = str(SANDBOX / "imports")

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

from app import config, logging_config
from app.agents import supervisor
from app.legacy_commands import handle_check_command
from app.repositories.group_state import load_state

# Structured logging is started by discord_bot.main(), which this never calls,
# so usage events would otherwise be lost and the cache result unverifiable.
logging_config.configure_logging(force=True)

TURNS = [
    "我仔細看看這個房間有什麼不對勁的地方",
    "我檢查一下自己身上還有什麼東西",
    "我想聽聽樓上有沒有聲音",
    "我把油燈舉高，照一照牆角",
    "我敲敲那面木板牆，聽聽是不是空心的",
    "我往樓梯走過去",
    "我沿著樓梯慢慢往上走",
    "我停在樓梯口，先觀察一下四周",
    "我推開最近的那扇門",
    "我進去之後先環顧整個房間",
    "我走到書桌旁邊，看看桌上有什麼",
    "我拉開書桌的抽屜",
    "我翻一翻抽屜裡的紙張",
    "我把找到的東西收進背包",
    "我走到窗邊，把窗簾拉開",
    "我往窗外看出去",
    "我回頭看看房間裡還有沒有別的出口",
    "我仔細聞聞這個房間的氣味",
    "我蹲下來檢查地板有沒有痕跡",
    "我拿出筆記本把看到的記下來",
]


def restore(pristine: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(os.environ["DB_PATH"] + suffix)
        if target.exists():
            target.unlink()
    shutil.copy2(pristine, os.environ["DB_PATH"])


async def resolve_pending(group_id: str, owner: str) -> str:
    """Roll an outstanding check the way a player pressing the button would.

    Without this a long session stalls: every later turn is refused because an
    earlier check is still waiting, and the run measures the refusal path
    instead of ordinary play.
    """
    state = load_state(group_id)
    if owner not in state.pending_checks:
        return ""
    lines: list[str] = []

    async def reply(message: str) -> None:
        lines.append(message)

    async def noop(*_args, **_kwargs) -> None:
        return None

    try:
        await handle_check_command(group_id, owner, reply, noop, noop, noop, "/coc check")
    except Exception as exc:  # noqa: BLE001 - a failed roll is a result too
        lines.append(f"[check raised {type(exc).__name__}: {exc}]")
    return "\n".join(lines)


async def play(group_id: str, turns: list[str], after_input: bool) -> list[dict]:
    config.OPENAI_DYNAMIC_PROMPT_AFTER_INPUT = after_input
    from app.providers import openai_provider
    openai_provider.config.OPENAI_DYNAMIC_PROMPT_AFTER_INPUT = after_input

    results: list[dict] = []
    for index, text in enumerate(turns, 1):
        state = load_state(group_id)
        character = next(iter(state.active_characters()), None)
        owner = character.owner_id if character else "probe-user"
        name = character.name if character else "調查員"
        started = time.monotonic()
        try:
            reply, private, images = await supervisor.run_turn(
                state, owner, name, text, None, "player", group_id)
        except Exception as exc:  # noqa: BLE001 - a failed turn is a result too
            reply, private, images = f"[turn raised {type(exc).__name__}: {exc}]", [], []
        check = await resolve_pending(group_id, owner)
        results.append({
            "turn": index, "text": text, "reply": reply, "check": check,
            "private": len(private), "images": len(images),
            "seconds": time.monotonic() - started,
            "hp": getattr(character, "hp", None), "san": getattr(character, "san", None),
        })
        print(f"  turn {index} ({results[-1]['seconds']:.1f}s): {reply[:80]}"
              + (f"  [check: {check[:50]}]" if check else ""), flush=True)
    return results


def show(label: str, records: list[dict]) -> None:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    for record in records:
        print(f"\n--- turn {record['turn']}  ({record['seconds']:.1f}s, "
              f"private={record['private']}, images={record['images']}) ---")
        print(f"> {record['text']}")
        print(record["reply"])
        if record.get("check"):
            print(f"[check] {record['check']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-db", type=Path,
                        default=Path.home() / "workspace/line-coc-keeper-main-v2/data/coc_bot.db")
    parser.add_argument("--group", default="discord-channel-1550744273060765719")
    parser.add_argument("--turns", type=int, default=len(TURNS))
    parser.add_argument("--only-wp2", action="store_true",
                        help="run the shipping composition alone, for a longer session")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    pristine = SANDBOX / "pristine.db"
    shutil.copy2(args.source_db, pristine)
    turns = TURNS[:args.turns]
    print(f"sandbox {SANDBOX}\ngroup {args.group}\nturns {len(turns)}\n")

    arms = [("after_input=True (WP2)", True)] if args.only_wp2 else [
        ("after_input=False (today)", False), ("after_input=True (WP2)", True)]
    outcomes = {}
    for label, after_input in arms:
        print(f"arm {label}")
        restore(pristine)
        outcomes[label] = await play(args.group, turns, after_input)

    for label, records in outcomes.items():
        show(label, records)

    print(f"\n{'=' * 78}\nsummary\n{'=' * 78}")
    for label, records in outcomes.items():
        lengths = [len(r["reply"]) for r in records]
        seconds = [r["seconds"] for r in records]
        failed = sum(1 for r in records if r["reply"].startswith("[turn raised"))
        incomplete = sum(1 for r in records if "尚未完整處理" in r["reply"] or "無法繼續" in r["reply"])
        rolled = sum(1 for r in records if r.get("check"))
        print(f"  {label:<28} replies={len(records)} failed={failed} "
              f"incomplete={incomplete} checks_rolled={rolled} "
              f"chars median={sorted(lengths)[len(lengths) // 2]} "
              f"seconds median={sorted(seconds)[len(seconds) // 2]:.1f}")
    print(f"\nsandbox left at {SANDBOX} for inspection; live data untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
