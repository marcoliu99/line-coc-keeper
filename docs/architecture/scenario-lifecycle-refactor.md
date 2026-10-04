# Scenario Lifecycle Refactor

## Before

`scenario_ingestion.py` parsed a source and also decided whether to activate or
store `pending_pdf_upload`. It reset timeline state, installed pregenerated
investigators, committed SQLite, and refreshed images. `system.py` separately
implemented `scenario use`, reparse claim/recovery, and multipart consumption.
`uploads.py` owned choice locking and multipart state writes. PDF and Markdown
had separate admission checks around the same pending decision.

## Problems

Changing an activation rule required checking upload, command, pending-choice,
and reparse paths. Callers knew the persisted pending dictionary, state reset
order, lock ownership, and commit-before-image rule. In particular, a reparse
without a Help revision could apply its parsed result after a concurrent
timeline replacement.

## New Boundary and Public Interface

`app/services/scenario_lifecycle.py` owns lifecycle decisions. Its intention
operations are `submit_published_scenario`, `resolve_pending_submission`,
`reparse_pending_scenario`, `cancel_pending_reparse`, and
`activate_existing_scenario`. `stage_similar_pdf` records a PDF similarity
candidate; `submit_merged_pdf` consumes multipart parts only after the merged
submission succeeds. `admit_submission` is an early read for immediate upload
feedback; final admission is repeated against the latest state during the
authoritative transition. These functions return a small `LifecycleResult`;
transport code renders the existing Chinese messages.

The interface takes a conversation ID, source/library identity, and command
intent. It does not take a SQLite connection, a mutable `GroupState`, a pending
dictionary, or a caller-owned lock.

## Internal Responsibilities

`scenario_ingestion.py` remains the source adapter: PDF validation, similarity
preview, OCR, map/image extraction, and library publication; Markdown filename,
UTF-8, page-marker and title rules, with no PDF extraction. Multipart receive,
staging, and merge construction also live there. The lifecycle module owns
pending state, latest-state admission, distinct transition policies, timeline
reset, pregenerated investigator installation, SQLite commit, and postcommit
image refresh. `scenario_activation.py` and `manual_pregens.py` remain internal
collaborators rather than duplicated implementations.

## Transaction Boundary

For activation: publish the parsed source to the file library; acquire the
conversation lock and reload state; check admission; apply the selected
transition; install manual/pregenerated investigator cards in the same
`state_transaction.commit_snapshot` SQLite transaction as `GroupState`; then
clear/copy derived images through `scenario_activation.commit_and_refresh`.
The image operation never runs on commit failure. An image failure leaves the
scenario committed and returns the existing warning; the cache may be empty.
Multipart bytes are consumed only after the submission's authoritative
transition succeeds. The persisted field remains `pending_pdf_upload` even for
Markdown; no serialized format or schema was migrated.

## Reparse Concurrency Protocol

Under the session lock, reload and authorize, verify the pending candidate,
read its staged bytes, remove the pending reference, and commit the claim.
Release the lock for expensive PDF parsing. Reacquire through the normal
submission transition and require the claimed timeline and claim revision
before applying the result. On failure or cancellation, reload state
under lock and restore the candidate only when its timeline still matches and
no newer pending candidate exists. Restoration writes the latest snapshot, so
unrelated concurrent fields are retained. A regression test pins the case
where reparse has no Help revision and the timeline changes during parsing.

## Preserved Behavior

- First PDF or Markdown upload activates; an upload over an existing scenario
  waits for `new`/`fix` choice. PDF similarity stages raw PDF bytes first.
- New upload rotates the timeline and clears pending checks/Luck, deterministic
  results, resolved check events, consequence origins/receipts, KP private log,
  map locations/facing, and game-start status. It retains public history and
  live investigators.
- Repair keeps ongoing progress and reconciles the investigator candidate pool.
- `scenario use` rotates the timeline and clears checks/Luck/results/events,
  while retaining consequence origins/receipts, public history, live
  investigators, and map locations still valid in the selected scenario.
- PDF and Markdown differ only in source preparation and their existing
  presentation text. There is one pending/activation transaction policy.
- Combat replacement guards and ADR-0002 narrative authority remain in their
  existing boundaries; combat semantics were not changed.

## Dependency Direction

```text
Discord command/upload transport
  -> PDF/Markdown preparation in scenario_ingestion
  -> scenario_lifecycle
  -> scenario_activation, state_transaction, library, investigator state
```

Commands also call lifecycle directly for pending resolution, reparse, cancel,
and library selection. Lifecycle does not import command handlers or Discord.
Storage and domain modules do not import Discord. Source parsers do not select
reset policy or control activation.

## Test Evidence

`test_scenario_lifecycle_characterization.py` observes persisted scenario,
timeline, checks, Luck, consequence receipts, investigator pool, history,
maps, images, and multipart references across first upload, pending new/fix,
scenario use, and failure. `test_help_reparse_recovery.py` exercises unlocked
parsing, newer candidates, timeline replacement, and exception/cancellation.
`test_scenario_activation.py` pins commit/image failure order. See the PR's
verification section for complete command results.

## Remaining Risks and Follow-up Candidates

File-library publication precedes SQLite activation and is not atomic with
it; a later commit failure can leave an orphan library entry. Postcommit image
failure leaves the authoritative scenario active with an empty or incomplete
derived cache. These existing behaviors were deliberately retained. The
`pending_pdf_upload` field name is misleading for Markdown; renaming it would
require a separate migration. The early upload admission is only for timely
UX and can become stale during parsing; authoritative admission is under the
lifecycle lock.

## Candidate #2 — Game Opening

**Recommend as a separate investigation and likely next module**, with an
`open_game(conversation_id, actor)` intention seam. The invariant joins
readiness, investigator/Luck repair, opening-check registration, authoritative
history, and `game_started`. Today `system.py` commits repaired character
state, extracts the intro without the state lock, then reacquires the lock to
register checks/history and commit again. A fallback narration path has a
separate narration lock and commit path. This is not one atomic transaction:
failure after repair can leave repaired investigators without an opening; a
state change during extraction must be revalidated. A useful module would own
those boundaries and return opening text/check notices. Merely moving the
branch to a class would leave the public interface and race unchanged.
`scenario_intro.py` should remain extraction, `check_lifecycle.py` check
admission, and `history_authority.py` provenance; their responsibilities do
not grant narrative text authority to create canonical facts (ADR-0002).

## Candidate #3 — Combat Obligations

**Investigate further before extracting.** Committed obligations live on
`GroupState`, working obligations in combat actions/resources, and settlement
projects future obligations to committed state. Rollback and pending-check
receipts must restore the same logical timing. `combat_flow.py`,
`combat_resources.py`, and `services/combat_engine.py` therefore participate
in one combat transaction aggregate. A standalone obligations facade that
forwards calls into these modules would add coupling, not own the invariant.
Any safe extraction would have to change due-check registration, working-copy
updates, settlement projection, rollback restoration, and their call sites
together. ADR-0003 requires whole-battle provisional resources and only
publishing durable effects at settlement; this refactor did not touch them.
