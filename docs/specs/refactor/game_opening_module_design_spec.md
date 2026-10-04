# Game Opening Deep Module

[繁體中文](game_opening_module_design_spec_zh.md)

## Status / Baseline

Category: `refactor`. Status: **implemented**; see the [architecture record](../../architecture/game-opening-refactor.md) for the actual boundary and validation. Factual design baseline after `git fetch origin` on 2026-10-05: `origin/main_v2` `b0e875ccb268966599a19f3823d377a7b4c7630f`, including Scenario Lifecycle PR #173; `ee23aa98fb0359baa7cd8d2e8b41e6b2ddc460fc` was integrated before implementation validation. This spec reconstructs behavior from the design baseline and its tests. The current transaction module is `app/repositories/state_transaction.py`, not `app/state_transaction.py`. The refactor preserves user-visible behavior and began with characterization tests; it added no schema change.

## Existing Flow

```text
/coc start -> router conversation lock (including post-turn hook)
  -> combat replacement guard and early readiness admission
  -> state lock: reload, heal investigators, optional commit_snapshot [H]
  -> public readiness roster reply
  -> to_thread(extract_opening_narration -> provider.analyze_text)
      found: state lock, reload, optional group check registration,
             opening instruction + assistant history, game_started,
             commit_snapshot [S], opening reply, optional check instruction
      not found: keeper turn + narration locks, refresh,
                 supervisor.run_turn(opening_fallback), restricted Narrator,
                 Guard and delivery, _commit_turn_result(start_game=True) [F],
                 public/private/image delivery, background maintenance
  -> router hook claims new pending buttons [possibly transaction B]
  -> release conversation lock; Discord adapter publishes claimed buttons
```

Opening is **not one SQLite transaction**. It can have two authoritative commits (H followed by S or F), then a separate button-claim commit B. H is absent when healing returns no notes. A blocked S or failed F may leave H committed.

## Existing Lock Model

`start` is in the router's `_SYSTEM_COMMANDS`, but not among its long scenario operation exceptions. Thus `_conversation_lock_with_notice()` covers the complete `handle_system_command()` call; start never calls `TurnHandoff.to_narration()`. The lock remains held during roster Discord I/O, `asyncio.to_thread(scenario_intro.extract_opening_narration)`, synchronous provider `analyze_text`, fallback `supervisor.run_turn`, the restricted Narrator/tools, Guard, and output delivery. Other commands in that conversation queue throughout; a queued notice may be sent after roughly ten seconds.

H and S each take `get_state_lock()` for a short reread and snapshot mutation. `commit_snapshot()` also uses that lock and a SQLite `BEGIN IMMEDIATE` transaction, checking the loaded revision and timeline. F takes `locks.narrating_turn()`, which acquires the keeper turn lock then the narration lock and holds both through supervisor, Narrator, Guard, and commit F. Those two locks are released before public delivery; the outer conversation lock remains held. The router hook claims buttons before releasing it; the Discord adapter publishes after release. Conversation locks are process-local. SQLite revision/timeline validation protects a loaded snapshot's commit and F's start guard prevents duplicate commits, but neither proves that extracted text still belongs to the current scenario after another process changes it during extraction.

Centralizing opening policy and reducing lock latency are separate questions. This spec selects the former only.

## Current Transaction Boundaries

| Phase | Authoritative state | Slow work / visible output |
| --- | --- | --- |
| Admission | Read under conversation lock; combat guard and five early conditions | Rejection reply |
| H: healing | Reload under state lock, run `heal_character` on `state.characters`, `commit_snapshot` only if notes exist; revision increases | Roster reply uses the H snapshot and notes |
| Extraction | Read H snapshot's `scenario_text`; no state lock or SQLite transaction, but conversation lock held | Worker thread/provider request |
| S: scripted | Reload under state lock; optional all-or-nothing checks, unknown skills, two history entries, and started flag in **one** `commit_snapshot` | Opening reply after commit; optional reason reply; blocker reply if rejected |
| F: fallback | Refresh, supervisor/Narrator/Guard; `_commit_turn_result(start_game=True)` appends log, starts game, invalidates response chain on latest state | Public/private/image delivery after narration locks; background maintenance scheduled afterward |
| B: controls | Router hook claims pending button identities in another transaction, setting `_buttons_posted` | Send buttons after conversation unlock; existing recovery handles failure |

