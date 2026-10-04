# Scenario Lifecycle deep module

Category: `refactor`. Status: **implemented on the refactor branch**. Baseline: fetched `origin/main_v2` at `9478dc2` on 2026-10-05. This spec records behaviour observed before runtime changes; the implementation and its verification are recorded in `docs/architecture/scenario-lifecycle-refactor.md`. No Discord wording, persistence format, OCR rule, Markdown parsing rule, or combat rule is intentionally changed.

## Problem and goal

Scenario submission, pending selection, reparse, switch, and activation currently cross `scenario_ingestion.py`, `system.py`, `uploads.py`, `scenario_activation.py`, the library, and the state transaction. `system.py` still knows pending dictionaries, when to clear them, timeline resets, character-card installation, the authoritative commit, and subsequent image publication. PDF and Markdown repeat the same lifecycle work. The goal is one deep **module**: a small **interface** that expresses the caller's intention, with the ordering and policy in its **implementation**. The **seam** is between command/upload callers and the scenario lifecycle, after source-specific extraction has produced a library candidate. Callers gain **leverage**; rule changes gain **locality**.

## Architecture notes: observed baseline

The following is code-derived and anchored to the current branch. Existing tests fix portions of it, but the listed missing cases need characterization tests before moving runtime code.

| Flow | Observed behaviour | Current owner / evidence |
| --- | --- | --- |
| PDF entry | Upload routing chooses one PDF; multiple PDFs or a `part` name are staged. Direct, imported, and merged PDFs call the same upload function. PDF preview runs before expensive extraction; a similar match stages bytes and saves `pending_scenario_upload`, without activating. | `uploads.py:44-61,118-147`; `system.py:60-129`; `scenario_ingestion.py:236-333` |
| Markdown entry | Only `scenario*.md` enters scenario ingestion. UTF-8 decoding and page-marker/title rules are source-specific. It skips PDF similarity detection, OCR, maps, and image extraction. | `uploads.py:63-77`; `scenario_ingestion.py:455-547` |
| First submission | After library publication, a conversation with no `scenario_text` immediately gets a new timeline and active scenario. The live investigators remain; the unclaimed pregen pool is rebuilt from the new source and manual cards. | `scenario_ingestion.py:63-116,370-427,531-595` |
| Existing scenario | Upload creates `pending_pdf_upload` (the field is also used by Markdown) and waits for `new` or `fix`. It does not expose replacement page images yet. A new choice resets timeline, provider chain, game-start marker, pending checks/Luck and deterministic results, check consequence records, KP OOC log, maps and positions; a correction retains current progress and merges pregens. | `scenario_ingestion.py:63-156,382-425,548-593,634-687` |
| Pending ownership | PDF similarity creates `pending_scenario_upload`; PDF/Markdown submissions create `pending_pdf_upload`; reparse claims/clears the first; cancel removes it; choice resolves/clears the second. The command layer currently manipulates the first and reaches the second through a function that requires its caller's lock. | `scenario_ingestion.py:317-328,394-408,554-569,634-676`; `system.py:500-570`; `uploads.py:150-170` |
| Reparse | Under the conversation lock, the command reloads state, checks authorization/revision/Luck, reads staged bytes, clears pending and commits; it releases the lock during PDF extraction. Success discards staged bytes. Failure or cancellation restores pending only if timeline is unchanged and no newer pending item exists. With an expected revision, a concurrent write prevents applying the parsed result. | `system.py:500-570`; `tests/test_help_reparse_recovery.py` |
| Existing-library switch | `/coc scenario use` requires KP authorization, no pending pregen Luck and no pending uploads. It loads context and approved variant, installs scenario fields, rotates timeline, clears pending checks/Luck, deterministic results and resolved-check events, resets provider chain, keeps valid map locations and live investigators, rebuilds pregens, commits, refreshes images, selects variant and schedules prewarm. Unlike new upload, it does not clear check-consequence origins/receipts or the public log; this difference is preserved pending a separate behavioural decision. | `system.py:575-667`; `scenario_activation.py:16-80` |
| Persistence and images | A library source is published before the conversation state is committed. Manual pregen capture/install shares the SQLite transaction with the state snapshot. Derived page images are cleared/copied **after** the authoritative commit. Commit failure leaves previous state/images; image refresh failure leaves committed state and a warning, possibly with an empty image cache. | `scenario_library.py:222-370`; `scenario_activation.py:39-68`; `tests/test_scenario_activation.py` |
| Concurrency | Similarity detection and source extraction run outside the conversation lock. The pending write reloads latest state under that lock, and `commit_snapshot` checks revision/timeline in SQLite. Pending-choice resolution currently expects its command caller to hold the lock. State transaction also acquires the per-conversation state lock and `BEGIN IMMEDIATE`. | `scenario_ingestion.py:301-328,382-427,548-595`; `uploads.py:162-169`; `state_transaction.py:408-770` |
| Failure stages | Source parse/publish errors occur before pending or activation state is committed (a published but unreferenced library entry can remain if later work fails). Missing pending library entry clears the pending choice and commits that cleanup. Commit failure prevents image refresh. Refresh failure reports warning after commit. Reparse failure conditionally restores staged pending; a newer timeline/pending item is never overwritten. | `scenario_ingestion.py:335-452,634-687`; `system.py:520-570`; `scenario_activation.py:51-68` |

