# A fallback turn that only looked things up gets its one retry

[繁體中文](fallback_recovery_after_lookup_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `0de529b`.

## Problem

A turn that ends in a generic reply (`executor_no_action`, `unsupported_action`, `no_scenario_evidence`, `invalid_tool_plan`) is meant to get one more search and one more Executor decision before the player sees the blocker (`supervisor._recover_blocked_turn`). The retry is safe only when the first attempt left the game untouched, and `turn_fallback.recoverable()` checks exactly that.

In a Dead Boarder 200-turn run (`1fd3ccb`, 22 `turn.fallback` events with their tool lists), the retry almost never ran:

| First attempt | Events | Retry ran |
|---|---:|---:|
| made no tool call | 4 | 4 |
| called at least one tool (18 of them only `search_scenario` or a write plus searches) | 18 | 1 |

The condition `not result.observed_outcomes` was the cause. Every tool call, including the read-only `search_scenario`, appends an `ObservedOutcome` (`tool_gateway`), so a turn that searched the scenario once counted as one that had changed something. The function's own docstring says its tools "may only have looked things up"; the code did not allow that. The existing tests passed because their results never carried an observed outcome.

## Change

`recoverable()` now ignores observed outcomes of read-only tools (`registry.READ_ONLY_TOOL_NAMES`: `search_scenario`, `search_memory`, `get_character_sheet`, ...). An outcome of any other tool still makes the turn unrecoverable. Everything else that guarded the retry is unchanged and still decides on its own:

- no gameplay state change (`state_changed`), no dice rolled (`dice_rolled`, which covers `roll_dice` and the damage rolls although they are listed as read-only), nothing resolved;
- the pending checks and Luck decisions are exactly as they were;
- no game events (inventory changes produce them);
- one retry at most, at most one extra search, and a player-visible reason when it still fails.

The registry import is inside the function because the tool registry imports modules that import `turn_fallback`.

## Effect and limits

- More fallback turns get a second Executor decision with the recovery search result. That costs another Executor run on a turn that was going to end in a generic reply anyway (the Executor median is about 30 s). The turn-wide deadline (`LLM_TURN_DEADLINE_SECONDS`) still applies.
- How many of those turns the retry rescues is not known: in the same run the five retries that did run recovered two. That is too few to estimate a rate; measure it with `recovery_result` in `turn.fallback` (or `scripts/summarize_turn_log.py`) on the next run.
- The retry cannot help a turn the model fails on for the same reason twice; it does not change the prompt, the tool budget or the deadline.

## Verification

`tests/test_turn_fallback.py`: a turn that only searched is run again and logged `recovery_attempted=True, recovery_result="recovered"` (this test fails on the previous code); a turn whose outcomes include `record_clue`, `add_carried_item`, `remove_carried_item`, `skill_check` or `adjust_character` is never run again. The earlier cases (state changed, dice rolled, resolved, events, pending) still hold. `ruff check .` and the full `pytest` pass.
