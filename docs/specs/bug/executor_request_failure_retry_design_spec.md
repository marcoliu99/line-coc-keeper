# A failed Executor request is retried once when it ran nothing, and cleanup never hides the failure

[繁體中文](executor_request_failure_retry_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `bug`. Status: **implemented** (retry and cleanup); the tool-budget finding is **proposed only**. Source: the 5-player / 200-turn Dead Boarder run with `NARRATION_OUTSIDE_MUTATION_LOCK=true` (7 `llm.turn.failed` events, 7 player-visible internal errors). Based on `main_v2` at `0de529b`.

Seven Executor requests failed outright. Three of them had called **no tool at all**: nothing had been done, so running the request again could not repeat anything, yet the player got "the system hit an internal error" and had to retype the action. One of the seven showed `PermissionError` instead of the timeout that actually happened.

## What the evidence shows

| Event | Facts |
| --- | --- |
| `llm.turn.failed` | 7: 4 after 1–4 tool calls, 3 with none; error types TimeoutError, PermissionError, CodexError; durations 52 s to 120 s |
| turn 122 `PermissionError` | The Executor waited 120 s for Codex and timed out. Cleanup then called `os.killpg(pid, SIGTERM)`, got `EPERM`, and only `ProcessLookupError` was suppressed (`codex_transport.stop_process`), so the cleanup error replaced the timeout |

## Contract

1. **Cleanup never masks the failure.** `stop_process` signals the process group; on `ProcessLookupError` or `PermissionError` it falls back to signalling the child itself and ignores the same two errors. The wait that follows is unchanged.
2. **One retry for a request that ran nothing.** A turn whose reason is `internal_error` is recoverable only when **all** hold: `execution_health == "failed"` (which the Executor sets only when the game state is also unchanged), no tool call was recorded, no observed outcome exists, and the existing guards pass (no state change, no dice, no event, pending checks and Luck as before). A failure after any tool call stays a fallback, because that state is not ours to repeat.
3. **Only when time remains.** The retry runs under the same turn deadline (`LLM_TURN_DEADLINE_SECONDS`, 180 s). It is skipped when less than `TURN_RETRY_MIN_REMAINING_SECONDS` (default 45) is left; a retry that cannot finish only makes the player wait longer for the same failure. A 120 s Codex timeout therefore usually does not retry (60 s left, 45 s needed), a 52 s failure does.
4. **Same switch, same cap.** `TURN_FALLBACK_RECOVERY_ENABLED` turns it off; the existing "one retry per turn, never repeated" cap applies. The `turn.fallback` row records `recovery_attempted` and `recovery_result` as for any other recovery.

## Finding for the tool budget (not changed here)

`CODEX_TIMEOUT=120` is a **whole-turn** deadline, set once in `run_conversation`, not a per-request timeout; each Codex request takes about 25–30 s. `MAX_TOOLS_PER_TURN=4` and `MAX_TOOL_ITERATIONS=5` (the last iteration has no tools) force a no-tool final answer after four lookups, and the Executor then returns `model_incomplete` → `executor_no_action`. A turn that spends four slots on `search_scenario` has none left for the acting tool.

Proposal for a later change, needing a decision: reserve the last tool slot and the last iteration for tools that change the game (everything not in `READ_ONLY_TOOL_NAMES`), so lookups cannot starve the action. Not implemented because it changes how many lookups a turn may do and should be measured first.

## Tests

- `tests/test_codex_transport.py`: `EPERM` on the group falls back to the child; both errors gone is ignored; a timeout survives `Process.close()` when cleanup is denied.
- `tests/test_turn_fallback.py`: a request that failed before any tool runs again and can recover; a failed retry is not repeated; a failure after a tool call, an observed outcome, `partial` / `recovery_required` health, a state change or an event never runs again; too little deadline left means no retry; the recovery switch applies.
