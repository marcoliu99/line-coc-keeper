# A handoff naming a check nobody opened is a decision to retry

[繁體中文](handoff_mismatch_retry_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `137f126`. Found in the 2026-10-13 Lightless Beacon random rerun4 (turn 183; the same slip recovered on its own at turn 105).

## Problem

Arthur asked to search the wrecked ships. The Executor looked the scenario up, then answered `await_check` for a Spot Hidden it never started (`check_id` `check-`, nothing pending for anyone). The handoff check failed with `pending_identity_mismatch`, which was classified as a `state_conflict`, so Arthur read 「目前的狀態和這次行動不一致（例如時間線已更新）」, although nothing had changed, and the turn got no second try (`state_conflict` is not recoverable). The next player's identical action went through at once.

## Change

`turn_fallback.classify` reports `invalid_tool_plan` for `pending_identity_mismatch` when every tool the turn ran only looked something up and no check or Luck decision is pending for anyone. Such a turn did nothing to the game, so it gets the one Executor retry that `invalid_tool_plan` already earns, and the wording asks the player to say the action again. A mismatch on a turn that ran a tool able to change the game (a fight opened, a stale replay) or while something is pending stays a `state_conflict`, and `cancellation_not_verified` is unchanged.

## Verification

`tests/test_turn_fallback.py`: a mismatch after only a scenario search, or with no tool at all, is `invalid_tool_plan`; with `initialize_combat` run, or with another owner's check pending, it stays `state_conflict`. The existing code-to-reason table now expects `invalid_tool_plan` for the bare case. The new test fails without the change.
