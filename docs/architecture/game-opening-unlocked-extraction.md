# Game Opening Phase 2A — Unlocked Extraction

Implementation baseline: `origin/main_v2` at `066667812289e2128322cd17bd49c5d0646300dd`, including stable active-source identity from PR #183. The [approved design spec](../specs/refactor/game_opening_unlocked_extraction_design_spec.md) defines the intended races and policy.

## Boundary and lock scope

Router supplies Game Opening with an async mutation scope that acquires the existing conversation lock, keeps queue notices, and invokes the existing post-turn button hook. Game Opening owns the phase ordering. The handler supplies transport-neutral readiness and completion callbacks; it does not decide when to unlock, revalidate, or commit. Direct non-Router callers use the same conversation lock without Router's delivery hooks. `ControlCompletion` accumulates claims from both scopes for one final, stale-checked publication; an earlier claim cannot be overwritten by the apply hook.

```text
PREPARE: Router conversation lock
  latest admission → independent healing commit → immutable roster/token → roster reply
  Router hook → unlock
WORK: no conversation/state/Keeper/narration lock
  read-only scenario_intro extraction in a worker thread
APPLY: reacquire Router conversation lock
  latest identity and admission → scripted SQLite commit OR locked fallback pipeline
  completion reply/delivery → Router button hook → unlock
```

A legacy state without `active_scenario_source_hash` retains the Phase 1 coarse lock throughout extraction. Fallback Narrator still runs under the second conversation scope and its existing Keeper/narration locks. Phase 2A reduces extraction lock hold only; it makes no fallback latency claim. `opening.extract` records extraction duration and `opening.extract.stale_discarded` records a low-cardinality reason, without scenario text, title, names, or hash.

## Extraction token and final admission

The immutable token carries the timeline, persisted full-source hash, library ID, variant, chapter/window metadata, SHA-256 of the exact persisted `scenario_text` passed to the extractor, and sorted owner→active character bindings. The full-source hash catches same-ID repair even when the current chapter text is unchanged. The context digest catches chapter/input changes under the same full-source hash. Character identity excludes HP, SAN, skills, persona, and other unrelated values. A participant/binding change discards extraction and requests retry; latest values are used for check candidates if membership is unchanged.

At APPLY, Game Opening reloads latest state. It discards work for an already-started game, replaced timeline, changed source/context, or changed participant set. It then repeats the original admission rules: active scenario/text, characters, pending pregen Luck, and combat replacement protection. The path-specific generic pending-check and Luck-decision behavior remains in `register_many` only for a scripted group check. An unrelated state revision alone does not invalidate extraction.

## Authoritative transaction guards

Scripted opening uses one `state_transaction.mutate` transaction. Under `BEGIN IMMEDIATE`, it rechecks timeline, source hash, extraction context, participant binding and admission, then registers group checks, initializes unknown skill values, writes the instruction and narrative history entries, and sets `game_started`. A blocker skips the save; no partial start is committed.

Fallback revalidates before Narrator, then reuses `supervisor.run_turn(turn_kind="opening_fallback")` and Keeper `_commit_turn_result(start_game=True)`. Keeper passes a pure `latest_state_guard` to `commit_for_snapshot`. That guard checks current source hash, context, participants and admission **inside the final SQLite transaction, before action-ledger replay**. The timeline check is in the same transaction. A rejected final commit cannot deliver stale public/private/image output. No second fallback transaction was introduced.

## Visible behavior and preserved semantics

The approved intentional UX change is that another command may finish during extraction. Its public reply can appear between the readiness roster and eventual opening narration. Within a start, roster still precedes opening and optional check instruction; the apply lock keeps opening/check instruction together. No output scheduler or reservation was added.

Healing remains an independent pre-opening commit and survives a later failure/cancellation. A cancelled extraction worker may finish read-only work but cannot apply or deliver it. Concurrent starts may both pay extraction cost; the latest-state checks and final SQLite transaction allow only one authoritative start. Existing pending-check/Luck differences, history authority, fallback failure retry, and button claim after pending-check commit remain unchanged.

## Tests and remaining risks

`tests/test_game_opening_unlocked_extraction.py` covers design races RACE-01 through RACE-16 with event barriers and temporary SQLite, including independent SQLite writers at scripted and fallback boundaries. `tests/test_game_opening_characterization.py` continues to cover legacy coarse locking, healing, history, cancellation, delivery and button behavior. Full pytest, ruff, mypy and diff check are required before merge.

A committed start may still fail Discord delivery; retry sees the game already started. The filesystem scenario library and SQLite active-state commit remain non-atomic as before. Fallback narration still holds the conversation lock; unlocking that path requires a separate protocol and PR.