`commit_snapshot` CAS failure cannot leave half of H/S. A successful H survives a failed S/F. Restricted fallback tools may have their own durable effects before F, so do not describe the entire fallback pipeline as one SQLite transaction.

## Readiness Admission

Before the start branch, `resource_bridge.guard_replacement()` applies to `start` and can reject unsettled combat working state. The start branch then rejects, in order: inactive or missing scenario text; no `state.characters`; `pending_pregen_luck`; and `game_started`. The router also checks an expected Help revision when supplied. Start has no KP-only requirement and does not require the actor to own an investigator.

There is **no general** admission check for `pending_checks`, post-check `pending_luck_decisions`, pending scenario replacement, away/alive status, or a pre-existing timeline. `pending_pregen_luck` differs from `pending_luck_decisions`. With an existing pending check, a scripted opening **without** an opening check can start, a scripted opening **with** an opening check is blocked by `register_many`, and fallback can start. Pending Luck decisions similarly block only a scripted opening that registers a new check. Preserve these differences.

## Investigator Healing Semantics

H is **pre-opening normalization**, separate from S/F. It fills missing base skills and repairs invalid HP/MP/SAN maxima; nine attributes all equal to 50 produce a warning note without guessed correction. Any nonempty notes cause H, even if notes contain only that warning. No notes mean no commit. A successful H increments the state revision and remains after extraction failure, a group check blocker, fallback failure, later transaction failure, or subsequent cancellation. Cancellation before H leaves no H. Characterization tests must pin this separate-commit behavior.

## Scripted Opening Path

`scenario_intro.extract_opening_narration()` remains the source-extraction collaborator. For a found nonempty opening, S reloads state rather than committing H's old snapshot. If `game_started` has become true it returns. There is no further scenario-identity comparison today; the outer conversation lock serializes normal same-process scenario changes.

For an opening check, build one skill or sanity candidate per owner in `state.characters`. The first skill resolution uses `resolve_skill_value(..., register_unknown=False)` to calculate the target **without** mutating character skills. After successful `register_many`, the default `register_unknown=True` call installs any unknown base skill on the cards. This two-step call prevents card mutations when registration is blocked. Sanity candidates default to `loss_success="0"` and `loss_failure="1d4"`.

## Opening Check Semantics

`register_many()` calls `admit()` for every candidate first. If anyone is blocked, it returns blocked decisions and registers **none**. For each owner, pending Luck decision has priority over existing pending check. The handler reports the first blocked owner with its current Chinese Luck/check wording. A blocker produces no S commit, no new checks/history/unknown skills, and leaves `game_started=False`. On success, each player receives a distinct check id with timeline/origin metadata in S. Existing checks do not block S when no opening check is requested.

## History Authority Semantics

S records a user entry as `record_kind="opening_instruction"`, `authority="claim"`, and an assistant entry as `record_kind="narrative"`, `authority="presentation"`. They share turn id and timeline id with the same S commit as optional checks and `game_started=True`. F uses `_commit_turn_result` to record the user claim and assistant presentation with the same turn/timeline. Moving this logic must not promote narration into canonical world facts (ADR-0002).

## Fallback Opening Path

When extraction reports no scripted opening, the current Keeper prompt is assembled, `narrating_turn` is acquired, state is refreshed, and an already-started game returns early. Otherwise `supervisor.run_turn(turn_kind="opening_fallback")` bypasses ordinary intent classification and Executor. The Narrator receives only opening-allowed registry tools; both tool offering and execution enforce that restriction. Consistency, Guard, and `turn_delivery.finalize` precede `_commit_turn_result(start_game=True)`.

That commit rereads the latest state in a transaction, checks the captured timeline, uses a turn-id/request-fingerprint action ledger for duplicate calls, rejects a second start, commits log and started together, and invalidates the response chain. A stale timeline returns false; a conflict raises. The Game Opening module must reuse supervisor and this existing commit primitive, not copy it.

When the Narrator sets `narration_failed`, supervisor returns the current failure wording before F. No new start flag or F opening log is committed; `/coc start` remains retryable, while H and the roster remain. For legacy state with no timeline, supervisor's `_ensure_turn_timeline()` can make a separate timeline-initialization commit before the Narrator, even if F later fails. A rejected F returns the existing stale reply. `run_post_turn_maintenance_after_output` handles public reply, private messages/images, and scheduling maintenance; it is not part of the SQLite transaction.

## Extraction Failure Semantics

