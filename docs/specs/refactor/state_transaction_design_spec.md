# Game-state transaction

[繁體中文](state_transaction_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented** (phase 1 of the [four-phase architecture refactor](architecture_refactor_phases_1_4_design_spec.md)). Based on `main_v2` at `2affd06` (2026-10-04).

Before this change a game-state write could reach SQLite by six different routes: Keeper tools (`_mutate_and_save_state`), the turn and maintenance commits in `keeper.py`, command handlers (`load_state` → edit → `save_state`), the legacy check and upload paths, pending-button claims, and checkpoint rollback. Only the first reloaded the latest state under a lock; the rest wrote a snapshot and relied on the revision check to fail. None could recognise a repeated action. The goal is one write boundary that reloads, validates and commits atomically, so retries, double clicks and background writers cannot lose or repeat a change. This adds no model call.

## Current evidence

- `repositories/group_state.save_state` already serialised on a per-conversation `RLock` plus `BEGIN IMMEDIATE` and refused a stale `state_revision`, but a handler that loaded outside the lock turned a harmless race into a `StateRevisionConflict` for the player.
- `newgame` reset the revision to 1 on a new timeline. A snapshot loaded in the previous game at revision 1 therefore passed the revision check and could overwrite the new game.
- A rollback, a maintenance trim and a turn commit each carried their own copy of the "reload, compare timeline, write" sequence.
- `combat._checkpoint_before_combat` opened its own `BEGIN IMMEDIATE` transaction from inside a Keeper tool mutation. Under a shared boundary that nests two write transactions on one database.

## Contract

`app/repositories/state_transaction.py` is the only module that writes a `GroupState` row.

```text
mutate(conversation_id, mutation, expected_timeline, action_id, request_fingerprint, expected_revision)
  lock the conversation  ->  BEGIN IMMEDIATE  ->  read latest row
  -> timeline  -> action ledger  -> revision  -> mutation(ctx)  -> local invariants
  -> write state + character mirrors + staged events + action result  -> COMMIT
```

1. **Outcomes are values.** `applied`, `duplicate`, `awaiting_input` (committed, a player must act next), `stale_timeline`, `conflict` and `rejected`. A domain error raised by the mutation still propagates after rollback.
2. **Order of checks.** A stale timeline is refused first, so a button from before a reset or restore never replays. Then the action ledger: the same timeline, action id and payload fingerprint returns the stored result (`duplicate`, with the original outcome, events and result payload) *before* any revision comparison; the same id with a different fingerprint is `conflict`. Then `expected_revision`, for actions computed from a snapshot.
3. **Two write styles.** `mutate` applies a delta to the latest state and is the default. `commit_snapshot` is the strict path for validated-snapshot actions that cannot be re-expressed as a delta: it writes only if the stored revision and the timeline the snapshot was *loaded* under (`GroupState.loaded_timeline_id`, in memory only) still match, and otherwise raises `StateRevisionConflict` / `StaleTimelineError`. A flow that deliberately starts a new timeline (scenario upload, `/coc scenario use`) is judged against the timeline it read.
4. **Atomic with the state.** The mutation may write other tables through `ctx.conn` (manual pregen assets, correction archive rows, memory chunks). Those writes, the state row, the character mirrors, the staged events and the ledger row commit together or not at all. `state_transaction.ambient(conversation_id)` gives helpers the open transaction; the pre-combat checkpoint uses it. A second `mutate`, `db.transaction()` or `db.set_json()` inside a mutation raises immediately instead of waiting on SQLite's own lock.
5. **Action ledger.** Table `state_actions`, keyed by conversation, timeline and action id, written in the same transaction. A mutation that chose to save nothing (`ctx.skip_save()`) leaves no ledger row, so a later retry is evaluated afresh. Retention is the newest 1000 rows per conversation and 30 days, pruned every 25 revisions; a terminal domain state (consumed pending check, existing event id) still prevents re-settlement after a row is pruned.
6. **Local invariants.** Only fields the mutation changed are judged: HP/MP/SAN/Luck are whole numbers within `0..max` where a max exists, ammo is within `0..ammo_max`, pending entries are mappings. An old save with an odd value is never rejected for something the action did not touch. A violation is `rejected` and nothing is written.
7. **Snapshots.** After a commit the caller's snapshot is synchronised from the committed state. `sync_snapshot` refuses to move a snapshot to an older revision of the same timeline, so a late refresh cannot roll the shared snapshot back; a different timeline always wins.
8. **Failures.** A failure while saving the state, mirrors, events or ledger rolls everything back and emits `state.save.failed`. A failure after commit (cache refresh, Discord delivery, narration) never re-runs the mutation; a retry with the same action id reuses the stored result.

Action ids come from a trusted owner of the operation, never from text similarity: the turn id owned by `run_turn` plus a digest of the committed entries (`turn:<id>:<digest>`), and the stable event id of a resolved check (`check-event:<event_id>`). Phase 2 and 3 add check and combat action ids on the same ledger.

## Writers moved onto the boundary

| Former route | Now |
| --- | --- |
| `keeper._mutate_and_save_state` / `mutate_tool_state` (all Keeper tools) | thin adapter over `state_transaction.run_snapshot` |
| `keeper._commit_turn_result`, `_commit_kp_ooc_turn_result`, `_persist_memory_maintenance_state`, `_ensure_turn_timeline`, resolved-check event persistence | `commit_for_snapshot` / `mutate` with ids and `ctx.conn` |
| `checkpoints.rollback` | one `mutate`: pre-rollback checkpoint, restored state and mirrors commit together |
| `/coc` character, combat and map handlers | validate and change the latest state inside `transact` |
| `/coc newgame` | `mutate` with `ctx.replace_state`, re-checking the unsettled-combat guard on the latest state |
| pending-button claim and release | delta on the latest state |
| legacy check/upload/map resolvers, system handler, correction services | `commit_snapshot` (strict) until phase 2–4 re-home them |

`tests/test_architecture_state_writes.py` parses every module under `app/` and `scripts/`, follows import aliases and rejects any reference to `save_state`, `write_state_tx` or the private variants, any `db.set_json*` / `db.delete_json*` call aimed at `group_states` or `characters` (or with a table it cannot resolve) and raw `INSERT/UPDATE/DELETE` SQL against them. The only exemptions are the two storage modules and a short, reasoned list of operator scripts.

## Compatibility

No existing save needs a migration. The new table is created with `CREATE TABLE IF NOT EXISTS` and older code ignores it; `loaded_timeline_id` is never serialised. Tool names, outputs, Discord custom ids and command text are unchanged. Reverting the code is safe: the ledger rows are simply unread.

## Deployment note

The in-process lock orders callers inside one process only. Cross-process safety rests on `BEGIN IMMEDIATE` and the stored revision. SQLite has one writer at a time for the whole database, so writes for different conversations serialise for the short duration of a transaction; the process-level locks are per conversation and do not add a global lock.

## Limits

- Tool calls themselves carry no action id: a tool mutation's return value is a Python object, so it cannot be replayed from the ledger. Tool-level replay protection stays in the domain receipts (combat roll receipts, consequence receipts) and is revisited in phases 2 and 3.
- With both `LOG_ENABLED` and `LOG_TEXT_ENABLED` off the request context carries no turn id; `run_turn` then generates one per run, which protects its own retries but not a re-delivery of the same Discord event.

## Verification

`tests/test_state_transaction.py` (scenarios S1–S10 plus invariants, nesting, rollback and pruning, including real concurrent processes and a hard process exit mid-transaction), `tests/test_state_transaction_adapters.py` (turn commit, tools, handlers, pending buttons, rollback) and `tests/test_architecture_state_writes.py`. Result report: [phase 1](../../refactor/phase1-result.md).