The public `GroupState.log` is not cleared by these transitions. It remains narrative presentation, not a source of canonical facts (ADR-0002). The combat replacement guard executes before relevant system commands or new-scenario application, and must continue to protect provisional combat working state (ADR-0003).

### Current callers' leaked knowledge

`system.py` knows the pending similarity dictionary and its staged-file key, reparse claim/restore transaction order, scenario switch timeline and check resets, map-position preservation, pregen capture/install, commit-before-image order, and variant/prewarm sequencing. `uploads.py` knows the lock needed to resolve a pending choice, and stages PDF parts with its own state write. Both PDF and Markdown ingestion know how to create the same pending choice and activate the first scenario. These are lifecycle implementation facts, not command parsing facts.

## Scope and non-goals

In scope: submit PDF/Markdown candidates, pending similarity and new-or-correction choices, reparse claim/restore/cancel, existing-library selection, source-part consumption after successful merged submission, first activation, investigator/pregen integration, timeline and pending resets, and authoritative commit/image ordering. Existing source parsers and storage stay internal collaborators.

Out of scope: combat, Keeper narration, provider implementation, OCR/layout, Markdown decoding semantics, scenario chapter progression tools, map upload, role-sheet upload, source review/authoring, `/coc newgame`, library deletion, and unrelated command cleanup. Authentication remains at command/upload entry points; the lifecycle module revalidates state-dependent admission after reloading latest state. No persisted schema change or data migration is planned.

## Proposed interface and seam

Prefer a small module-level interface, consistent with existing Python modules, instead of introducing a class solely for indirection. The intended caller actions are:

1. Submit a scenario source or an already prepared source candidate.
2. Resolve a pending new-or-correction choice.
3. Reparse or cancel a staged similar PDF.
4. Activate an existing library scenario.

Exact Python names and compact value types can be finalized after characterization. The interface returns a simple outcome plus metadata required to preserve current user-facing text and timing; command handlers format the text and emit immediate acknowledgement before long extraction as they do today. It must not expose `GroupState`, pending dictionaries, `sqlite3.Connection`, a caller-held lock requirement, or a `prepare → caller commit → activate` sequence. The caller may identify PDF versus Markdown from filename; format-specific decoding, extraction and library publication remain in internal adapters. Two real source formats justify that internal seam. No external port is added for the local SQLite/filesystem dependencies: tests can use local stand-ins or a real temporary SQLite database and library directory.