No provider, empty input, adapter result `None`/`found=False`, or `found=True` with empty text map to `found=False` and fallback. Provider adapters commonly catch ordinary request exceptions and return `None`. **The extraction function itself does not catch every failure:** it directly calls the provider and assumes a mapping; a custom provider that raises or returns a non-mapping may abort after H/roster without entering fallback. A malformed skill check (missing skill or unknown type) drops the check but **retains** the scripted opening. Error taxonomy improvements are a separate follow-up.

## Failure / Cancellation Matrix

“None” means no new state from this start. `CancelledError` can land at reply, worker wait, or fallback awaits; a synchronous SQLite commit is not partially cancelled by the event loop.

| Point | Authoritative state | Started/check/history | Visible output and retry |
| --- | --- | --- | --- |
| Before H / H failure | No H; transaction rollback | None | Usually no roster; retry safe |
| After H, before/during roster reply | H remains | None | Roster may be absent/uncertain; retry can repeat it |
| Cancellation during extraction | H remains; worker may continue read-only | None | Roster sent; retry after unlock |
| No opening / ordinary provider failure | H remains | Decided by F | Roster sent, then F; a raw exception/non-mapping aborts instead |
| After extraction, before S / S conflict | H remains; S absent/rolled back | None | Roster only; retry safe |
| Group check blocker | H remains | None | Roster then blocker; retry after resolving pending state |
| F Narrator failure/cancellation | H remains; earlier tool effects obey their own transactions | No F start/log | Failure reply if delivered; cancellation may leave only roster; retryable |
| After S/F commit, before opening reply | H and S/F remain | Started and relevant state committed | Opening may not be visible; normal start cannot resend; button recovery may still run |
| After opening reply, before check instruction/maintenance | H and S/F remain | Started | Scripted instruction or fallback private/image delivery may be missing; after entering `run_post_turn_maintenance_after_output`, its `finally` still schedules maintenance; start cannot be rerun |

`asyncio.to_thread` cancellation stops the await, not necessarily the provider worker. Under Option A the worker itself has no authority to apply opening state.

## Observable UX Ordering

Rejection produces no roster. After H, `build_readiness_roster(state, healed_notes, format_mention)` uses the healed snapshot to show HP/SAN/equipment and unclaimed pregens, and replies **before** extraction. Scripted success is roster → opening → optional check reason; a blocker is roster → blocker. F is roster → public generated/failure reply → private/image delivery, then maintenance scheduling. A single call that returns only after all work cannot preserve the early roster reply; the interface needs an early presentation callback.

For scripted checks, the router post-turn hook sees committed pending state. Discord's `ControlCompletion` captures the before-snapshot, claims newly pending buttons **while the outer conversation lock remains held** (a separate commit), and publishes only after unlock. Failure follows the existing identity/recovery path. Game Opening does not build buttons; the hook/lock/commit order must stay intact.

## Problems / Leaked Knowledge

`system.py` currently owns readiness policy, H-before-roster ordering, extraction choice, scripted/fallback routing, group candidate construction, delayed unknown-skill installation, history provenance, started commit, fallback lock/turn kind, and delivery ordering. Those related rules require touching the transport handler to change opening behavior. A forwarding wrapper that leaves H/S/F sequencing in the handler fails the deep-module deletion test.

## Proposed Deep Module Boundary

Create `app/services/game_opening.py` as a module with a small interface. Internally coordinate `character_service`, `scenario_intro`, `check_lifecycle`, `history_authority`, `state_transaction`, and the existing supervisor/Keeper fallback commit. It must not import Discord, command handlers, generic Narrator implementation, or button implementation. `system.py` parses and renders `/coc start`; Router retains generic conversation serialization and the post-turn hook.

## Public Interface

Proposed conceptual interface:

```python
async def open_game(
    conversation_id: str,
    actor_id: str,
    *,
    on_readiness: Callable[[Readiness], Awaitable[None]],
) -> OpeningResult: ...
```

`Readiness` is immutable roster presentation data (including healing notes, equipment and unclaimed pregen count), not a mutable `GroupState`. The handler applies `format_mention` and replies inside the callback. A small `OpeningResult` reports rejection/check blocker/scripted success/fallback result, opening text, optional reason, private/image delivery data and a presentation key. Existing wording stays in the presentation adapter. The callback only sends the early roster; it does not tell the module which transaction to perform. The module awaits it after H and before extraction; callback failure retains today's abort behavior.

