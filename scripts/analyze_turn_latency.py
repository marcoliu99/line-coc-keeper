#!/usr/bin/env python3
"""Reproduce the turn latency baseline from structured runtime logs.

Read-only. Every metric here already exists in the logs — `observability.
usage_fields` records cached_input_tokens, `lock.wait` spans carry durations,
`turn.queue` carries queue depth — so this is a reader, not instrumentation.

The numbers this prints are what docs/specs/enhancement/
measured_turn_latency_priorities_design_spec.md bases its ordering on. Run it
before and after a change so a comparison is repeatable rather than derived by
hand.

    scripts/analyze_turn_latency.py ~/coc_v2_log
    scripts/analyze_turn_latency.py ~/coc_v2_log/20260927-132223-807874_runtime_profile-async.log
    scripts/analyze_turn_latency.py ~/coc_v2_log --since 2026-09-27T13:00
"""
from __future__ import annotations

import argparse
import ast
import collections
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

# A request id groups one player message; model usage arrives per model call.
Key = tuple[str, str]


def _record_ids(found: str) -> frozenset[str]:
    """Parse one logged `evidence_record_ids=[...]` list.

    Extracting `r\\d+` substrings instead silently emptied every set for a
    scenario whose ids are words such as `intro`, and truncated a compound id
    like `c1-u1-r1` to `r1`, where it could then collide with another record.
    An emptied set still counted toward the denominator while failing the
    `bool(records_b)` test, so retrieval waste read lower than it was.
    """
    text = found.strip()
    if not text:
        return frozenset()
    try:
        parsed = ast.literal_eval(f"[{text}]")
    except (ValueError, SyntaxError):
        return frozenset(part.strip().strip("'\"") for part in text.split(",") if part.strip())
    return frozenset(str(item) for item in parsed)


def _iter_events(paths: list[Path], since: str | None) -> Any:
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line.startswith("{"):
                    continue  # plain-text log lines are not structured events
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if since and str(event.get("timestamp", "")) < since:
                    continue
                yield path.name, event


