# Game Opening Refactor

Implementation baseline: `origin/main_v2` at
`aa79f22c1db67c00992ea9bc878eea8836788b1f`. The approved design used
`b0e875c`; intervening Turn Payload Contract, Correction Lifecycle, and
Scenario Source Store changes were integrated before final validation and did
not change the opening path's admission or lock scope.

## Before

`system.py` owned the `/coc start` readiness checks, investigator healing,
scripted opening extraction, group check registration, opening history, final
start commit, and fallback routing. The Router already serialized the entire
command with its conversation lock and claimed pending check buttons afterward.
The [design spec](../specs/refactor/game_opening_module_design_spec_zh.md)
records the prior flow and its failure matrix.

## New Boundary and Public Interface

`app/services/game_opening.py` exposes `open_game(conversation_id, actor_id,
on_readiness=...) -> OpeningResult`. It owns readiness admission, the healing
transition, extraction choice, scripted check and history transition, and
fallback orchestration. The callback receives an immutable `OpeningReadiness`
snapshot for the early roster reply; the result contains only presentation
metadata and fallback delivery items. Neither accepts or returns a mutable
`GroupState`, transaction connection, or pending check dictionary.

`system.py` renders existing messages and sends the roster, opening, check
instruction, and fallback delivery. It does not perform opening state writes.
Game Opening does not import Discord, command handlers, Router, or button code.

## Lock Ownership

```text
Router conversation lock
    -> Game Opening orchestration
         -> short state-lock + SQLite transactions
         -> existing narrating_turn locks for fallback
    -> Router post-turn button claim
```

Router's outer lock remains held during roster delivery, slow opening
extraction, fallback narration, and final delivery. Game Opening does not
reacquire that non-reentrant conversation lock. Its synchronous state lock is
only held for short read/mutate/commit sections, never for a provider call or
an async callback. This PR makes no latency or lock scope change.

## Healing Semantics

The service reloads the state under the state lock, runs `heal_character` for
each current investigator, and calls `commit_snapshot` only when notes exist.
This is an independent pre-opening normalization commit. Its revision and
character repairs survive a later extraction exception, check blocker,
fallback failure, cancellation, or failed final start commit. The roster
snapshot is built after this commit and delivered before extraction.

## Scripted Opening Transaction

When `scenario_intro.extract_opening_narration` finds text, the service reloads
state under the state lock. For an opening check it computes each investigator's
candidate without installing unknown skills, then calls
`check_lifecycle.register_many`. A blocker leaves all new checks, skills,
history, and `game_started` uncommitted. After successful group registration,
unknown skills are installed. The checks, two annotated history entries, and
`game_started=True` share one `state_transaction.commit_snapshot`. Revision and
timeline comparison reject a stale snapshot without a partial start.

## Fallback Opening Pipeline

When extraction returns `found=False`, the service uses the existing
`locks.narrating_turn`, latest-state refresh, and
`supervisor.run_turn(turn_kind="opening_fallback")`. The supervisor and Keeper
continue to own narrator/Guard work, action ledger and timeline checks,
`_commit_turn_result(start_game=True)`, log persistence, and response chain
invalidation. Game Opening does not duplicate that transaction. A narrator
failure remains retryable and does not set `game_started` or write an opening
log; any earlier healing and roster delivery remain.

## History Authority

The scripted instruction remains `record_kind="opening_instruction"` and
`authority="claim"`. The assistant opening remains a narrative presentation
record. Both share the current timeline and turn ID. The refactor preserves
ADR-0002: opening prose does not establish canonical world facts.

## Observable Output Ordering

Scripted success: readiness roster, opening narration, optional check
instruction. Scripted blocker: readiness roster, blocker. Fallback: readiness
roster, then existing public/private/image delivery. Scripted pending checks
commit before Router's post-turn hook claims buttons. Discord publishes those
claimed buttons after the conversation lock is released. Button handling stays
outside Game Opening.

## Failure and Cancellation Semantics

Before healing commit, failure leaves no opening mutation. After healing
commit, that normalization remains even if roster delivery, extraction,
scripted commit, or fallback fails. Cancellation during `to_thread` extraction
stops the command while the worker may finish its read-only work; it cannot
commit an opening by itself. Cancellation during fallback generation leaves
the game retryable unless the existing turn pipeline has already committed.
Once the scripted or fallback start commits, Discord delivery failure cannot
roll back the authoritative start. The full stage-by-stage matrix is in the
design spec.

## Preserved Differences

- An existing generic pending check does not block a scripted opening with
  no opening check.
- An existing pending check or pending Luck decision may block a scripted
  opening that registers a group check; registration remains all-or-nothing.
- Fallback has no new generic pending-check admission gate.
- Malformed or absent scripted opening data retains `scenario_intro`'s current
  fallback contract; no provider error taxonomy changed.

## Dependency Direction and Test Evidence

```text
Discord / Router -> system presentation -> game_opening
                                         -> character_service, scenario_intro
                                         -> check_lifecycle, history_authority
                                         -> state_transaction, supervisor
```

`tests/test_game_opening_characterization.py` exercises real SQLite state for
admission, healing, group check atomicity, history provenance, fallback retry,
double start, cancellation, conflict, and post-commit button claim. Existing
supervisor and transaction tests cover the fallback turn commit. The full
regression suite and static checks are the merge gate.

## Accepted Existing Risks and Follow-up

The conversation lock is process-local. Another process can change the
scenario/source during slow extraction; the current snapshot CAS does not
prove that extracted prose belongs to the latest source. This refactor does
not claim to solve that existing cross-process stale-source risk. A later
unlocked extraction design needs stable scenario/source identity, timeline
binding, revalidation, character set policy, pending-check/Luck re-admission,
and deterministic cross-process tests. Separately, a committed opening may
not reach Discord if delivery fails. Neither risk is silently changed here.