The interface takes no pending dictionaries, SQLite connection, mutable state, manual lock, candidate set or prepare/commit/finish commands. Generic Router locking is a command-dispatch policy; the start handler acquires no special lifecycle lock. The module owns H/S state locks/transactions and the fallback `narrating_turn`. The existing transport delivery collaborator consumes the result without learning F's commit internals.

## Option A vs Option B

| Dimension | A: keep outer lock | B: phased extraction outside lock |
| --- | --- | --- |
| Locality | Concentrates policy in one module | Same, plus claim/revalidation protocol |
| Latency | Other commands still wait for extraction/fallback | Extraction can overlap commands; fallback unlock needs separate proof |
| Concurrency complexity | Existing in-process serialization and state CAS | Scenario/source identity, roster, characters, pending, double start and output need revalidation |
| Behavior compatibility | High; roster/character set remain frozen | A scenario or character may change after roster, visibly changing behavior |
| Implementation size | Modest ownership refactor | Larger Router/hook/state-machine rewrite |
| Race risk | Existing cross-process/delivery risks | Adds stale extraction and ABA risks |
| Test burden | Characterization and existing conflict/double-start tests | Seven deterministic interleavings plus source identity checks |

## Selected Design

**Option A.** Scenario Lifecycle now exposes `scenario_library_id`, `scenario_variant_id`, and `timeline_id`; the library manifest has `content_hash`, but `GroupState` does not persist a source version/fingerprint bound to the active scenario. Repair can update scenario text on the same timeline; library id and variant alone do not identify that version. Comparing only text cannot settle source identity/ABA; requiring total `state_revision` equality would invalidate extraction for away/back, button claims and unrelated writes that today's lock would have serialized afterward. Unlocking also changes the frozen roster/character-set behavior. A claim schema or complex selective revalidation is disproportionate to a behavior-preserving architecture refactor. Latency work should be separate.

## Concurrency Protocol

Option A retains `Router conversation lock -> Game Opening -> (short state lock + SQLite transaction)`. F additionally uses `keeper turn lock -> narration lock` in the existing order. Two starts in one conversation queue at Router; after a successful S/F, the second sees `game_started=True`. If another process bypasses that process-local lock, S's commit CAS and F's latest-state start guard still prevent **two opening commits**. But S rereads and builds its commit snapshot *after* extraction: a scenario switch by another process *during* extraction can allow the old opening to apply to the new state. F can likewise refresh a new state after deciding to fall back from the old source. This is an existing cross-process stale-source risk, not a guarantee supplied by Option A. Future cross-process strengthening needs a source identity, tests and a separate behavior fix. S rereads and checks `game_started`; it never writes H's old snapshot over a newer write.

There is **no** unlocked extraction apply protocol in this design. Do not implement a half measure guarded only by `scenario_text`. If B is proposed later, specify extraction identity (at least timeline + library/variant + actual source version), latest-state final admission (started, active, characters, Luck/check blockers), character-set change policy, roster-after-conflict UX, button claim ordering and ABA proof. Today's character set remains frozen. Choosing the latest set under B would require an explicit product behavior decision.

## Preserved Invariants

1. Combat replacement guard, ordered readiness rejection and non-KP-only start.
2. H is independent and precedes roster/extraction; no notes means no commit; H survives opening failure.
3. Pending checks only block scripted S when it asks for an opening check; no-check S and F do not reject them generally.
4. Group check registration is all-or-nothing; unknown skills are written only after success.
5. S commits checks/history/started together and retains ADR-0002 provenance.
6. F reuses supervisor and `_commit_turn_result(start_game=True)`; failure remains retryable and response-chain behavior is preserved.
7. Roster, opening, optional check instruction, and button ordering stay the same.
8. Conversation locking stays coarse; no schema/provider/OCR/combat changes.

## Characterization Tests

Before implementation, use temporary real SQLite through Router/new module interface, mocking only provider and external Discord/image boundaries. Assert persisted state, revision, log, pending checks, and reply ordering. Existing `tests/test_unified_keeper_turn_flow.py` covers some fallback, distinct check ids, Luck blocker and F atomic commit, but many handler tests patch `load_state`/`commit_snapshot` and do not establish persistence.

Add: no scenario, no characters, pregen Luck, already started, combat replacement rejection, and the lack of a general pending-check/Luck/scenario-pending gate; no healing/no commit, H revision and roster timing, H persistence after S blocker/extraction exception/F failure; scripted no check, skill, SAN, malformed skill, one-owner pending check/Luck, group all-or-nothing, unknown-skill installation only on success; both history provenance entries and shared turn/timeline; found=false/provider failure F, F success/failure/retry/timeline mismatch, duplicate start; roster before opening, check reason after opening, pending buttons after S commit; observable cancellation checkpoints.

