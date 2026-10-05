# Game Opening Phase 2: Unlocked Extraction

## Status and baseline

Design draft, not yet safe to implement without a persistent active-source binding and the race-test gate below. Investigated `origin/main_v2` at `c5a19912c2f1faf5454eb262693ddbc7dc3d0e4d`, which includes Scenario Lifecycle #173 and Game Opening #178. This round changes no production behavior.

The objective is to stop holding a conversation's mutation lock through slow, read-only `scenario_intro.extract_opening_narration`, without allowing a stale opening to start a different scenario, source version, or timeline. **Select Design B / Phase 2A: unlock extraction only.** The fallback Narrator, Guard, restricted tools, final Keeper transaction, and delivery retain their current locked pipeline.

## Existing flow and lock cost

Router's ordinary `_SYSTEM_COMMANDS` path wraps all of `/coc start` in `_conversation_lock_with_notice`. Under that lock, Game Opening performs admission, short state-lock healing and an optional independent commit, awaits the readiness reply, awaits `asyncio.to_thread(extract_opening_narration)` (whose provider call is synchronous `analyze_text`), then commits a scripted opening or runs the fallback. Scripted public replies and fallback `supervisor.run_turn`, Narrator, Guard, public/private/image delivery all remain inside the conversation lock. The Router's pending-button claim hook runs in the lock's `finally`; Discord publishes buttons after Router returns. Background post-turn maintenance is scheduled separately. No production extraction-vs-fallback latency distribution was available in this repository, so no share of total latency is asserted.

The synchronous state lock never spans the provider or a delivery await. Fallback additionally acquires `narrating_turn` (Keeper turn lock, then narration lock). Ordinary text may acquire a KP priority gate before the conversation lock and can hand off read-only `player_action` narration after commit. `opening_fallback` narration can still use restricted state-writing tools, so that handoff is not valid for it.

## Source identity: required prerequisite

Persisted `GroupState` has a timeline, monotonic state revision, scenario library ID, variant ID, chapter IDs, and the current `scenario_text`. It has **no persisted binding to the activated full-source version**. The library manifest has a full parsed-text `content_hash`, but it is filesystem data and is not atomically committed with the active SQLite state. `load_context` creates a chapter window; the extractor reads `GroupState.scenario_text`. Repair can update the same scenario ID without rotating the timeline, and the library can replace an entry under the same ID.

An ID alone misses repair; a timeline alone also misses repair; comparing the preparation revision rejects harmless writes; hashing only the current chapter misses a repair that changes another chapter. Reading the current filesystem manifest just before a SQLite commit is a time-of-check/time-of-use gap.

Before unlocking extraction, bind the verified library full-source hash into the **same SQLite commit** as each active context installation, for example a backward-compatible `active_scenario_source_hash` field. Audit new upload, repair, scenario use, chapter advance, rollback/newgame, and every `install_context_fields` caller. An old snapshot with no binding must keep the Phase 1 coarse-lock path until safely rebound; never infer its active binding from the present filesystem alone. This is future implementation work, not a production change in this design round.

Proposed persisted-state token:

```text
OpeningExtractionToken(
  timeline_id, scenario_library_id, scenario_variant_id,
  active_chapter_id, context_chapter_ids,
  active_scenario_source_hash,  # full library text, bound in SQLite
  context_sha256,               # exact persisted extraction input
  roster_identity,              # independent participant policy guard
)
```

The full-source hash catches same-ID repairs even if the current chapter is unchanged. The context digest binds the exact extractor input. A byte-identical republish is content-equivalent for this extractor; if publication events themselves must invalidate work, add a persisted publication generation instead. “Active source” means the DB-committed binding: library publication alone does not activate it. The existing filesystem/SQLite non-atomicity remains.

## Prepare → work → apply

1. **PREPARE, Router conversation lock held.** Reload and perform current admission and combat guard. Under the short state lock, heal investigators; commit only when changed. Capture the token, exact text, and immutable post-healing readiness roster. Await the roster delivery before releasing the conversation lock. Router runs its normal button-claim hook.
2. **WORK, no conversation/state/Keeper/narration lock.** Run the read-only extractor in `asyncio.to_thread` against captured text. Its worker has no state-write or delivery capability.
3. **APPLY, reacquire through Router scheduling.** Within the final SQLite authoritative transaction, reload and compare the source/timeline token and latest admission. For scripted opening, use latest state for `register_many`, skill installation, history, and `game_started`, committing all in one transaction before delivery. For `found=False`, perform the same token/admission validation *before* entering the existing locked fallback pipeline. Hold the second conversation scope through final delivery and button claim.