The lifecycle implementation may be internally composed of source adapters, a coordinator, state-transition functions, and presentation metadata. Its external interface stays small; it does not absorb Discord text formatting, PDF OCR internals, Markdown parser internals, or database implementation.

## Required transition ordering

1. Read current state for early rejection; validate source-specific input and run slow preview/extraction without holding the conversation lock.
2. Publish the immutable/replaceable source in the scenario library, preserving its current file format and manifest. For PDF similarity, stage bytes and persist the similarity decision instead of extracting.
3. Acquire the conversation lock; reload latest state; recheck revision, pending choice, timeline and any state-dependent admission. Do not write an old snapshot over a concurrent turn.
4. For a pending choice, persist only the pending reference/metadata. For activation, apply the current transition policy to the loaded state; capture and install manual pregens inside the same SQLite transaction; commit the state once.
5. Only after a successful commit, refresh derived page images. Report image failure without rolling back the committed scenario. Trigger existing variant notice/prewarm at their present positions.
6. Release the conversation lock before the final response. Reparse must claim and commit the staged item under lock, release it during extraction, then discard or conditionally restore exactly as today.

The implementation should use the existing `state_transaction` contract. Replacing a snapshot commit with a delta mutation is acceptable only when characterized externally visible behaviour and race semantics remain identical; otherwise keep the strict snapshot path and place its use inside the lifecycle module.

## Characterization and verification plan

Before runtime edits, add tests through the existing command/upload paths with temporary SQLite and library storage, observing persisted outcomes rather than private functions. The test seam is the public command/upload behaviour and, after introduction, the lifecycle module interface. Cover:

- PDF first submission, similarity staging, pending new/fix resolution, and no premature image publication.
- Markdown first submission and pending choice, including preserved text-only source format and identical lifecycle state semantics.
- Existing-library activation and switch: timeline, log, investigators, pending checks/Luck, check records, map position, scenario metadata, images and manual cards.
- Reparse success, validation failure, extraction exception/cancellation, concurrent state change, and no resurrection over a newer pending item/timeline.
- Commit failure versus image refresh failure, observing persisted state and image cache.
- Two concurrent submissions or a pending choice arriving during extraction, with lock release and latest-state reload.
- Exact current acknowledgement, button and error text through current trace/command tests.

Run focused scenario/upload/activation/reparse/switch/history/investigator/command tests, then the full automated suite if practical. Run repository-standard `ruff check .`, `mypy app`, and any configured formatting/static gates. Review the complete diff, dependency direction, and `git diff --check`; compare before/after user-visible messages. No test should require patching a new private lifecycle method.

## Dependency direction and completion criteria

Desired direction: command/upload callers → scenario lifecycle module → source adapters, scenario library, state transaction, state/domain and image refresh. The lifecycle module must not import Discord command handlers; storage and source parsers must not direct activation. Success means a lifecycle rule changes mostly inside one module, without parallel edits to upload/system handlers, pending-state mechanics, activation sequencing and transaction orchestration.

After implementation, write `docs/architecture/scenario-lifecycle-refactor.md` with the observed before/after, interface, ordering, preserved invariants, risks, and **analysis only** of the Game Opening and Combat Obligations candidates. No implementation of those two candidates belongs in this PR.

## Trade-offs and open review points

- Current PDF and Markdown rejection/pending messages differ. Preserve exact wording while sharing lifecycle policy; presentation metadata must support this without making source type select a second lifecycle.
- Existing `scenario use` has a different reset set from new upload, and intentionally preserves valid map positions. Preserve that difference and record it in tests. Any proposed semantic correction needs its own failing regression test and explicit before/after note.
- Filesystem library publication and SQLite commit are not one atomic transaction. Preserve the present publication-before-commit order; an unreferenced library entry after a later failure remains a known risk.
- Multi-part PDF staging is source preparation, but its successful consumption currently edits state in a caller. Decide during implementation whether it belongs in the lifecycle module's submit action or a narrow internal collaborator; the command handler must no longer order the cleanup transaction itself.