## Race Tests

Even with A, deterministically test: concurrent same-conversation starts yield one S/F success; when extraction pauses at a provider barrier, another Router command (character change or scenario switch) cannot enter its mutation; a direct transaction **after S has loaded its commit snapshot but before commit** produces a CAS conflict; F's final commit rejects a timeline change after extraction; buttons claim only after S; cancelling extraction cannot let its worker commit; failed F does not consume started. Separately pin the existing cross-process scenario switch *during* extraction as a known risk; do not incorrectly claim CAS protects it. A later B proposal additionally needs scenario switch, timeline replacement, two starts, character add/remove/switch, pending Luck, pending check, and unrelated revision races, with explicit continue/invalidate outcomes rather than accidental total-revision equality.

## Dependency Direction

```text
Discord adapter -> router (generic lock and post-turn hook)
                -> system start presentation -> Game Opening interface
                                            -> character_service / scenario_intro
                                            -> check_lifecycle / history_authority
                                            -> state_transaction / supervisor fallback
                                               -> existing Keeper turn commit
```

Game Opening must not import handlers, router, or Discord. Deletion test: removing it should force readiness, healing, extraction routing, check admission, history and started ordering back into callers. If only a forwarding wrapper disappears while callers keep coordinating H/S/F, it is too shallow.

## Non-goals

No Scenario Lifecycle, PDF/OCR, combat or ADR-0003 obligation changes, Keeper rules, generic supervisor/check semantics, schema, `/coc newgame`, provider error taxonomy, or Discord wording. Existing Discord/SQLite non-atomic delivery and cross-process early-admission limitations are recorded risks, not silent behavior changes.

## Implementation Plan

1. Add persistence characterization and deterministic race tests first; existing tests must pass unchanged.
2. Define the small result/readiness callback in `game_opening.py`; move start's combat replacement guard from `system.py`'s shared guard branch into this module, retaining its order before the five readiness checks; move the other admission, H, extraction choice, S, and F coordination while reusing collaborators and presentation keys. Do not move provider/Narrator/button implementations.
3. Reduce `system.py` start to `open_game` invocation and result rendering; preserve Router's generic lock/hook and the early roster callback.
4. Compare state/output/button sequence at each phase; run relevant tests, full pytest, Ruff, mypy and `git diff --check`. Confirm `system.py` no longer knows H/S/F ordering or pending internals.

## Risks

- The outer conversation lock still blocks a channel through slow extraction/fallback; that is the accepted latency cost.
- Cancellation or delivery failure after commit may leave a started game whose opening was not seen; preserve current behavior.
- Restricted fallback tools can have independent durable effects, so F is not the only possible fallback transaction.
- Common provider failures map to found=false, but malformed/non-mapping or uncaught exceptions can abort after H; characterize now, improve separately.
- Button claim is a distinct post-opening transaction; delivery failure uses existing recovery and it must not move before S.
- The conversation lock is process-local. S/F currently lack source-identity revalidation if another process switches the scenario during extraction; this existing correctness risk is separate from whether this refactor unlocks slow work.

## Follow-ups

Separately evaluate phased extraction (Option B), provider extraction error taxonomy, and resend UX for committed-but-undelivered openings. Each needs its own tests and product decision.

## Ten design answers

1. H is pre-opening normalization, because it commits first and is not rolled back with S/F.
2. A committed H remains after opening failure; preserve that.
3. S group checks, history and started must share one authoritative commit.
4. F must reuse `_commit_turn_result(start_game=True)` for latest-state, timeline, ledger, duplicate-start and response-chain semantics.
5. Do not release the conversation lock for slow extraction in this refactor.
6. No unlocked claim is needed now; a later design needs a source version beyond current library id/variant/timeline plus final revalidation.
7. Character set stays frozen during extraction; S uses its latest reread under the same outer lock. A phased design needs a separate decision on roster drift.
8. Router serialization plus S CAS and F's latest-state started guard allow only one success.
9. The readiness callback replies before extraction; result delivery replies with opening then optional check reason; button hook remains after commit.
10. `system.py` retains only parsing, rendering and transport delivery, not healing/extraction/check/history/started policy.