Router owns lock acquisition, queue notices, and hooks. Game Opening owns phase ordering and domain policy. A narrow `mutation_scope` async context capability supplied by Router to `open_game`, plus transport-neutral `on_readiness` and `on_completion` callbacks, lets the service perform both locked phases without importing Router/Discord or exposing a raw lock. The completion callback runs inside APPLY so commit and final reply retain their old ordering. Do not manually release/reacquire a lock, and do not make the handler call `prepare()` and `apply()` in domain order. The prepare hook cannot claim a new opening check; the apply hook must claim checks after the successful commit.

Source and admission checks must happen **inside the same SQLite transaction as the scripted write**, for example in a `state_transaction.mutate` callback on `ctx.state`. A pre-transaction load/check followed by a separate commit is not proof against another process. Reuse the existing Keeper `_commit_turn_result(start_game=True)` for fallback timeline, double-start, log, and idempotency guarantees, **but its final start transaction must also atomically compare the source token**: another process can repair the same ID during fallback narration after the pre-fallback check. Add a narrow guard to the existing commit primitive, not a second fallback commit in Game Opening. HOLD if the guard cannot reach that transaction. Current opening restricted tools' private/image requests must remain undelivered when the final commit rejects; reconsider this if the allowed tool set later gains earlier persistent writes. Healing remains an independent pre-opening commit.

## Participants, readiness, and admission

Choose **C3: invalidate and request retry if participant membership or active-investigator binding changes**. Under Phase 1 these were frozen between roster and opening. C1 would register checks for retired investigators; C2 would include newly joined investigators who never appeared in the roster. Capture owner→character ID and active bindings, not the global revision. Re-evaluate the latest HP/SAN/skills and other roster/check inputs; if a change makes the already-sent roster false, return `state_changed_retry` rather than install checks from old values. Tests must establish the exact projection.

Choose **R1**: healing is still committed independently, and the roster is still sent before extraction. It can become stale while unlocked; final admission must reject or request retry on relevant changes. Choose **D1**: another command's reply may appear between this start's roster and its opening. Within one start, keep `roster → opening → optional check instruction`. D1 is an explicit user-visible interleaving change. If product owners reject it, retain Design A or separately design a conversation-wide output sequencer. The narration lock alone does not order every system-command reply. A readiness callback failure retains prior healing and prevents extraction.

At APPLY rerun the current combat replacement guard, active/scenario-text, characters, pending pregen Luck, and game-started checks. Only a scripted **group check** passes through `register_many` and its generic pending-check/Luck-decision blockers. Scripted no-check and fallback gain no new generic pending gate. Distinguish `stale_source` from changed admission/retry. Concurrent starts may duplicate expensive extraction, but one SQLite final transition wins; no durable or in-memory opening claim is needed.

## Mutation matrix during extraction

| Mutation | Invalidate extraction? | Re-run admission? | Reason |
|---|---|---|---|
| away/back | Only if roster/eligibility projection changes | Yes | Revision alone is too broad |
| setpersona | No | Yes | Does not change extractor text |
| era | Only if participant/readiness projection changes | Yes | Selective participant guard |
| KP Assistant transfer | No | Yes | Not a source change |
| HP/SAN/skill change | Retry if roster/check projection changes | Yes | No stale candidate values |
| investigator add/retire | Yes | Yes | C3 participant set |
| active investigator switch | Yes | Yes | C3 binding |
| pending generic check | No | Yes | Only group-check path may block |
| pending Luck decision | No | Yes | Preserve three path policies |
| pending pregen Luck created/resolved | No if participant set unchanged | Yes | Current readiness gate |
| same-ID source repair | Yes | Yes | Full source binding changed |
| scenario use/new upload | Yes | Yes | ID/timeline changed |
| newgame/timeline reset | Yes | Yes | Timeline changed |
| map position | No | Yes | No source dependency |
| public log append | No | Yes | `game_started` still gates duplicates |

“No” never means skipping final admission. Validate behavior against persisted transitions, not command-name guesses.

## Scope and design comparison

| Dimension | 2A: extraction only | 2B: extraction and fallback |
|---|---|---|
| Mutation-lock latency | Removes scripted provider wait; fallback still blocks | Removes both waits |
| Concurrency | One token revalidation | Also tools, Guard, turn commit, and handoff |
| Stale result | Extracted prose or `found=False` | Also Narrator/tool/delivery results |
| Supervisor handoff | Reuses locked fallback | Requires a new safe opening-fallback handoff |
| Delivery order | Other replies may interleave with roster/opening | Public/private/image effects can also interleave |
| Size and regression surface | Router, Game Opening, source binding | Also supervisor, Keeper, Guard, delivery |

