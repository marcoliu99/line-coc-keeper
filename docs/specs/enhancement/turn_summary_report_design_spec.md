# Counting the turns that did not finish, from the turn summary line

[繁體中文](turn_summary_report_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `f03a110`.

## Problem

The Dead Boarder five-player validation report says "Per-turn errors: 0" while 15 of its 102 turns ended in a generic "not fully processed" reply, four of them "internal error". Nothing was wrong with the count: a turn whose Executor fails is caught inside the turn and delivered as a reply, so no exception reaches a harness that counts exceptions. The numbers that would have shown it (`fallback=`, `queue_wait_ms=`, per-phase times) have been written to the ordinary text log on every turn since the turn summary line (`docs/specs/enhancement/turn_summary_log_design_spec.md`), but nothing reads them.

The validation harness itself is not in this repository, so its own "errors" field cannot be corrected here.

## Change

`scripts/summarize_turn_log.py` reads `turn.summary` lines (logger `app.turn`) from a log file or every file under a directory and prints:

- how long players waited for an action (wall p50/p90/p95/p99/max, queue wait, the median of each phase), counting ordinary turns only so a 25 s dice continuation is not mistaken for a player's wait;
- how many lines ended with a `fallback=` reason, in total and by reason, with a note that `internal_error` is invisible to anything that counts exceptions;
- the routes, the turns answered from state, and the five slowest turns with their ids.

`--json` prints the same numbers for another tool (a harness can read them instead of counting exceptions), `--since` drops lines stamped earlier. Exit status 2 when no line was read, which usually means the log does not include logger `app.turn` or `LOG_TEXT_ENABLED` is off, so an empty log is an error, not a clean report.

Read-only, no dependency on `LOG_ENABLED`, and it never sees player text because the line does not carry any.

## Verification

`tests/test_summarize_turn_log.py` parses the line the runtime actually writes (through the real logger), so a change to the line's format fails the test; also a log prefix and noise, the counts by reason, ordinary turns only for the percentiles, a directory and `--json`, `--since`, and an empty or missing log. It has not been run on a real deployment's logs.
