#!/usr/bin/env python3
"""How long players waited and how many turns did not finish, from the `turn.summary` lines alone.

Read-only. Every turn writes one plain line (logger `app.turn`) whether or not `LOG_ENABLED` is on, so this works on
any deployment's ordinary text log. It carries timings and ids only, never player text. Unlike a harness counting
exceptions, it also counts the turns that ended in a generic reply: a turn whose Executor failed is recorded as
`fallback=internal_error` and raises nothing.

    scripts/summarize_turn_log.py logs/                  # every file under a directory
    scripts/summarize_turn_log.py run.log --json         # the same numbers for another tool
    scripts/summarize_turn_log.py logs/ --since 2026-10-05T04:52

Exit status: 0 when at least one `turn.summary` line was read, 2 when none was (the usual cause is a log that does not
include logger `app.turn`, or `LOG_TEXT_ENABLED=false`).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any

_MARKER = "turn.summary "
_LOGGER = "app.turn"
_FIELD = re.compile(r"(\w+)=(\S*)")
_TIMING_FIELDS = ("wall_ms", "queue_wait_ms", "retrieval_ms", "memory_ms", "executor_ms", "tool_ms",
                  "continuation_ms", "narrator_ms", "other_ms")
# `LOG_FORMAT=text`: "<timestamp> <LEVEL> <logger> <message>[ context key=value ...]" (app/logging_config.py).
_TEXT_LINE = re.compile(r"^(?P<stamp>\S+)\s+[A-Z]+\s+" + re.escape(_LOGGER) + r"\s+(?P<message>" + re.escape(_MARKER) + r".*)$")


def _envelope(line: str) -> tuple[str, str] | None:
    """The timestamp and message of a line that the `app.turn` logger wrote, in either log format.

    The default `LOG_FORMAT=json` wraps the message in an object; matching the marker anywhere in a line would also accept
    another logger's text that happens to contain it (a logged Keeper reply, for instance), so the logger is checked.
    """
    text = line.strip()
    if text.startswith("{"):
        try:
            record = json.loads(text)
        except ValueError:
            return None
        if not isinstance(record, dict) or record.get("logger") != _LOGGER:
            return None
        message = record.get("message")
        if not isinstance(message, str) or not message.startswith(_MARKER):
            return None
        return str(record.get("timestamp", "")), message
    match = _TEXT_LINE.match(text)
    return (match["stamp"], match["message"]) if match else None


def parse_line(line: str) -> dict[str, Any] | None:
    """The fields of one `turn.summary` line written by the `app.turn` logger, or None for any other line."""
    found = _envelope(line)
    if found is None:
        return None
    stamp, message = found
    fields: dict[str, Any] = {}
    for key, value in _FIELD.findall(message[len(_MARKER):]):
        if key in _TIMING_FIELDS:
            try:
                fields[key] = float(value)
            except ValueError:
                return None
        else:
            fields.setdefault(key, value)  # a text log appends context keys (turn_id, request_id) after the message
    if "turn_id" not in fields or "wall_ms" not in fields:
        return None
    fields["_stamp"] = stamp
    return fields


def parse_failure(line: str) -> dict[str, Any] | None:
    """One ``llm.turn.failed`` event (a model request that did not finish) from a structured JSON log line, or None.

    These come from event logging (``LOG_ENABLED``) and so only exist in the JSON runtime log. They are what a turn's
    ``internal_error`` is made of, and the one place that says whether tools had already run (``tool_call_count``).
    """
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("event") != "llm.turn.failed":
        return None
    return {key: record.get(key) for key in ("timestamp", "turn_id", "agent", "provider", "status", "error_type",
                                             "duration_ms", "tool_call_count")}


def read_failures(paths: list[Path], since: str | None) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                failure = parse_failure(line)
                if failure is None or (since and failure["timestamp"] and str(failure["timestamp"]) < since):
                    continue
                failures.append(failure)
    return failures


def read(paths: list[Path], since: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                row = parse_line(line)
                if row is None:
                    continue
                if since and row["_stamp"] and row["_stamp"] < since:
                    continue
                rows.append(row)
    return rows


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _spread(values: list[float]) -> dict[str, float]:
    return {"p50": percentile(values, 0.50), "p90": percentile(values, 0.90),
            "p95": percentile(values, 0.95), "p99": percentile(values, 0.99), "max": max(values, default=0.0)}


def _failures(failures: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    after_tools = [f for f in failures if (f.get("tool_call_count") or 0) > 0]
    return {
        "total": len(failures),
        "by_error": dict(collections.Counter(f"{f.get('agent') or '?'}/{f.get('error_type') or '?'}" for f in failures).most_common()),
        # A request that failed after tools ran may have committed changes the player is not told about.
        "after_tool_calls": len(after_tools),
        "duration_ms": _spread([float(f["duration_ms"]) for f in failures if f.get("duration_ms") is not None]),
        "turn_summaries_marked_internal_error": sum(1 for r in rows if r.get("fallback") == "internal_error"),
    }


def summarize(rows: list[dict[str, Any]], failures: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    turns = [r for r in rows if r.get("kind", "turn") == "turn"]
    reasons = collections.Counter(r["fallback"] for r in rows if r.get("fallback"))
    degraded = sum(reasons.values())
    return {
        "lines": len(rows),
        "turns": len(turns),
        "continuations": len(rows) - len(turns),
        "wall_ms": _spread([r["wall_ms"] for r in turns]),
        "queue_wait_ms": _spread([r.get("queue_wait_ms", 0.0) for r in turns]),
        "phase_p50_ms": {f: percentile([r.get(f, 0.0) for r in turns], 0.5) for f in _TIMING_FIELDS[2:]},
        "routes": dict(collections.Counter(r.get("route", "") or "unknown" for r in turns)),
        "short_circuits": dict(collections.Counter(r["short_circuit"] for r in rows if r.get("short_circuit"))),
        "fallbacks": {
            "total": degraded,
            "share_of_lines": degraded / len(rows) if rows else 0.0,
            "by_reason": dict(reasons.most_common()),
        },
        "failed_requests": _failures(failures or [], rows),
        "slowest": [{"turn_id": r["turn_id"], "kind": r.get("kind", ""), "wall_ms": r["wall_ms"],
                     "fallback": r.get("fallback", "")}
                    for r in sorted(rows, key=lambda r: r["wall_ms"], reverse=True)[:5]],
    }


def render(data: dict[str, Any]) -> str:
    wall, queue = data["wall_ms"], data["queue_wait_ms"]
    out = [
        f"summary lines: {data['lines']}  (turns {data['turns']}, continuations {data['continuations']})",
        "",
        "== what players waited (turns, seconds) ==",
        (f"  wall   p50 {wall['p50'] / 1000:.1f}  p90 {wall['p90'] / 1000:.1f}  p95 {wall['p95'] / 1000:.1f}"
         f"  p99 {wall['p99'] / 1000:.1f}  max {wall['max'] / 1000:.1f}"),
        f"  queue  p50 {queue['p50'] / 1000:.1f}  p95 {queue['p95'] / 1000:.1f}  max {queue['max'] / 1000:.1f}",
        "  median per phase: " + "  ".join(f"{name.removesuffix('_ms')} {ms / 1000:.1f}s"
                                           for name, ms in data["phase_p50_ms"].items()),
        "",
        "== turns that did not finish ==",
    ]
    fallbacks = data["fallbacks"]
    out.append(f"  {fallbacks['total']} of {data['lines']} lines ({100 * fallbacks['share_of_lines']:.1f}%)")
    for reason, count in fallbacks["by_reason"].items():
        out.append(f"    {reason:<26}{count:>4}")
    if "internal_error" in fallbacks["by_reason"]:
        out.append("  internal_error is a failed model/tool step caught inside the turn: no exception reaches the caller,")
        out.append("  so a harness that counts exceptions reports 0. Look the turn_id up in the runtime log.")
    failed = data["failed_requests"]
    if failed["total"]:
        out += ["", "== model requests that failed (llm.turn.failed) =="]
        out.append(f"  {failed['total']} failed, {failed['after_tool_calls']} of them after tool calls had run "
                   "(changes may be committed)")
        out += [f"    {kind:<32}{count:>4}" for kind, count in failed["by_error"].items()]
        spread = failed["duration_ms"]
        out.append(f"  duration p50 {spread['p50'] / 1000:.1f}s  max {spread['max'] / 1000:.1f}s")
        marked = failed["turn_summaries_marked_internal_error"]
        if marked < failed["total"]:
            out.append(f"  only {marked} turn summary line(s) say internal_error: the rest of these turns ended some other way, "
                       "or their summary line is missing. Look the turn_id up in the runtime log.")
    out += ["", "== routes ==", "  " + ("  ".join(f"{k} {v}" for k, v in sorted(data["routes"].items())) or "-")]
    if data["short_circuits"]:
        out.append("  answered from state: " + "  ".join(f"{k} {v}" for k, v in data["short_circuits"].items()))
    out += ["", "== slowest =="]
    out += [f"  {row['wall_ms'] / 1000:>6.1f}s  {row['turn_id']}  {row['kind']}  {row['fallback'] or '-'}"
            for row in data["slowest"]]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", type=Path, help="a log file, or a directory whose files are all read")
    parser.add_argument("--since", help="ISO timestamp prefix; drop lines stamped before it")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON instead of a report")
    args = parser.parse_args(argv)

    if args.target.is_dir():
        paths = sorted(p for p in args.target.rglob("*") if p.is_file())
    elif args.target.is_file():
        paths = [args.target]
    else:
        print(f"no such log file or directory: {args.target}", file=sys.stderr)
        return 2
    rows = read(paths, args.since)
    if not rows:
        print(f"no turn.summary lines in {len(paths)} file(s) under {args.target}; the text log must include logger "
              "app.turn (LOG_TEXT_ENABLED=true)", file=sys.stderr)
        return 2
    data = summarize(rows, read_failures(paths, args.since))
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
