# One home for a turn's locks, an order test, and a held-too-long report

[繁體中文](turn_scope_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `e4c6044`.

## Problem

A conversation has four locks a turn can hold: the Keeper priority gate, the conversation lock, the Keeper turn lock and the narration lock. A turn that hands off releases the first two before taking narration, so no turn ever waits for a lock that belongs earlier in that order. The order was kept by documentation: `router.py` carried ten copies of `async with _conversation_lock_with_notice(...)` and 130 lines of queue-notice and hand-off machinery, and the failure it guards against is a channel that deadlocks until the bot restarts. See `docs/architecture/main_v2_architecture_review.md` (F5, F16).

## Constraint: the player's turn does not change

Nothing about who waits for what, for how long, or what a queued player is told changes. The machinery moved as written; the queue notice, its delays and its text are the same.

## Changes

- **`app/commands/turn_scope.py`** holds what `router` had: `conversation_turn` (was `_conversation_lock_with_notice`), `keeper_turn` (was `_keeper_priority_gate_and_lock_with_notice`), the queue notice and its constants, `run_post_turn_hook`. Both context managers still yield a `locks.TurnHandoff`. The router imports it and no longer holds the machinery. Tests that reached the moved names through `router` use `turn_scope`.
- **The router's remaining direct uses of a lock are listed.** `tests/test_architecture_turn_scope.py` pins them (a sudo act narrating, the sudo path's gate and lock, the Help-revision check around long scenario operations, joining the mutation phase through `TurnHandoff.mutation_phase_lock`), each with its reason. A new route that takes a lock itself fails the test until it goes through `turn_scope` or is added with a reason.
- **`tests/test_lock_order.py` records real acquisitions** (the gate, the conversation lock, the Keeper turn lock and the narration lock are replaced by recording subclasses) and fails when a task takes a lock while holding one that belongs after it: gate, then conversation, then Keeper turn, then narration. It drives the ordinary turn with and without a hand-off, the priority-gate path, a command route that narrates and a sudo-style path, several turns at once. A reversed-order control proves the checker can fail.
- **A held-too-long report.** A `TurnHandoff` arms a timer when its turn takes the lock; if the turn still holds any lock after `LOCK_HELD_WARNING_SECONDS` (default `LLM_TURN_DEADLINE_SECONDS` + 60, so 240 s; `0` turns it off) it logs `lock.held_too_long` with the turn id (the router names it on the handoff), the phase (`mutation` or `narration`) and how long. When the turn finally releases, `lock.released_after_warning` says how long it held. **It never releases anything**: a forced release would turn a stuck turn into two turns changing the state at once, which is worse than a stuck channel that someone can now see and name.

## F16: a timing test that no longer depends on the clock

`test_scenario_and_memory_rag_are_gathered_and_one_failure_is_isolated` asserted `elapsed < 0.15` for two 0.08 s searches that must overlap, and failed now and then on a loaded machine. Each search now waits for the other to have started and the test asserts both saw it; a sequential implementation fails it, a slow machine does not.

## Verification

`ruff check .`, `mypy app` and the full `pytest` pass. The watchdog tests use a short threshold only to see the report fire, and a long one to see it stay quiet, so a loaded machine can delay a report but not invent one.
