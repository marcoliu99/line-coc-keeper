# A turn that fails after only look-ups is run once more, and an unfinished turn logs why

[繁體中文](retry_after_lookups_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c46e341`. Found in the 2026-10-12 soak reruns (Camp Sunny rerun7).

## Problem

Turn 81 of Camp Sunny: a player shot at Billy and was told 「系統發生內部錯誤」. The Executor had called `get_enemy_stat_block` (a look-up), then its decision was rejected as invalid twice (`codex.decision.rejected invalid_schema`, then `codex_invalid_decision`), so the turn fell back as `internal_error` with `recovery_attempted: false`.

`turn_fallback.recoverable` already retries a failed turn once when running the Executor again cannot apply anything twice, but for `internal_error` it only allowed a request that failed before **any** tool ran. A look-up leaves the game as it was, so it is no reason to give up the retry.

## Change

- `_failed_before_any_tool` becomes `_failed_without_a_change`: an `internal_error` whose `execution_health` is `failed` or `partial` can be retried. `recoverable` keeps every other guard (no state change, no dice, pending checks and Luck as found, no events) and now also requires every `tool_calls` name, not only every observed outcome, to be a read-only tool, so a tool that ran and changed something still stops the retry. `recovery_required` (state changed) is never retried.
- `executor.resolution` logs the model's own `reason` (cut to 240 characters) when the disposition is `incomplete` or `blocked`. In the reruns these turns showed no way to tell why the Executor gave up (Camp Sunny turns 67, 76 and 80, Scritch-Scratch turns 57, 69, 81 and 93); the next soak will.

## Not changed

- A turn the Executor ends `blocked` after the scenario search (Scritch-Scratch asked four times for a library the village does not have) still answers with the fixed 「目前無法繼續」 text. The same request had been answered in the story four times before; the fixed text is the design for a turn the Keeper will not invent an answer to.

## Verification

`tests/test_turn_fallback.py`: a failure after only look-ups is run once more and recorded as recovered; a failure after a tool that changed something (an item, dice, state, events, a non-read-only tool call) is never run again.