def _pct(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _rate(cached: int, total: int) -> str:
    return f"{100 * cached / total:5.1f}%" if total else "    --"


def collect(paths: list[Path], since: str | None) -> dict[str, Any]:
    usage: dict[Key, list[tuple[str, int, int]]] = collections.defaultdict(list)
    lock_waits: list[float] = []
    queue: list[tuple[float, int, str, str]] = []
    agents: dict[str, list[float]] = collections.defaultdict(list)
    tools: collections.Counter[str] = collections.Counter()
    tool_rounds: collections.Counter[int] = collections.Counter()
    resolutions: collections.Counter[tuple[str, str]] = collections.Counter()
    projections: list[tuple[Any, Any, Any]] = []
    tokenizer: collections.Counter[str] = collections.Counter()
    # Per-turn retrieval, paired by order: the query lines come from the tool,
    # the record ids from the reducer's summary at the end of the turn.
    # Keyed by (file, turn_id) because two conversations' log lines interleave:
    # a single "current turn" variable would be reassigned by whichever turn
    # started most recently, attaching one turn's queries to another. Logs
    # written without a turn_id (a harness driving run_turn directly) fall back
    # to a per-file sequence number, which is the old behaviour and is only
    # correct for a single-conversation log.
    retrieval: dict[tuple[str, str], dict[str, Any]] = {}
    fallback_seq: collections.Counter[str] = collections.Counter()
    last_fallback: dict[str, str] = {}

    def turn_key(name: str, event: dict[str, Any]) -> tuple[str, str] | None:
        turn_id = event.get("turn_id")
        if turn_id:
            return (name, str(turn_id))
        seen = last_fallback.get(name)
        return (name, seen) if seen else None

    for name, event in _iter_events(paths, since):
        kind = event.get("event", "")
        key: Key = (name, str(event.get("request_id")))
        if event.get("input_tokens") is not None:
            usage[key].append((
                str(event.get("timestamp")), int(event["input_tokens"]),
                int(event.get("cached_input_tokens") or 0),
            ))
        if kind == "lock.wait.completed" and event.get("lock_name") == "conversation":
            lock_waits.append(float(event.get("duration_ms") or 0))
        if kind == "turn.queue":
            queue.append((
                float(event.get("queue_wait_ms") or 0), int(event.get("turns_ahead") or 0),
                str(event.get("route") or "?"), str(event.get("speaker_role") or "?"),
            ))
        if kind == "llm.turn.completed":
            agents[str(event.get("agent"))].append(float(event.get("duration_ms") or 0) / 1000)
            if event.get("agent") == "executor":
                tool_rounds[int(event.get("tool_call_count") or 0)] += 1
        if kind == "llm.tool.completed":
            tools[str(event.get("tool_name"))] += 1
        if kind == "executor.resolution":
            resolutions[(str(event.get("disposition")), str(event.get("validation_code")))] += 1
        if kind == "llm.history.selected":
            tokenizer[str(event.get("tokenizer"))] += 1
        message = str(event.get("message", ""))
        if message.startswith("Supervisor starting turn"):
            if not event.get("turn_id"):
                fallback_seq[name] += 1
                last_fallback[name] = f"seq:{fallback_seq[name]}"
            key_or_none = turn_key(name, event)
            if key_or_none is not None:
                retrieval.setdefault(key_or_none, {"queries": [], "records": []})
        if message.startswith("search_scenario query="):
            entry = retrieval.get(turn_key(name, event) or ("", ""))
            if entry is not None:
                entry["queries"].append(message.split("query=", 1)[1].strip().strip("'"))
        if message.startswith("StateReducer:"):
            entry = retrieval.get(turn_key(name, event) or ("", ""))
            if entry is not None:
                for found in re.findall(r"evidence_record_ids=\[([^\]]*)\]", message):
                    entry["records"].append(_record_ids(found))
        if "取用完整性" in message:
            # The projection metadata is embedded in a reducer log line.
            for blob in re.findall(r'\[\{"record_id".*?\}\]', message):
                try:
                    parsed = json.loads(blob)
                except ValueError:
                    continue
                for row in parsed:
                    projections.append((
                        row.get("budget_tokens"), row.get("complete_for_action"),
                        row.get("projection_reason"),
                    ))
    return {
        "usage": usage, "lock_waits": lock_waits, "queue": queue, "agents": agents,
        "tools": tools, "tool_rounds": tool_rounds, "resolutions": resolutions,
        "projections": projections, "tokenizer": tokenizer,
        "retrieval": list(retrieval.values()),
    }


def report(data: dict[str, Any]) -> None:
    usage = data["usage"]

    print("== cache hit rate by request position in a turn ==")
    by_position: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for calls in usage.values():
        for index, (_, tokens, cached) in enumerate(sorted(calls)):
            label = "first" if index == 0 else ("second" if index == 1 else "third+")
            bucket = by_position[label]
            bucket[0] += tokens
            bucket[1] += cached
            bucket[2] += 1
    for label in ("first", "second", "third+"):
        tokens, cached, count = by_position[label]
        if count:
            print(f"  {label:<8} n={count:<4} input={tokens:>9,} cached={cached:>9,} {_rate(cached, tokens)}")

    print("\n== cache hit rate by stage ==")
    # The Narrator's single request is much smaller than an Executor request,
    # which is the only stage marker the usage events carry.
    by_stage: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for calls in usage.values():
        for _, tokens, cached in calls:
            bucket = by_stage["narrator-shaped (<17k)" if tokens < 17000 else "executor-shaped (>=17k)"]
            bucket[0] += tokens
            bucket[1] += cached
            bucket[2] += 1
    total_in = total_cached = 0
    for label, (tokens, cached, count) in sorted(by_stage.items()):
        total_in += tokens
        total_cached += cached
        print(f"  {label:<24} n={count:<4} input={tokens:>9,} cached={cached:>9,} {_rate(cached, tokens)}")
    print(f"  {'overall':<24}           input={total_in:>9,} cached={total_cached:>9,} {_rate(total_cached, total_in)}")

    print("\n== conversation lock wait (lock.wait spans) ==")
    waits = data["lock_waits"]
    if waits:
        print(f"  n={len(waits)}  median {_pct(waits, .5):.0f}ms  p90 {_pct(waits, .9):.0f}ms"
              f"  p99 {_pct(waits, .99):.0f}ms  max {max(waits):.0f}ms"
              f"  over 1s: {sum(1 for w in waits if w > 1000)}")
    else:
        print("  none recorded")

    print("\n== turn queue (turn.queue events) ==")
    queue = data["queue"]
    if not queue:
        print("  none recorded — pre-WP3.2 logs do not carry this event")
    else:
        for field, index in (("route", 2), ("speaker_role", 3)):
            print(f"  by {field}:")
            grouped: dict[str, list[float]] = collections.defaultdict(list)
            for row in queue:
                grouped[row[index]].append(row[0])
            for label, values in sorted(grouped.items()):
                print(f"    {label:<14} n={len(values):<4} median {_pct(values, .5):>7.0f}ms"
                      f"  p90 {_pct(values, .9):>7.0f}ms  max {max(values):>7.0f}ms")
        depths = collections.Counter(row[1] for row in queue)
        print(f"  turns ahead: {dict(sorted(depths.items()))}")

    print("\n== per-agent duration ==")
    for agent, values in sorted(data["agents"].items()):
        print(f"  {agent:<10} n={len(values):<4} median {_pct(values, .5):5.1f}s"
              f"  p90 {_pct(values, .9):5.1f}s  max {max(values):5.1f}s")

    print("\n== model requests and input tokens per turn ==")
    turns = [calls for calls in usage.values() if len(calls) >= 2]
    if turns:
        counts = [len(calls) for calls in turns]
        totals = [sum(tokens for _, tokens, _ in calls) for calls in turns]
        print(f"  multi-request turns n={len(turns)}  requests median {_pct(counts, .5):.0f} max {max(counts)}")
        print(f"  input tokens median {_pct(totals, .5):,.0f}  max {max(totals):,}")

    print("\n== tool composition ==")
    tools = data["tools"]
    total_tools = sum(tools.values())
    for name, count in tools.most_common():
        print(f"  {name:<24} {count:>4}  {_rate(count, total_tools)}")
    if data["tool_rounds"]:
        print(f"  executor tool rounds per turn: {dict(sorted(data['tool_rounds'].items()))}")

    print("\n== executor resolutions ==")
    resolutions = data["resolutions"]
    total_res = sum(resolutions.values())
    incomplete = sum(v for (disposition, _), v in resolutions.items() if disposition == "incomplete")
    for (disposition, code), count in resolutions.most_common():
        print(f"  {count:>3}  {disposition} / {code}")
    if total_res:
        print(f"  incomplete: {incomplete}/{total_res} ({100 * incomplete / total_res:.0f}%)")

    print("\n== scenario projection completeness ==")
    projections = data["projections"]
    if projections:
        complete = sum(1 for _, flag, _ in projections if flag)
        budgets = [b for b, _, _ in projections if b is not None]
        print(f"  complete_for_action: {complete}/{len(projections)}")
        if budgets:
            print(f"  budget_tokens median {_pct(budgets, .5):.0f}  min {min(budgets)}  max {max(budgets)}")
        print(f"  projection_reason: {dict(collections.Counter(r for _, _, r in projections))}")
    else:
        print("  none recorded")

    print("\n== retrieval within a turn ==")
    turns = [t for t in data["retrieval"] if len(t["queries"]) >= 2]
    # Record ids are only recoverable when the reducer summarised as many
    # results as the turn issued queries; an original-source follow-up reports
    # none, so a turn is skipped rather than guessed at.
    paired = [t for t in turns if len(t["records"]) == len(t["queries"])]
    identical = subset = pairs = 0
    for entry in paired:
        rows = list(zip(entry["queries"], entry["records"], strict=True))
        for (query_a, records_a), (query_b, records_b) in itertools.pairwise(rows):
            pairs += 1
            identical += query_a == query_b
            subset += bool(records_b) and records_b <= records_a
    print(f"  turns with 2+ searches: {len(turns)}  of those with recoverable records: {len(paired)}")
    if pairs:
        print(f"  consecutive pairs: {pairs}")
        print(f"    identical query reissued {identical:>3}  {_rate(identical, pairs)}")
        print(f"    no new records returned  {subset:>3}  {_rate(subset, pairs)}")
        print("  a full split of reworded-repeat / genuinely-new / budget-retry needs the")
        print("  record ids on each llm.tool.completed; today they survive only in the")
        print("  reducer's end-of-turn summary, so this is a lower bound on waste.")

    print("\n== tokenizer ==")
    print(f"  {dict(data['tokenizer']) or 'no llm.history.selected events'}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", type=Path, help="a runtime log file, or a directory of them")
    parser.add_argument("--since", help="ISO timestamp prefix; drop events at or before it")
    parser.add_argument("--glob", default="*_runtime_*.log",
                        help="pattern used when target is a directory")
    args = parser.parse_args(argv)

    if args.target.is_dir():
        paths = sorted(args.target.glob(args.glob))
    elif args.target.is_file():
        paths = [args.target]
    else:
        print(f"no such log file or directory: {args.target}", file=sys.stderr)
        return 2
    if not paths:
        print(f"no logs matching {args.glob!r} under {args.target}", file=sys.stderr)
        return 2

    print(f"logs: {len(paths)} file(s)" + (f", since {args.since}" if args.since else ""))
    report(collect(paths, args.since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