| Property | A: existing coarse lock | B: extraction-only unlock (selected) | C: full handoff |
|---|---|---|---|
| Extraction blocks mutation | Yes | No | No |
| Fallback blocks mutation | Yes | Yes | No |
| Source stale protection | Existing process-local limitation | Persisted binding, atomic DB validation | Same plus tool/turn identity |
| Narration ordering | Existing | Existing fallback; roster may interleave | New pipeline-wide protocol |
| Cross-process, code, rollback, test, deployment burden | Existing/minimal | Moderate; no claim recovery | High |

**Select B / 2A**, conditional on implementing and testing the active-source binding. If atomic validation in the final DB transaction cannot be established, **HOLD and keep A**. There is no production latency split to prove that 2A removes most total start latency; fallback may remain dominant. C is a separate investigation.

## Deterministic race-test gate

Use real temporary SQLite and real state transactions. Control synchronous extraction with `threading.Event` inside `to_thread`; control async delivery/Narrator with `asyncio.Event`. Wait for extractor entry, commit an intervening mutation through Router or a separate DB connection/process-equivalent writer, release extractor, then assert persisted state/history/checks and visible order. No sleeps as race synchronization.

| ID | Interleaving and required result |
|---|---|
| RACE-01 | S1 → scenario use S2; discard S1 prose/check/history, S2 not started |
| RACE-02 | Same ID full-source V1 → V2 repair, including unchanged active chapter text; discard V1 |
| RACE-03 | Timeline replacement/newgame; discard old opening |
| RACE-04 | Two starts extract concurrently; exactly one start, history pair, group-check set; loser sends no stale prose |
| RACE-05 | Investigator add/retire/switch; C3 retry, no partial checks |
| RACE-06 | Pending pregen Luck appears; latest admission rejects |
| RACE-07 | Pending generic check appears; no-check scripted start may proceed, group check blocks atomically |
| RACE-08 | Pending Luck decision appears; preserve no-check/group-check/fallback differences |
| RACE-09 | Unrelated revision only; proceed if identity/admission remain valid |
| RACE-10 | Cancel extraction await; worker eventually finishes but cannot apply; healing remains |
| RACE-11 | Independent DB writer changes source binding without sharing Python lock; SQLite token check rejects old result |
| RACE-12 | Source/timeline changes after `found=False`, before fallback; no stale fallback |
| RACE-13 | Writer races after revalidation; a single SQLite transaction/CAS prevents intervening overwrite |
| RACE-14 | Roster → other command reply → opening D1 order; opening/check instruction stay ordered |
| RACE-15 | Pending checks commit before Router button claim, publication after command returns |
| RACE-16 | After `found=False` revalidation, an independent DB writer repairs the same ID during fallback narration; Keeper's final start commit rejects the old source token and sends no successful opening/private/image |

Retain Phase 1 characterization for independent healing, readiness callback errors, one scripted commit, all-or-nothing group blockers, ADR-0002 history authority, fallback retry/timeline guard, delivery-after-commit failure, and cancellation. Performance gate: an unrelated mutation completes while the extractor barrier remains blocked; provider time is absent from `T_opening_extract_lock_hold`. Report that fallback still holds the lock. Suggested low-cardinality metrics: `opening.extract.duration_ms`, prepare/apply lock hold, and stale discard reason.

## Failures, dependencies, non-goals, implementation order

Healing remains committed after later failure/cancellation; the roster may already be visible. A cancelled worker may finish but has no commit capability. A stale token or participant set commits no checks/history/start and emits no stale opening. Even `found=False` requires revalidation. Scripted commit followed by Discord failure leaves the game started, as today. Without a claim, duplicate extraction incurs provider cost but crashes leave no claim to recover.

Dependency direction: `Router scheduling → system presentation → Game Opening → scenario_intro / state_transaction / check_lifecycle / history_authority / existing supervisor`. Scenario Lifecycle binds source identity when installing active context; Game Opening consumes the binding, never controls PDF/Markdown parsing. It does not import Router, Discord, or buttons.

Implementation sequence after approval: (1) source-binding compatibility and legacy tests; (2) failing deterministic races, especially same-ID/same-context repair; (3) Router-provided phase scopes and one Game Opening intention API; (4) atomic scripted revalidation and existing fallback delegation; (5) output/button/cancellation/cross-process tests; (6) metrics and full regression gate. Stop before step 3 if active source cannot be reliably bound. Do not change the prompt, healing, pending check/Luck policy, history authority, generic supervisor, provider/OCR, combat, or fallback tool contract.

Review decisions: D1 message interleaving requires product acceptance; content-identical republishing is semantically equivalent for this text-only extractor unless publication generation is required; all context-installation and legacy snapshot paths need audit; existing filesystem/SQLite non-atomicity remains; Phase 2B needs its own tool/Guard/delivery protocol and is not authorized here.
