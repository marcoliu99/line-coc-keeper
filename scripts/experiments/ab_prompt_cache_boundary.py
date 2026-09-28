#!/usr/bin/env python3
"""A/B the prompt cache boundary against the real API, before implementing WP2.

Arm A reproduces today's composition: instructions = static + dynamic, so the
per-turn dynamic block sits inside the cached prefix and the tool schema after
it. Arm B moves the dynamic block into the input items, leaving static and
tools as a stable prefix.

Real state, real tool schema, real player messages. Dynamic varies per turn as
it does in play (HP/SAN move), because a dynamic block that did not vary would
cache in arm A too and prove nothing. One request per turn, so what is measured
is cross-turn reuse — exactly the 0.0% figure WP2 targets.

Costs real tokens. Requires OPENAI_API_KEY. Writes nothing to game state: the
database is opened read-only and per-turn variation is in memory only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app import config, keeper
from app.agents.tool_gateway import tools_for_speaker_role
from app.models import GroupState
from app.services import input_budget, prompt_config


def load_states(db_path: Path) -> list[tuple[str, GroupState]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = connection.execute("select key, data from group_states").fetchall()
    return [(key, GroupState.from_dict(json.loads(data))) for key, data in rows]


def player_turns(states: list[tuple[str, GroupState]], wanted: int,
                 skip: int = 0) -> list[tuple[GroupState, str]]:
    turns: list[tuple[GroupState, str]] = []
    for _, state in states:
        for entry in state.log:
            if isinstance(entry, dict) and entry.get("role") == "user":
                turns.append((state, str(entry.get("content", ""))))
    selected = turns[skip:]
    if not selected:
        return []
    # Recorded messages run out before a long run does. Cycle them and mark
    # each turn so no two requests share a player message, which would let an
    # arm match a previous turn's prefix and read as a win it did not earn.
    out: list[tuple[GroupState, str]] = []
    for index in range(wanted):
        state, message = selected[index % len(selected)]
        lap = index // len(selected)
        out.append((state, message if lap == 0 else f"{message}（第 {index + 1} 輪）"))
    return out


def provider_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "name": t["name"], "description": t["description"],
             "parameters": t["input_schema"]} for t in tools]


async def run_arm(client, arm: str, turns, tools, max_output: int) -> list[dict]:
    records: list[dict] = []
    seen_dynamic: set[str] = set()
    for index, (state, message) in enumerate(turns):
        character = next(iter(state.active_characters()), None)
        owner = character.owner_id if character else ""
        if character is not None:
            # Dynamic content must be distinct every turn, not merely varying:
            # a cyclic perturbation lets a later turn match an earlier turn's
            # prefix, which read as an 88.7% hit rate for the arm this is
            # supposed to show failing. Real play never repeats a dynamic
            # block — the recorded sessions cache 0.0% on a turn's first
            # request — so Luck carries a monotonic per-turn value here.
            character.hp = max(1, (character.hp_max or 10) - (index % 5))
            character.san = max(1, (character.san_max or 50) - (index % 7))
            character.luck = 20 + index
        static_system = prompt_config.build_executor_static_prompt(keeper._build_static_prompt(state))
        dynamic_system = prompt_config.build_executor_dynamic_prompt_with_context(
            keeper._build_dynamic_prompt(state, owner, None, "player"), "", "")

        if arm == "A":
            instructions = f"{static_system}\n\n{dynamic_system}"
            input_items: list[dict] = [{"role": "user", "content": message}]
        elif arm == "B":
            instructions = static_system
            input_items = [{"role": "developer", "content": dynamic_system},
                           {"role": "user", "content": message}]
        elif arm == "C":
            # Diagnostic only, not a shippable shape: dropping the dynamic
            # block entirely isolates whether arm B lost its hits because of
            # the developer message in input or because instructions alone
            # stopped being cached.
            instructions = static_system
            input_items = [{"role": "user", "content": message}]
        else:
            # Dynamic placed after the varying user message.
            instructions = static_system
            input_items = [{"role": "user", "content": message},
                           {"role": "developer", "content": dynamic_system}]

        started = time.monotonic()
        response = await client.responses.create(
            model=config.OPENAI_MODEL, instructions=instructions, input=input_items,
            tools=provider_tools(tools), max_output_tokens=max_output,
            prompt_cache_key=f"ab-cache-boundary-{arm}",
        )
        usage = response.usage
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        total_in = getattr(usage, "input_tokens", 0) or 0
        if not total_in:
            # A response can come back without usage accounting; record it as
            # unusable rather than dividing by zero or silently counting a hit.
            print(f"  {arm}{index + 1:>3}  no usage reported", flush=True)
            records.append({"arm": arm, "turn": index + 1, "input_tokens": 0,
                            "cached_tokens": 0, "output_tokens": 0,
                            "latency_s": time.monotonic() - started, "usable": False})
            continue
        seen_dynamic.add(dynamic_system)
        records.append({
            "arm": arm, "turn": index + 1, "input_tokens": total_in,
            "cached_tokens": cached, "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "latency_s": time.monotonic() - started, "usable": True,
        })
        print(f"  {arm}{index + 1:>3}  in={total_in:>6} cached={cached:>6}"
              f"  {100 * cached / total_in:5.1f}%  {records[-1]['latency_s']:5.2f}s",
              flush=True)
    usable = [r for r in records if r.get("usable", True)]
    note = ""
    if len(seen_dynamic) < 2:
        note = "  (CONSTANT — a cache hit here proves nothing)"
    elif len(seen_dynamic) < len(usable):
        note = "  (REPEATS — a later turn can match an earlier turn's prefix)"
    print(f"  distinct dynamic blocks across this arm: {len(seen_dynamic)} of {len(usable)}{note}")
    return records


def summarise(all_records: list[dict], label: str, estimates: dict[str, int]) -> None:
    records = [r for r in all_records if r.get("usable", True)]
    if not records:
        print(f"\n{label}: no usable responses")
        return
    dropped = len(all_records) - len(records)
    total_in = sum(r["input_tokens"] for r in records)
    total_cached = sum(r["cached_tokens"] for r in records)
    latencies = [r["latency_s"] for r in records]
    print(f"\n{label}")
    print(f"  requests           {len(records)}" + (f"  ({dropped} without usage)" if dropped else ""))
    print(f"  input tokens       {total_in:,}")
    print(f"  cached tokens      {total_cached:,}   ({100 * total_cached / total_in:.1f}%)")
    print(f"  first turn cached  {records[0]['cached_tokens']:,}")
    print(f"  later turns cached {statistics.median(r['cached_tokens'] for r in records[1:]):,.0f} (median)")
    print(f"  latency median     {statistics.median(latencies):.2f}s   p90 "
          f"{sorted(latencies)[int(len(latencies) * 0.9)]:.2f}s")
    print(f"  local estimate     static {estimates['static']:,} + tools {estimates['tools']:,} "
          f"+ dynamic {estimates['dynamic']:,}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path,
                        default=Path.home() / "workspace/line-coc-keeper-main-v2/data/coc_bot.db")
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--skip", type=int, default=0,
                        help="skip leading recorded turns; use it to land on turns whose "
                             "dynamic block actually varies (a state with an active character)")
    parser.add_argument("--max-output", type=int, default=96)
    parser.add_argument("--arms", default="AB")
    parser.add_argument("--out", type=Path, help="write per-request records as JSON")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    states = load_states(args.db)
    turns = player_turns(states, args.turns, args.skip)
    tools = tools_for_speaker_role("player")
    if len(turns) < args.turns:
        print(f"only {len(turns)} recorded player messages available", file=sys.stderr)

    sample_state = turns[0][0]
    owner = next((c.owner_id for c in sample_state.active_characters()), "")
    estimates = {
        "static": input_budget.estimate(
            prompt_config.build_executor_static_prompt(keeper._build_static_prompt(sample_state)),
            config.OPENAI_MODEL),
        "dynamic": input_budget.estimate(
            prompt_config.build_executor_dynamic_prompt_with_context(
                keeper._build_dynamic_prompt(sample_state, owner, None, "player"), "", ""),
            config.OPENAI_MODEL),
        "tools": input_budget.estimate(provider_tools(tools), config.OPENAI_MODEL),
    }
    print(f"model {config.OPENAI_MODEL}  turns {len(turns)}  arms {args.arms}")
    print(f"estimated input per request ~{sum(estimates.values()):,} tokens\n")

    import openai

    client = openai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    all_records: list[dict] = []
    try:
        for arm in args.arms:
            print(f"arm {arm}: {'dynamic inside instructions (today)' if arm == 'A' else 'dynamic in input items (WP2)'}")
            all_records += await run_arm(client, arm, turns, tools, args.max_output)
    finally:
        await client.close()

    for arm in args.arms:
        records = [r for r in all_records if r["arm"] == arm]
        if records:
            summarise(records, f"arm {arm}", estimates)
    if args.out:
        args.out.write_text(json.dumps(all_records, ensure_ascii=False, indent=1))
        print(f"\nrecords written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
