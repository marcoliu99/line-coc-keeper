# Spec: preserve resolved check outcomes across turns

## Changeset tracking

- **main_v2 start**: `origin/main_v2:f2c37d5`
- **implementation**: `bug/resolved-check-outcome-context:b7000773`
- **verification**: full pytest, Ruff, mypy, and compileall pass

## Problem

The 2026-09-25 basement-stairs sequence has an inconsistent follow-up. The
Keeper's resolved DEX-failure turn narrated a fall and a 5 HP loss, and the
following state inquiry reported HP as 7/12. On the next turn, however, the
Supervisor described only that the current message required no mechanic and
the answer then claimed the earlier damage had no mechanical basis. The
current turn's lack of a check or mutation was treated as if it disproved the
previous turn's resolved outcome.

The current logs establish that the check follow-up invoked `roll_dice` and
`adjust_character` and that the subsequent status response read HP as 7/12;
they do not include the mutation arguments or return values. The system
therefore needs to retain a structured, authoritative record of the resolved
check and its committed state effects, instead of asking a later Narrator to
reconstruct them from current-turn tool facts or prose history.

## Goal and scope

Persist the outcome of resolved player checks across turns, including any
state changes made while resolving/narrating that check. Make that outcome and
the current character values available to later gameplay/status responses.
Ensure a later response can distinguish:

- the current turn had no mechanic action;
- the latest check was resolved with a particular roll and outcome; and
- the character's current persisted HP/SAN/MP/Luck values.

Treat committed character state as authoritative for current values, and the
resolved event as authoritative for what happened during that check. A later
answer must not infer “no prior damage” merely because the current turn had no
`adjust_character` call.

## Non-goals

- Do not retroactively edit prior campaign state or the saved HP value based
  only on this log excerpt.
- Do not change CoC dice, pushed-roll, fall-damage, or Luck-spending rules.
- Do not let Narrator invent side effects or overwrite character values.
- Do not persist arbitrary unbounded conversation prose as mechanic history.

## Data and flow design

Add a bounded per-conversation list of structured resolved-check events to
`GroupState`, backward compatible when loading older snapshots. Each event
should contain a stable event/check identifier, timeline identifier, owner
and investigator, check skill/value, raw roll, difficulty and final outcome,
resolution time, and a compact list of actual state effects. State effects
must record field, before, after, and delta based on committed state/tool
results; omit an effect when no corresponding mutation succeeded. Keep only a
small recent window (proposed: 20 events) and clear it when starting a new
game/timeline.

At the shared check-finalization boundary, record the deterministic check
result and observe the authoritative state before and after the Keeper's
resolution phase. Record only successful persisted changes, including
changes made by tools in that phase. Preserve the existing immediate save and
locking behavior; event append must be atomic with the final state snapshot
where practical and must be timeline guarded to avoid recording stale work.

When a later gameplay response asks about a character's status, recent check,
damage, or Luck adjustment, provide the latest relevant structured event(s)
and current persisted character sheet values to the Executor/Narrator. Label
the event as historical and current-turn mechanic results separately. Keep
other players' private character values scoped to the appropriate owner.

## Testing plan

- Old serialized states load with an empty event list; new event data round
  trips and remains bounded.
- A resolved failed check with a successful HP mutation records exact before,
  after, and delta values; a check without a mutation records no damage effect.
- A failed mutation does not appear as a committed state effect.
- A subsequent no-mechanic status inquiry receives the historical check event
  separately from current-turn facts and reports the current persisted value.
- Events from another timeline are ignored/cleared; owner scoping is preserved.
- The basement-stairs regression ensures a later Luck/HP question does not
  deny or fabricate the previous check's recorded effects.
- Run focused tests, full pytest, Ruff, mypy, and compileall.

## Tradeoffs and open question

Comparing authoritative state around the Keeper resolution phase captures
committed effects but may include unrelated concurrent mutations unless the
existing conversation/state locks fully serialize that phase. Prefer explicit
mutation receipts from the tool gateway if they can be collected without
changing persistence semantics; otherwise keep the before/after window inside
the existing serialized finalization boundary. Confirm the bounded history
size and whether non-check mutations should eventually share this event model.
