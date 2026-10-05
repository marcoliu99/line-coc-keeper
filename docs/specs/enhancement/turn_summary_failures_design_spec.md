# The turn summary and its report count failed model requests

[繁體中文](turn_summary_failures_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `0de529b`.

## Problem

`scripts/summarize_turn_log.py` (#202) reads the one `turn.summary` line every turn writes. Run on the Dead Boarder 200-turn logs (`1fd3ccb`) it listed `executor_no_action` 31, `unsupported_action` 14, `unresolved_pending_state` 3 and `state_conflict` 2, and **no `internal_error`**. The same run's `api-events.jsonl` holds seven `llm.turn.failed` events (five `TimeoutError`/`PermissionError`/`CodexError` after 52 to 120 s, four of them after one to four tool calls had already run) and the player-visible replies said so seven times. A report that omits the failures a player saw cannot be used to judge a run.

Two gaps in the code explain it, and neither is the reporter's parsing:

- A turn whose `run_turn` **raises** never reaches `turn_fallback.record`, which is the only place a reason is attached to the summary line. The line is still written (the timeline reports in `finally`), but it carries no reason.
- The summary line says nothing about model requests: which ones failed, how, after how long, and whether tools had run before they failed. That is in the `llm.turn.failed` event, which only exists when event logging is on.

## Changes

- `turn_phases.timeline` marks a turn that leaves by an exception: `fallback=internal_error` unless a reason was already recorded, plus `error=<ExceptionType>`. The summary line gains an `error` field (empty for a normal turn). The exception still propagates unchanged.
- `scripts/summarize_turn_log.py` also reads `llm.turn.failed` events from structured (JSON) log lines and prints a section: how many requests failed, by `agent/error_type`, how many **after tool calls had run** (the player may be told nothing while a change is committed), the duration spread, and a warning when fewer turn summaries say `internal_error` than there are failed requests, so a missing mark is visible instead of silent. `--json` carries the same numbers under `failed_requests`. Without such events (text logs, or `LOG_ENABLED=false`) nothing is added.

## Not done

- Why the Dead Boarder summaries showed no `internal_error` for failures the Executor caught itself (where `classify` should have named `internal_error`). That path was not changed here. The new warning line is what will show whether it recurs.
- Joining a failure to the player's input by `turn_id`; the input is not in the logs by design.

## Verification

`tests/test_summarize_turn_log.py`: failures parsed from the structured log and ignored in other lines, counted apart from the summaries, a report with and without the section, the CLI over a directory with both kinds of line, a turn that raises gets `fallback=internal_error` and `error=`, and a reason recorded before the raise is kept. `ruff check .` and the full `pytest` pass.
