# Turn safety, recoverable results and measured latency

[繁體中文](turn_safety_and_latency_design_spec_zh.md) | [Docs index](../../README.md)

## 1. Status, provenance and decision

Category: `refactor` with bug-fix and performance work packages. Status: **S0/S1/S3 implemented; S2 reverted with PR #99**. Baseline: `main_v2` at `8e32683a3e3a3c0159153b3d96d9c6911ec04071`, including PR #94 and its review fixes. Review date: 2026-09-27. Subsequently aligned with `main_v2` `e03d5dc`; review R1–R6 contracts are in section 12. Branch: `refactor/turn-safety-and-latency`.

This specification adopts selected recommendations from two user-supplied ChatGPT documents after reading both documents and checking the current source. It does not adopt their older source baselines as current facts. The original proposal was documentation only; this branch implements S0/S1 as recorded in section 13. No production migration, deployment or live API benchmark was performed.

| Input document | Its source baseline | SHA-256 of supplied file |
| --- | --- | --- |
| `main_v2_detailed_fix_plan_ux_latency_v2.md` (F1–F8, UX, E2E) | `afe8ace` | `ce79390c4d79ebe24e1eb93ea0118a213d0c3d6b7fb9a9a3639f8b26053d7224` |
| `main_v2_python_optimization_safety_addendum.md` (P1–P8, C01–C20) | `c05e152` | `1a9d9afa11bee169e005ca066abef41b34f2495aac9c545f14556933a277660f` |

**Decision:** adopt correctness fixes and equivalent local optimizations first. Preserve normal Executor continuations and the existing Narrator. Treat durable operation recovery and delivery as later, separately testable migrations. Defer early termination of Executor to a separate, disabled experiment with explicit workflow coverage.

The source documents are review inputs, not repository implementation contracts. The decisions below govern this proposal. Existing implemented specs remain in force until each approved work package lands.

## 2. Current evidence and recommendation disposition

`Confirmed` means source inspection or a specified isolated probe; it does not imply every production turn fails. `Opportunity` means a measurable optimization candidate, not a proven latency bottleneck.

| Input | Current evidence at baseline | Decision and adjustment |
| --- | --- | --- |
| F1 movement | `router._handle_ordinary_text_message_locked` and `_run_sudo_act_locked` call `_resolve_map_action_transaction` before Supervisor. The helper saves a separately loaded state; regex and room-name matching can mutate location. | **Confirmed bug; first tranche.** Pure proposal, shared commit validation and refreshed caller snapshot. Topology alone cannot prove a passage is unlocked or safe. Support scenarios without maps. |
| F2-A partial results | `executor.run_executor` replaces facts with a generic instruction on Exception, while inventory events, pending evidence and actual DB changes may survive. `enforce_mechanic_check_consistency` then replaces incomplete narration with a generic warning. | **Confirmed loss of specific handoff facts; first tranche.** Repair both layers. Do not claim all persisted effects were lost. |
| F2-B operation ledger | `GameEvent` has type/payload but no durable operation identity. Tool recovery markers exist; source search finds creation/serialization but no admission consumer of those markers. | **Recovery gap; staged refactor.** Add immediate in-process worker ownership/hold before relaxing concurrency; durable ledger follows. A full late-worker race has not been reproduced in this review. |
| F3 output recovery | `_deliver_side_effects` is best effort; DM/image failures are logged and absent images skipped. Receipts/corrections already exist. No general delivery outbox in current DB tables. | **Adopt in later tranche.** First keep inline timing with a unique outbox claimant. Reuse receipts; no exactly-once claim across SQLite and Discord. |
| F4 player OOC | `intent_router.classify_intent` classifies whole parenthesized player text as PURE_ROLEPLAY. Supervisor's canonical commit is not OOC-aware. KP Assistant already has its own path. | **Confirmed routing gap; first tranche.** Separate role from message mode and output audience. Parentheses alone must not suppress real actions. |
| F5 early routing | Supervisor awaits `build_context` before intent routing. Scenario/Memory retrieval is already concurrent with separate degradation. | **Adopt conservatively.** Route using current state before optional retrieval. Keep an appropriate ACK response and ordinary gameplay retrieval until equivalence is measured. |
| F6 mirrors | `_save_state_unlocked` executes `SELECT key, data FROM characters`, then upserts all current aliases on every save. Caller revision is changed before outer commit exits. | **Confirmed unnecessary work and commit-boundary risk.** Exact-key read/diff first; copy back revision only after commit. Membership table is conditional, not a prerequisite for ledger/outbox. |
| F7 stage prompts | Executor/Narrator wrap the full Keeper prompt and rebuild dynamic context. Gateway formats broad tool results. | **Adopt after structured partial facts.** Share section definitions; reduce duplicate machine content. Ordinary and tool-enabled Narrators need different capabilities. |
| F8 read paths/locks | Router command branches use conversation locks; state reads/commits include synchronous calls. Gateway offload/shield and detached maintenance already exist. | **Audit and extract pure reads first.** Do not merely remove locks or offload a mutable state shared with the event loop. Moving delivery outside gameplay locks depends on outbox/barriers. |
| P1 known arguments | Purchase injects `_turn_key`/`_owner_id`; search injects retrieval principal. No general immutable execution context. | **Adopt incrementally.** Inject authority, never infer an ambiguous gameplay target or replace a valid other-player target with the caller. |
| P2 check evidence reuse | Follow-up refreshes state and receives structured resolved outcome/action context; no general versioned evidence-reference handoff. | **Adopt later.** Rehydrate authorized source references; do not persist a prior prompt or reuse a PR #94 cursor as durable evidence. |
| P3 RAG grouping | `_result_rows` scans eligible siblings per chosen record. After PR #94, v4 uses `_ranked_rows -> scenario_retrieval.project` instead. | **Narrow scope.** Group eligible v3/legacy candidates once per search; benchmark that path separately. Do not claim it accelerates v4. |
| P4 token/prompt reuse | `provider_history` now shares the selection policy with `request_budget` (PR #94). `select_history` still re-encodes histories/suffixes. | **Partly already solved.** Reuse request-scoped selection/count results; preserve exact selection. Whole-log retrieval overcharging must not return. |
| P5 snapshot/DB work | `gameplay_snapshot` serializes state, removes some fields, deep-copies; Executor takes before/after snapshots. These validate isolated changes. | **Measure first; keep checks.** Optimize mirrors/serialization first; do not replace deep snapshots with nested references or a model-reported changed-fields list. |
| P6 fixed workflows | Purchase already has an atomic domain service; combat already has damage/effect services; transfer validator accepts both add/remove orders. | **Selective future work.** Atomic authorized transfer is a candidate. Do not rebuild purchase/combat rules or activate the separate macro-combat backlog implicitly. |
| P7 early stop | `enable_wrapup=False` skips exhaustion wrap-up, not normal tool-result continuation. Ordinary Narrator has no tools. | **Deferred, off.** Tool success/pending presence/model done flag cannot prove the player's whole action is handled. |
| P8 narrative requirements | Validated resolution and multi-actor current state exist; generic incomplete fallback can erase concrete information. | **Adopt with F2/F7.** Events + pending + scoped output intentions form the requirements. IDs/regex cannot prove full semantic narrative quality. |

### Isolated baseline reproduction

The probes used a disposable SQLite database, blank provider/bot keys and a fake provider. The map fixture has origin A, west exit B, east exit C, upstairs D and disconnected named room X. Each input starts from A; no LLM or RAG request is needed.

| Input | Stored room observed | Expected behavior |
| --- | --- | --- |
| `不要往左` | B | Stay A |
| `樓上有聲音嗎？` | D | Stay A; answer inquiry |
| `向右走，不要往左` | B | Propose C, subject to conditions |
| `我查看左邊的門` | B | Stay A; observation is not entry |
| `他說「往左走」` | B | Stay A; quoted speech is not authorization |
| `我去密室` | X | Do not teleport to a disconnected room |

In all six helper probes, the caller snapshot still said A. Another probe successfully called `add_carried_item` for `rope`, then raised a fake provider error: the DB retained the item and an `inventory_change` event survived, but `narrative_facts` became the generic error instruction and final consistency output omitted the acquisition. A log-only save with one character executed a whole-character-table scan and two mirror upserts. These are functional/query observations, not performance measurements.

The existing isolated baseline suite passed **940 tests, 1 skipped, 33 subtests**. Passing existing tests does not cover these new counterexamples. Convert the probes into permanent regression cases during implementation. No production data was read or changed by the probes.

## 3. Goals and invariants

1. Preserve natural-language play, legitimate multi-step movement, NPC interaction and narrative quality. No new routine move approval, read receipt or action restatement.
2. Current state and committed results remain authoritative. A proposed move, successful lookup or pending check is not a completed player action.
3. No fixed classifier/planner/judge/repair LLM stage. Keep player actions in Supervisor; resolved checks and opening fallback retain their current restricted Narrator route. KP Assistant stays independent.
4. Check/Luck ownership, timeline, actor/subject, revision, source version and recipient visibility must survive every new adapter.
5. Incomplete evidence means unknown. Preserve original-source fallback, required armor/attacks/abilities/limits and distinct names for simultaneous identical monsters.
6. No replay of an already committed operation, reroll of an exposed result, or public fallback for a failed private message.
7. Preserve sequential same-conversation gameplay. Pure reads may later use the latest committed snapshot while the turn is running; they cannot act on it without revalidation.
8. Show checks, dice and Luck at their existing safe times. Measure when buttons become actionable, not just visible. Typing is not meaningful output.

Out of scope: model/reasoning changes, dynamic tool-scoping rollout, rewriting scenario translations, automatic source translation, speculative dual-model execution, public unvalidated streaming, microservices, multi-process ownership of the same game, bulk rewriting historical OOC, or general Python inference of story consequences.

## 4. Flow and interface map

### Current ordinary action

```text
Discord -> router [conversation gate, load snapshot S0]
  -> map helper [load S1 -> parse -> SAVE location]
  -> Supervisor(S0, resolved_location)
      -> build_context [Scenario || Memory]
      -> classify -> Executor -> tools [individual commits]
      -> validated resolution -> Narrator
      -> Guard -> spoiler check -> mechanic consistency rewrite
      -> canonical commit [reload latest]
  -> public reply -> DM/images [best effort] -> maintenance
```

### Target flow (staged; not yet implemented)

```text
Transport(message/interaction ID, actor) -> early defer if applicable
  -> pure input hint
  +-> verified read command -> ReadView [short read tx] -> audience renderer
  `-> existing priority/conversation admission -> latest authority snapshot
       -> state-aware RouteDecision + correction/pending checks
       +-> KP Assistant -> existing separate agent/authorization/commit
       +-> PLAYER_OOC -> public/self projection -> one answer -> noncanon output
       +-> ACK/roleplay -> minimal sufficient context -> existing Narrator
       `-> RetrievalPlan -> authorized evidence [Scenario || Memory]
            +-> ordinary: MovementProposal [no write]
            |    -> Executor <-> gateway/tools [shared admission; arrival before dependent effects]
            |    -> final movement commit only without remaining arrival dependencies (§12.1)
            |    -> refreshed state + validated resolution/requirements
            |    -> ordinary Narrator [no tools]
            +-> resolved: authoritative check + final Luck result
            |    -> shared admission validates/commits movement bound to this check
            |    -> restricted Narrator <-> permitted follow-up tools
            `-> opening fallback: restricted Narrator <-> opening tools
                    |
                    v
            reconcile actual outcomes, pending and execution health
            -> Python DeliveryEnvelope [facts / narrative / controls / audience]
            -> mechanic consistency -> Guard -> recheck changed text -> final audience safety
            -> delivery contract [one safe fallback/recheck on failure; still invalid -> blocked]
            -> final commit [canon/outcome/outputs]
            -> immediate delivery claim -> transport -> receipts
                 `-> server RecoveryMode
                      +-> delivery_only: sealed payload, zero model calls
                      +-> render_only: tools=[] and rejecting callback
                      `-> reconcile_required: account for unresolved effects, no whole-turn replay
```

In the resolved-check route, any movement commit must occur after final Luck/result validation and before the restricted Narrator consumes the outcome. If the Narrator performs permitted follow-up tools, collect those outcomes and apply the same final checks/commit; do not add another ordinary Narrator. Opening first keeps the extracted opening path; only the existing fallback is model-generated.

| Boundary | Existing entry to retain | Proposed extension / consumer |
| --- | --- | --- |
| Transport | `discord_bot`, command callbacks | Trusted source-event identity and delivery results; no durable raw Interaction object |
| Routing | `commands/router.py`, `agents/intent_router.py` | Input hint + state-aware `RouteDecision`; ordinary and sudo share behavior |
| Context | `context_builder.build_context` | Pure base projection then `RetrievalPlan` enrichment; immutable worker input |
| Movement | `intent_parser`, `scene_map`, map handlers | `MovementProposal`/shared service; no pre-Supervisor commit |
| Execution | `executor.run_executor`, gateway, `keeper._execute_tool` | `ToolExecutionContext`, observed outcomes; same domain mutators |
| Admission | Every mutator/timeline-switch entry | Shared `MutationAdmission`; hold owner, authority, evidence and pre-write revalidation |
| Output contract | Existing Narrator response, renderer/commit | Candidate mixed segments -> server `DeliveryEnvelope`; separate canon and recipient projections |
| Recovery mode | Existing task ownership; later ledger/outbox | Server `RecoveryMode` overrides turn_kind; rendering recovery cannot offer tools |
| Checks | check/Luck callbacks, `_finalize_check_result` | Existing identity/result + optional proposal/evidence refs |
| Resolution | `turn_resolution.validate_resolution` | Validate proposed move against its committed event; disposition stays distinct from execution health |
| Narration | ordinary/restricted `narrator`, `prompt_config` | Stage sections and scoped requirements; no unavailable tools |
| Persistence | `_mutate_and_save_state`, `_save_state_unlocked`, `_commit_turn_result` | Same-connection state/event/output hooks; caller synchronization after commit |
| Delivery | reply/DM/image callbacks, post-turn hook | One outbox claimant per logical output; reuse narrative receipts/corrections |
| Recovery | async task ownership, existing recovery markers | Immediate admission hold, then durable operation reconciliation |
| Maintenance | detached summary/memory/checkpoints | Same mirror/commit contract; timeline/source invalidation; no foreground summarizer |

## 5. First tranche: concrete correctness and local-cost fixes

### A. Routing and movement (F1/F4)

`RouteDecision` separates `speaker_role`, `message_mode` (IC/OOC/mixed), `turn_kind`, output audience, canonical policy and retrieval plan. Explicit OOC and high-confidence rules questions are safe routes; punctuation alone is not. `（我往右走）` remains an action; `（為什麼要骰？）然後我往左走` stays one request with distinct spans. Unknown language is interpreted in the already scheduled gameplay call. Exact ACK never implicitly spends Luck, rolls or accepts a purchase.

PLAYER_OOC initially has no tools. It answers from public context for public output; authorized self-only information uses private output. Never borrow the broad Keeper read-only tool list (which includes dice) or KP Assistant's unfiltered state. OOC is excluded from canonical log, summary and public memory; optional OOC history has conversation/timeline/recipient scope and a size bound. No retrospective deletion of old logs.

For mixed input, the existing model response may return separate IC/OOC text fields in the same request. The renderer sends an appropriate combined response; only validated IC spans enter canon. Preserve original input and span mapping in scoped audit. Malformed separation does not promote the whole response into canon, replay effects or trigger a fixed repair call; render verified committed facts plus a safe explanatory fallback.

`MovementProposal` is an immutable value: proposal ID, timeline, actor/subject IDs, origin, destination candidate/path, assertion kind, original span, map/source version and evidence refs. A destination may be absent. Source spans are data, not instructions. Stage A can use an in-turn ID; restart-safe IDs arrive with the ledger and must not be claimed earlier.

- Parse observation, negation, question, quotation and action clauses independently. Keep original text. Never fix this with a global “contains 不” veto.
- Graph lookup proves candidate topology only. Imported maps currently lack authoritative lock/trigger conditions; absent fields do not prove free passage. Named rooms and RAG hits are candidates, not teleport permission.
- No-map scenarios continue through normal scenario adjudication. Do not create map nodes merely to satisfy the new interface. Record narrative location only when scenario/play evidence supports it.
- Use known legal multi-edge paths without forcing one message per room. Stop at actual choices, checks and scenario reactions. Unknown necessary conditions stay with the existing Executor.
- Commit once after current timeline, active character, origin, source version, action authorization and applicable final check/Luck outcome are verified. Another actor's independent pending is not a blanket prohibition.
- A final Executor response can carry a movement proposal decision only without remaining arrival-dependent mechanisms and with the reaction point conditions/evidence verified (section 12.1). It is untrusted until validated; local commit produces the event before resolution is marked completed and before Narrator. Do not execute malformed/incomplete JSON or invent a new fixed completion call.
- Refresh the entire caller snapshot from the committed state. An unrelated summary revision may trigger revalidation, not overwrite or an automatic second move. Sudo carries the real actor and subject separately.
- A failed post-completion move validation produces a truthful incomplete/blocked result; any extra lookup required to fix old incorrect behavior is reported separately from ordinary latency comparisons.

### B. Partial effects and final output (F2-A/P8)

Keep `TurnResolution.disposition`. Add a distinct execution health dimension (`completed`, `partial`, `failed`, `recovery_required`) and structured observations. Lifecycle `accepted/running` belongs to turn persistence, not final resolution.

`ToolExecutionContext` carries trusted actor, subject, timeline, entry kind and authorized check/decision identity. The adapter strips model-supplied private authority fields and injects server values. Unknown target/quantity/action remains model input subject to domain validation; cross-character tools must remain possible where authorized.

`OperationOutcome` contains effect class (pure read/random/state/output intent), observed success/rejection/unknown, tool identity, verified events, pending changes, scoped output intentions and failure code. First-tranche observations are in-process only. Do not infer cause solely from a state delta or expose raw errors/private results publicly.

Use one result builder for normal/exception paths. Preserve successful observed effects, existing pending/Luck and dice results. Partial is not PURE_ROLEPLAY and not complete. With budget available use the existing Narrator once; otherwise render safe committed facts and remaining work. Update the incomplete consistency renderer too, or the final warning will erase the repaired handoff again.

Order all final text transformations before the final audience/spoiler check. If Guard changes text, recheck mechanical invariants deterministically before the final audience check; do not add a fixed model repair loop. The current Supervisor checks spoilers before a later mechanic rewrite: regression tests must exercise protected content introduced by a deterministic fallback. Private/image outputs also require receiver-specific validation.

Cancellation propagates after accounting for the worker. A tracked mutating worker that outlives grace keeps an immediate in-process hold on affected gameplay until reconciled; asynchronous marker persistence alone cannot close that window. Pure read queries remain possible. This tranche must not claim restart-safe reconciliation without the ledger.

### C. Exact mirror writes and commit semantics (F6-A/P5)

Extract a pure mirror projection preserving owner and character-ID aliases, retired/manual cards and existing ordering. In the same write transaction, read old group payload, derive exact old/new keys, fetch only those keys and compare actual serialized rows against expected new rows. Delete only proven owned stale keys; upsert changed/missing rows; unchanged rows keep `updated_at`. Comparing old/new projections alone is insufficient to repair a missing mirror.

Remove hot-path whole-table scans. Legacy orphan discovery is an explicit one-time dry-run/repair, never a per-save operation; ambiguous ownership is preserved and reported. Membership indexing is deferred unless orphan management/query requirements justify it. No unescaped prefix `LIKE` ownership queries.

Prepare revision/timeline and final serialized payload locally, reuse serialization for byte metrics, then synchronize caller state only after the outer transaction succeeds. Cover maintenance's direct `_save_state_unlocked` call as well as normal saves. Preserve revision conflicts, atomic group/mirror writes and `newgame` cleanup. Unknown mutating fields retain full before/after snapshot validation.

## 6. Second tranche: reduce repeated work without shortening gameplay

1. **Routing before enrichment (F5):** pure hints + latest state choose skip/proactive/tool-only per source. Preserve Scenario/Memory concurrency and independent source outcomes. Only skip provably unnecessary retrieval; unknown gameplay retains current policy. A faster context builder that causes more tool/model rounds is a failed optimization.
2. **Request-scoped history/prompt reuse (P4):** share selected history and counts across retrieval admission/provider assembly for one immutable history/model/tokenizer/budget/min-turn key. PR #94's shared policy is already implemented. Do not reuse by mutable object identity, omit tools/tool outputs, or assume sum-of-fragment BPE counts equals whole serialization. Verify the final candidate exactly; bound caches, and avoid hashing the full history repeatedly just to save tokenization.
3. **Eligible legacy RAG grouping (P3):** group only after chapter/visibility filtering, preserve order, de-duplication and budget markers. v4 required-fragment traversal/continuations stay unchanged. No NumPy/heap rewrite without a measured hotspot and ranking-equivalence tests.
4. **Versioned evidence (P2):** store source/variant/record/span/visibility/actor/action/check references with completeness requirements, not a whole old prompt. On follow-up reauthorize and rehydrate source, refresh dynamic state and retrieve newly needed branches. Missing/partial/expired evidence never counts as complete. PR #94 cursors are query/history-bound, process-local paging tokens; they are not persistent check references.
5. **Stage prompts (F7/P8):** first extract identical shared sections, then remove only irrelevant tool instructions/raw duplicates from ordinary Narrator. `NarrationRequirements` includes original intent, verified disposition/health/events, pending per actor, committed scene, permitted evidence and private output recipients. Preserve style, ordinary-item policy and all scenario mechanics. A required section that does not fit cannot silently disappear.
6. **Pure read path (F8-A):** audit status/sheet/help/where handlers and post hooks for mutation, button claims and receipts before allowlisting. Read group and turn status in one short transaction, off the event loop, return an independent projected snapshot; label a running turn naturally. Preserve gameplay locks. Bounded workers and ownership avoid abandoned writes and foreground starvation.

These are independently measurable changes, not one toggle that also changes model, history policy, tool set and output length.

## 7. Later tranche: durable operations and delivery

### Operation persistence (F2-B)

Use trusted transport event identity, not message text. Proposed records:

| Record | Stable keys / required data |
| --- | --- |
| Turn | `turn_id`; unique `(platform, event_kind, source_event_id)`; conversation/timeline, actor/subject, lifecycle, resume policy |
| Operation | `operation_id`, turn, native call ID plus response identity or dispatch ordinal, input digest, effect class, status, saved result |
| Committed event | event ID; unique `(operation_id, effect_index)`; affected IDs, payload, visibility/recipient, source/version |
| Output | output ID; unique `(turn_id, logical_key, part_index)`; immutable payload/hash, destination, stream order/dependencies, status/lease/receipt |

A reused operation ID with different input is rejected. Two distinct calls with identical inputs remain distinct actions. Transport redelivery resumes/checks the original turn; it must not dispatch a second Executor. Legacy internal callers without transport IDs cannot claim transport-level deduplication.

Persist operation identity before dispatch. For local mutation, use a same-connection transaction: verify operation/timeline/authority -> load latest -> domain mutation -> group/mirror diff + operation outcome + events -> commit -> publish result/synchronize caller. Random outcomes are persisted before exposure and reused on retry. Audit tools with multiple saves (including resolved-check event writes); combine their transactions or represent stable child operations with partial parent status. No network await within the transaction.

Private/image intentions also need durable events before final narration, closing the “tool committed, Narrator not yet committed” gap. On restart, reconcile local prepared/committed states; resume tool-free rendering/delivery only from known receipts under an independent RecoveryMode (section 12.2). Unknown external operations are not blindly replayed. Reuse existing check, purchase and correction identities.

### Immediate recoverable delivery (F3/F8-B)

Canonical text, final outcome and output drafts share a short commit; stable pending decisions and deterministic roll feedback may commit their outputs earlier at the existing safe boundary. Freeze split text, recipient and versioned asset hashes before enqueue. Never retain a Python callback, mutable page number alone, or an ephemeral Interaction token as a durable destination.

First rollout keeps current inline timing: commit -> immediate claim -> send -> receipt. A bounded recovery worker handles unsent rows; polling is only a recovery safety net. Sender/hook/worker share one logical-key claim, preventing double delivery. Reuse narrative receipts so correction message IDs remain valid.

State machine: `pending -> sending -> sent`; explicitly unsent transient failure -> `retry_wait`; permission/asset failure -> `blocked`; potentially accepted HTTP result -> `uncertain`; obsolete unsent timeline -> `cancelled`. Lease-token compare-and-set prevents late workers overwriting a newer claim. Payload mismatch under an existing output key is an error.

Later separate ordered streams for required public text, actor-private decisions and supplemental output with bounded cross-stream concurrency. Barriers apply only to dependent actions; unrelated players/groups and pure reads do not wait on a decorative image. No read acknowledgement is added. Rollback stops new send admission, coordinates bounded in-flight sends and cancels old unsent rows. Late uncertain sends need receipts/compensation; DB validation alone cannot atomically fence an external HTTP delivery.

Discord's nonce mechanism is bounded to recently sent messages; it is not a permanent exactly-once guarantee. Verify installed SDK support before relying on it. See [Discord Create Message](https://docs.discord.com/developers/resources/message#create-message).

### Migration and rollback

Add separately versioned relational migrations; do not insert relational table names into the existing generic `key/data` `_TABLES` loop. Repositories enforce identity, ownership and status transitions; any FK choice must cover deletion/retention tests. Existing SQLite backup must include new tables; asset retention must preserve versions referenced by pending output. Use the backup API, not a copy of the main WAL-mode file alone ([SQLite WAL documentation](https://www.sqlite.org/wal.html)).

Pause gameplay/background writers for schema activation; dry-run any legacy mirror cleanup; restore-test a disposable backup. Enable new turn recovery only from the cutover, without deriving old operations from narrative text. Keep compact deduplication tombstones under an explicit retention window; never GC unresolved operations or referenced assets.

Performance flags may restore fuller prompts/retrieval or slower reads. They must not re-enable pre-adjudication movement, OOC canon pollution or unsafe replay. Outbox rollback requires a safe drain/pause and one sender, not an immediate return to a competing legacy helper. One gameplay owner per conversation remains the deployment assumption.

## 8. Deferred completion optimization (P6/P7)

An atomic transfer service may reuse existing inventory rules once source, receiver, quantity and authorization are known. Combat subflows reuse existing services and stop at all check/Luck/defense choices. Tool batching alone does not prove fewer LLM requests.

`TurnBoundaryGuard` is **not implemented or enabled by this proposal's first tranches**. A later experiment needs registered complete workflows, trusted entry IDs, full input coverage, all calls in the provider response accounted for, committed/rejected receipts, current scope, complete necessary evidence, pending/output intentions and no unknown worker. Boundaries: continue Executor / yield to existing player choice / ready for Narrator / recovery required.

Do not exit on one successful call, one pending check belonging to another actor, a lookup hit, a model's `done` field or a partial response. Evaluate all calls in a batch without blindly executing those whose prerequisites are unresolved. General free narrative stays on the existing continuation path. Use typed local receipts, not fabricated model JSON; reuse authority validators across all providers.

Shadow mode only computes the hypothetical boundary while the existing Executor continues; it executes no second side effect or LLM evaluation by default. A later necessary lookup/mechanic/private output is a premature-stop counterexample. Zero observed mismatches is not proof of story completeness. Require separate review before enabling a specific workflow.

## 9. Implementation sequence and gates

| Package | Scope | Depends on | Exit gate |
| --- | --- | --- | --- |
| S0 | Baseline fixtures, request/interaction trace | Existing observability | Reproducible correct-task baseline and bug counterexamples |
| S1 | F2-A/P8 partial facts + final output contract + all-entry worker hold (R3/R4) | S0 | Failure preserves real outcomes; no extra success-path LLM |
| S2 | F4/F1 mode, arrival dependencies, shared commit and mixed segments (R1/R5) | S0; integrates S1 outcomes | Ordinary/sudo/no-map/check routes covered; no routine extra input |
| S3 | F6-A exact-key mirrors + post-commit caller sync | S0 | No whole-character scan; log-only mirror writes zero; commit-failure tests |
| S4 | P1/F5/P2 known authority, routing and evidence reuse | S1/S2 | Same mechanics/privacy; fewer redundant requests or demonstrated local benefit |
| S5 | P3/P4/F7 grouping, history reuse, stage projection | S1; retain PR #94 | Exact selection/ranking and narrative-quality gates |
| S6 | F8-A audited pure reads and bounded I/O | S3 and task ownership | Reads independent of model wait; same-conversation mutations serialized |
| S7 | F2-B durable ledger + transaction integration | S1/S3 and all mutator audit | Commit/crash/idempotency fault matrix passes |
| S8 | F3 immediate inline outbox | S7 | No duplicate sends; existing button timing; restart delivery only |
| S9 | F8-B independent delivery streams/barriers | S8 | Ordering, rollback race and slow-stream isolation pass |
| Later | Optional atomic workflows/early-boundary shadow | Separate approval, evidence and tests | No hidden gameplay, privacy or narrative regression |

Implement each package as a reviewable change; do not merge all architecture into one performance patch. S1 and S3 are the smallest independently valuable starts. This document authorizes no implementation by itself.

## 10. Tests and performance evaluation

Proposed tests are not claimed to exist/pass. Extend current suites where they already test the contract; create new files only for distinct behavior.

| Area | Required cases | Existing coverage to extend / proposed new suite |
| --- | --- | --- |
| Movement | Six baseline probes; positive right/entry; no-map; legal multi-edge route; locked/triggered route; two actors; sudo; stale map/timeline; check success with Luck pending | New movement proposal/commit tests; unified-turn and priority integration tests |
| OOC/mixed | Full/half-width/multiline question; action parentheses; quoted NPC speech; mixed input one request; no privilege escalation; self-private vs public context; malformed structured reply | New player OOC routing/visibility tests; KP Assistant and spoiler suites |
| Partial | Item/HP/check success then provider error; Narrator failure; rejected tool; private output; known dice; worker beyond grace; no false cause from a raw diff | `test_turn_consistency_handoff.py`, `test_narrator_check_consistency.py`, async provider contract |
| Final safety | Mechanic fallback introduces protected name/text; Guard rewrites; incomplete summary preserves visible effects | Narrator consistency + spoiler policy tests |
| Persistence | Log-only zero mirror writes; exact alias changes; missing mirror repair; other-group prefix collision; retirement/newgame; stale revision; outer commit failure; maintenance caller | `test_state_persistence.py`, state-loss tests; new mirror diff tests |
| RAG/history | v3 grouping equivalence/order/scopes; v4 required rules/cursors unchanged; long history selected identically; updated source/check/actor invalidates evidence; original-source lookup retained | `test_scenario_authoring.py`, scenario template/RAG tests, input budget tests |
| Read/concurrency | Provider blocked on Event but status returns committed view; hidden handler save rejected; two mutations serialize; cancellation/shutdown; background worker saturation | Conversation/priority tests; new read snapshot/worker lifecycle tests |
| Ledger | Same operation replay vs two same-input operations; state/event atomicity; commit-before-return crash; persisted dice; private intention before Narrator; native ID collisions | New ledger atomicity/recovery tests + resolved-check/purchase suites |
| Delivery | Multipart failure; DM rejection never public; accepted-then-timeout; ack failure; stale lease; stale asset/timeline/button; immediate claim without polling; slow stream isolation | Pending button/receipt/correction tests + new outbox/delivery tests |
| Completion boundary | Search-only, pending other actor, damage not applied, required CON, multiple unfinished actions, incomplete provider response, unknown worker | Addendum C01–C20 mapped into the later experiment; no early stop in initial packages |

Integration tests use temporary DB/data/import/backup paths configured before importing `app.db`, fake transport/provider, deterministic dice and Events/fake clock. Python cancellation/ownership rules follow the installed runtime; retained tasks must be observed and reconciled ([Python asyncio documentation](https://docs.python.org/3/library/asyncio-task.html)). Fault injection never targets production.

Performance report must separate **local CPU/DB**, **retrieval/embedding**, **actual provider attempts/continuations**, **admission/retry**, **queue**, and **delivery**. Include input/output/cached tokens, mirror reads/writes, full-turn success, mechanism/tool accuracy, missing facts/privacy, fallback/repair/clarification and player actions per complete task. Measure transport defer, first meaningful result, button visible/effective, dice feedback and full required narration.

Compare baseline and one change at a time with identical scenario/state/input/model/reasoning, separate cold/warm and single/multiple groups; interleave real API runs to reduce time-of-day bias. Count failures/timeouts rather than dropping them. Legacy/current API trials are context, not a new baseline. Use 1/10/100/1000 synthetic groups for mirror scaling with fixed target-group size; report examined rows/SQL and repeated timing, not arbitrary speed claims.

Before a later live evaluation, specify fixture count, model, request/cost cap and statistical plan. No real API calls are authorized or performed in this spec task. Pre-register tolerances from baseline noise; an inconclusive interval is inconclusive. Success-path fixed LLM stages and normal player decisions must not increase. Required work newly added to repair a bug is a separately explained case, not hidden in the ordinary performance comparison.

## 11. Decisions for design review and existing contracts

Recommended scope: approve S0–S3 first, then measure and review S4–S6; keep S7–S9 as staged recovery architecture. Early stopping remains off. No decision on a membership table, NumPy, model changes or new deployment services is needed for S0–S3.

Before S7/S8 implementation, finalize transport identity plumbing for every entry, durable payload retention, maximum recovery age, blocked/uncertain-delivery operator UX, and installed SDK capabilities. The safe default for uncertain private delivery is reconciliation without public disclosure or gameplay replay. Record unresolved coverage per work package rather than representing the entire proposal as complete.

Related contracts: [unified turns](unified_keeper_turn_flow_design_spec.md), [turn resolution](../bug/log_backed_turn_consistency_design_spec.md), [narrative boundaries](../enhancement/narrative_boundaries_design_spec.md), [atomic purchases](../bug/purchase_turn_provenance_design_spec.md), [token admission](../enhancement/token_admission_evaluation_design_spec.md), [external authoring/PR #94](../enhancement/external_template_authoring_design_spec.md), [state persistence](../feature/state_persistence_design_spec.md). Dynamic tools and macro combat remain separate backlog specs.

### Source navigation

The findings above were checked in [router](../../../app/commands/router.py), [movement parser](../../../app/intent_parser.py), [legacy adapters](../../../app/legacy_commands.py), [Supervisor](../../../app/agents/supervisor.py), [Executor](../../../app/agents/executor.py), [tool gateway](../../../app/agents/tool_gateway.py), [intent router](../../../app/agents/intent_router.py), [Narrator](../../../app/agents/narrator.py), [resolution](../../../app/services/turn_resolution.py), [prompt policy](../../../app/services/prompt_config.py), [group repository](../../../app/repositories/group_state.py), [DB](../../../app/db.py), [Keeper services](../../../app/keeper.py), [RAG](../../../app/scenario_rag.py), [v4 retrieval](../../../app/scenario_retrieval.py), [history selection](../../../app/services/input_budget.py) and [embedding cache](../../../app/embedding_cache.py). Paths point to the checked-out source; the baseline commit above fixes the review's historical meaning.

## 12. Review decisions, 2026-09-27 (R1–R6)

Reviewed `turn_safety_and_latency_spec_review.md` (SHA-256: `22e00d613e144f40da08e0857932d8fd947f749b87cfbcaf98c6f62ca8baa6b4`) against latest `main_v2` `e03d5dc`. The review fixed the spec at `8de40eb`; this section is a subsequent design revision, still awaiting approval. It implements no runtime behavior and claims no new SR tests passed. PRs #95/#96 changed authoring files/import recognition, not the Supervisor/Narrator/gateway/spoiler core checked here. The original 940-test result is historical; the import fix's 987 tests are not acceptance evidence for this refactor.

| Review | Decision | Required package |
| --- | --- | --- |
| R1 Movement causality and evidence entry | Adopt; restrict final-response commits and define arrival dependencies | S2 |
| R2 Recovery capabilities | Adopt; separate RecoveryMode from turn_kind; disable render-only tools at two boundaries | S7/S8; no automatic tool-enabled model restart in S1 |
| R3 Shared mutation admission | Adopt; hold ownership and effect class govern execution | S1, extended by S6/S7 |
| R4 Operable final output | Adopt; server delivery envelope and bounded fallback | S1 |
| R5 Mixed IC/OOC | Adopt with separate types for model candidates and server projections | S2 |
| R6 Per-route request ledger | Adopt; deterministic traces and real-model distributions have separate gates | S0 and every subsequent package |

These clauses make sections 4–10 concrete. Where a simplified diagram could imply that all movement follows every tool or recovery always calls the original Narrator, the restrictions below take precedence. Normal gameplay capabilities and the disabled early-stop policy remain unchanged.

### 12.1 R1: valid arrival precedes arrival-dependent effects

“Enter the study and take the letter” requires validated, committed arrival before acquiring the indoor item. “Eat carried rations, then try entering the study” may preserve the independently completed consumption. Do not globally reorder by tool name or roll back every earlier legitimate effect when movement fails.

- Arrival dependencies reference server-verified current location or a committed arrival event for the same action. A MovementProposal, RAG match or model-provided `requires_arrival=false` is not authorization. Apply this to relevant item, flag, clue, check, resource and output effects without binding every carried-item operation to a new destination.
- When mechanisms must follow arrival, commit movement through the shared service inside the existing Executor tool sequence, refresh full state, then execute dependent effects. Unresolved check/Luck stops at the existing player choice. Preserve necessary model continuation; do not introduce a fixed “confirm movement” request.
- Final-response movement commit is only available when **no arrival-dependent mechanism remains unexecuted** and all conditions/evidence at the current legal reaction point are verified. The existing Narrator may then proceed. Required remaining tools invalidate this shortcut; it must not become early stopping or delegate mechanics to the tool-free Narrator.
- Shared `MutationAdmission` covers ordinary, sudo, explicit map commands, final-response services and resolved-check follow-up. Validate authority, timeline, actor/subject, origin/source, correction/recovery holds and action-scoped required evidence. Checking only the outer gateway is insufficient; matching versions do not prove complete evidence.
- Bind continuations to action/proposal/check/decision IDs, actor/subject, origin/path/source version and approved outcome conditions. Spot Hidden success is not lock-opening success; unresolved Luck is not final. Never reroll known dice; changed source/path requires adjudicating the necessary changed prerequisites.
- Inventory the location-sensitive domain mutators and their prerequisite sources in the first implementation. Model dependency metadata is a proposal. Unknown narrative prerequisites retain existing retrieval/adjudication or incomplete status; Python cannot claim universal understanding of free-form story causality. Mapless scenarios use supported narrative locations without mandatory graph creation.
- Location/arrival events and dependent effects have explicit transaction boundaries; no DB transaction spans an LLM wait.

```text
"Enter the study, then take the letter"
  -> MovementProposal (no location write)
  -> existing Executor retrieves conditions
       +-> unresolved check / Luck: preserve waiting; no indoor item
       `-> shared admission -> commit movement + arrival event
             -> refresh state -> validate item arrival dependency -> item event
  -> resolution -> existing Narrator

"Move, then narration only"
  -> Executor final candidate -> same admission / evidence validation
  -> movement + arrival event -> resolution -> existing Narrator
```

Acceptance uses **SR-M01–M07**: locked door blocks indoor acquisition; final entry cannot bypass evidence; move event precedes dependent item event; rations remain consumed; Luck waits; observation does not authorize entry; valid mapless movement adds no routine confirmation.

### 12.2 R2: recovery mode is independent of the original turn kind

| Mode | Model/tool capabilities | Behavior |
| --- | --- | --- |
| Normal ordinary Narrator | Existing tool-free narration | Unaffected by recovery restrictions |
| Normal resolved/opening Narrator | Existing restricted tools | Legitimate follow-up remains available |
| `delivery_only` | Zero generation requests and gameplay tools | Claim/send sealed payload |
| `render_only` | At most the necessary existing narration stage; force `tools=[]` and reject all gameplay tools in callback | Render verified events/receipts projected for the recipient |
| `reconcile_required` | No whole Executor or tool-enabled Narrator restart | Determine committed, unexecuted and uncertain effects first |

Server-owned `RecoveryMode` takes precedence over `turn_kind`; an original resolved-check route cannot reopen damage or advance tools during rendering recovery. Targeted resumption is allowed only for registered workflows with durable trusted action/step identity, a proven missing step and current authorization. Unsupported free-form recovery remains partial with completed and unresolved facts.

Operation retry returns its saved result. A newly generated call ID cannot claim an already completed logical step. Global tool-name/input-hash deduplication is also invalid: two legitimate same-input attacks are distinct effects. Fixed-workflow step identity must distinguish such repetition. First-tranche process-local observations do not promise automatic restart recovery.

Acceptance **SR-R01–R06** covers no repeat damage/advance, missing necessary steps remaining partial, sealed output requiring no model, unchanged normal follow-up capabilities, and a new call ID not replaying a completed logical step.

### 12.3 R3: every mutation entry consumes the hold; only its owner releases it

The MutationAdmission consumer inventory includes Executor, restricted Narrator, check/Luck callbacks, sudo, purchase confirmation, map changes, character switching, and newgame/rollback. S1 requires actual call sites and tests per entry, not just gateway coverage.

Holds carry conversation, timeline, affected scope, task/operation identity and generation/owner token. Cancellation makes the hold visible before releasing relevant gameplay admission; recheck at the transaction or authoritative lock boundary. Only matching owner/generation completion, rejection or reconciliation releases it; an old task's finally cannot clear a newer hold. Timeout is not evidence of completion.

If independence cannot be established, initially hold conversation mutations conservatively; audited pure reads and other groups remain available. Timeline switching waits for safe worker settlement or validates the worker's original expected timeline immediately before writing. An old worker must not reload the new timeline and apply its obsolete effect. Maintenance/background writers also obey timeline, revision and commit ownership.

| Effect class | Execution policy |
| --- | --- |
| Pure read | Independent snapshot with no shared-state refresh; waiting may stop but task remains owned/observed |
| Random outcome | Preserve known results; reconcile uncertainty rather than rerolling the same operation |
| State mutation | Retain ownership/hold while commit is uncertain; reject conflicting writes |
| Output intent | Stable logical key and recipient; reuse intentions rather than lose/recreate critical output on cancellation |

Multi-effect tools obey all applicable constraints. Shared-state refresh or random output prevents a tool from inheriting pure-read cancellation/retry semantics merely because it belongs to READ_ONLY_TOOL_NAMES. Slow decorative delivery does not create an unbounded conversation hold; S8/S9 manage output dependencies/barriers separately.

Block workers with Events and attempt conflicting mutations through each entry. Test responsive reads/other groups, stale owners unable to clear newer holds, no known-dice rerolls, and shutdown not falsely claiming workers stopped. Distinguish process-local safety from S7 durable recovery.

### 12.4 R4: final safety preserves legitimate results and effective controls

Python builds a `DeliveryEnvelope`: output ID, audience/recipient, authorized fact/event references, narrative, mechanical feedback, interaction references and canonical policy. S1 can wrap existing string responses without changing every provider or adding a model request.

```text
Candidate narrative + server-projected facts / controls
  -> mechanism consistency -> existing conditional Guard -> final audience/spoiler check
  -> validate_delivery_contract (validation only; no rewriting)
       +-> pass: persist / send
       `-> fail: one deterministic projected facts / controls fallback
             -> identical safety and contract checks
                  +-> pass: persist / send
                  `-> fail: blocked; preserve pending/results; safe notice/reconciliation
```

The contract checks required recipient-visible hard facts and controls, not full narrative semantics through keywords. Buttons retain original check/decision IDs, owner and timeline; do not recreate pending state or duplicate controls. Private pending requirements belong to private outputs rather than forcing public disclosure. Every fallback passes the same safety checks, without fixed LLM repair or unbounded rewriting. Passing narrative retains its style. S1 records delivery-contract outcomes; only S8 supplies durable outbox recovery.

Test protected text plus legitimate pending, private pending, Guard rewrites, an unsafe fallback, and unchanged valid narration. “No disclosure but no usable next step” is not success.

### 12.5 R5: separate model candidate segments from server output projection

Do not expose writable server_output_projection inside a model-populated schema. Use distinct types:

```text
ModelReplySegments (mixed mode only; model candidates)
  schema_version
  segments[]: text, source_request_span_refs, proposed_mode, proposed_event_refs

DeliveryEnvelope (Python-owned; not model-writable)
  request_id / turn_id / output_id
  audience / recipient
  verified facts / interaction refs
  final text / canonical policy
```

Mixed segmentation comes from the **already scheduled final Narrator response**. Executor retains mechanisms/resolution responsibility; there is no second OOC agent. Ordinary mode preserves the string interface through a server adapter. Tool-enabled follow-up, where mixed content applies, uses its existing final response without a second narration stage.

Server policy determines speaker role, capabilities, context visibility, recipients and canon. OOC assertions cannot become established facts during input/context/action handoff; handling “I already have the key” only at final commit is too late. Validate input/output span and event references for existence, range, applicability and permission. Without support, never canonize the entire segment. These structural checks do not prove complete natural-language semantics.

Compute canonical and delivery projections separately. One request may yield public IC and self-private OOC outputs without asking the player to split the message or forcing all text into public output. Filter context before generation; model segmentation alone cannot ensure confidentiality.

Malformed/uncertain coverage uses R4 verified outcomes and safe unresolved information. Do not commit the entire raw input/reply into canon, replay tools, or add fixed JSON repair. Clarify genuine unresolved ambiguity only, accounting for quality/player-action cost rather than asking routinely to avoid parsing.

Test mixed rules-plus-movement, OOC ownership assertions, private OOC/public action, forged role/recipient/canonical fields, and malformed/missing segments. Check user log, assistant log, summary and memory projections separately, not just visible rendering.

### 12.6 R6: request and experience accounting per entry

For each fixed fixture, S0 records route, input/state/scenario/model/reasoning, generation requests, provider attempts/retries, Executor/Narrator tool-response rounds, embedding/search, queue, Guard/repair/wrap-up, effective buttons, dice/full-required-narrative timing and required player decisions.

These are basic shapes, **not hard caps or permission to omit work**. Existing conditional Guard, retries and wrap-up are itemized and included in totals; special gameplay uses the fixture's correct complete behavior as its baseline.

| Route | Basic generation shape | Constraint |
| --- | --- | --- |
| Audited pure-read status/sheet | 0 | No model introduced |
| ACK / pure roleplay | 1 Narrator | Preserve an appropriate reply |
| Gameplay without tools | 1 Executor + 1 Narrator | No fixed classifier/judge |
| Gameplay with tools | b+1 Executor + 1 Narrator | b counts tool-response rounds; no unnecessary confirmation round |
| Resolved check / Luck | k+1 restricted Narrator | k counts follow-up tool-response rounds; no second agent chain |
| Opening fallback | k+1 restricted Narrator | Still reuse an existing opening directly |
| delivery_only | 0 | Send sealed content without regeneration |

Mixed/OOC gets dedicated fixtures based on the actual existing route and corrected behavior; a renamed route is not proof of fewer requests. Fake providers verify deterministic traces; live trials compare per-route distributions, continuation/fallback/repair rates and predeclared non-inferiority margins. Identical model choices on every run cannot be guaranteed.

Report per-route sample counts, success/partial/timeout/fallback, generation/attempts, p50/p95 where sample sizes support them and confidence intervals. Bug-fix work must explain the original incorrect and corrected flows plus player cost; it is not a blanket exception for ordinary regressions. Count failures, incomplete narration, manual intervention and effective button time. Do not promise seconds/percentages without measurements; this review authorizes no live API calls.

### 12.7 Revised sequencing and discussion points

Start with S0; S1 includes R3/R4 completely, while S3 can proceed independently. S2 requires R1/R5 movement dependencies and segmented commit contracts first. Measure S4–S6 separately; S7/S8 implement durable recovery using R2's capability matrix, then S9 separates delivery streams. Early stopping remains disabled.

The first reviewable implementation scope should be S0 baselines plus S1 partial-success/safe-output work. Splitting PRs must not omit hold entry coverage. If S1 is split, label which changes provide partial facts and which finish mutation admission; do not claim all of S1 complete early. S3 and S2 can follow without bundling every schema and behavioral change into one patch.


## 13. Approved S0/S1 implementation evidence (2026-09-27)

Branch: `refactor/turn-safety-s0-s1`, based on reviewed design `36fb67d` aligned with `main_v2` `e03d5dc`. S3 was integrated independently. Section 14 records the historical S2 implementation, which was subsequently reverted with PR #99. S4–S9 and early stopping remain deferred.

### 13.1 S0 reproducible baseline and trace

`tests/test_task_trace.py` fixes the input, one-character state, timeline and SDK responses; scenario/retrieval is intentionally absent. It executes real provider adapter loops with an in-memory SDK double. Intent is controlled to isolate each path. Ordinary and sudo cases also execute the command router. Provider keys are fake, the DB is temporary, and the production `.env`, files and APIs are not used.

| Fixture | Generation requests / attempts | Tool-response rounds | Fixed extra Guard/review/wrap-up |
| --- | --- | --- | --- |
| Roleplay | 1 / 1 | 0 | 0 |
| Gameplay: obtain item, complete, narrate | 3 / 3 | Executor 1 | 0 |
| Ordinary router: same gameplay | 3 / 3 | Executor 1 | 0 |
| Sudo router: same gameplay | 3 / 3 | Executor 1 | 0 |
| Opening fallback, supplied context | 1 / 1 | 0 | 0 |
| Resolved-check narration, no additional tool required | 1 / 1 | 0 | 0 |

A retry fixture distinguishes one logical generation request from two provider attempts. Fault fixtures separately cover successful Executor tool + failed continuation, restricted Narrator tool + failed narration, pending check/Luck preservation, stale identities, private controls and running-thread cancellation.

`task.trace` aggregates request/interaction events: entry and route, provider/model/reasoning, logical requests, attempts/retries, tool rounds by stage, embedding/retrieval, queue wait, Guard/wrap-up, confirmed dice readiness, narration readiness, fallback readiness, controls actually sent, pending decision count and request elapsed time. A fallback is not recorded as full narration readiness. IDs/prompt/user text are not added to the trace. Mechanical accuracy and manual intervention are explicitly unknown at runtime; they require fixture assertions or a live evaluation. SDK fixtures prove structure and deterministic mechanism outcomes, **not real-model accuracy, latency, or narrative quality**. Visible-button timing is measured on successful Discord sends; it does not prove a player clicked it.

### 13.2 Partial success and final delivery

`ObservedOutcome` records tool evidence before worker settlement. `MechanicResult.execution_health` is separate from validated action disposition. Both successful and failed Executor continuations use the same result builder; actual facts, check/Luck status and inventory events survive. A snapshot difference alone never manufactures an outcome, and no tool is replayed to repair narration. Restricted Narrator tools append to the same evidence collection.

Python creates a `DeliveryEnvelope` with output identity, audience/recipient, authorized outcomes, original check/decision identities and canonical policy. Public fallback uses allowlisted result projections; raw RAG, errors, private messages and enemy sheets are not dumped. Known results and pending interactions remain intact. Private controls go to the owner and retain the original button identity. Valid narration is retained with server-projected mechanical feedback where required.

```text
entry -> admission + existing actor/subject authorization
  -> current Executor / restricted Narrator tools
       -> owner(original timeline, generation) -> actual worker
       -> committed tool evidence -> ObservedOutcome (no replay)
  -> existing Narrator
  -> mechanism consistency -> existing conditional Guard -> consistency recheck
  -> server facts + original pending controls
  -> final spoiler/audience check -> validate_delivery_contract
       pass -> normal commit/delivery
       fail -> one deterministic projection -> same safety/contract checks
                 pass -> projected fallback
                 fail -> blocked notice; preserve real results and pending state
```

### 13.3 Admission entry coverage

The hold is conversation-scoped until independence can be proven. Existing actor authorization, subject binding, correction holds and action-evidence checks remain in their original handlers. `MutationAdmission` adds lifecycle ownership, not new permissions. All guarded entries recheck at the authoritative write boundary; only the actual worker settles its own generation. A cancelled Task cannot settle a running thread. Queued work proven not to have started is rejected and cannot start later.

| Entry / trusted principal | Admission call site | Pre-write / release rule | Regression evidence |
| --- | --- | --- | --- |
| Ordinary and sudo / actual sender + authorized subject | `router.handle_text_message`, conversation lock, `supervisor.run_turn` | Existing sudo authorization; group/timeline gate before write | router mutation matrix; real adapter ordinary/sudo baselines |
| Executor / trusted speaker role + tool target | `run_executor`, `tool_gateway.make_tool_executor`, `keeper._execute_tool` | `_mutate_and_save_state` checks original timeline before mutator; owner generation settles in worker finally | tool success then API failure; blocked dice; stale worker timeline |
| Restricted Narrator, opening, resolved follow-up / original player | `run_narrator`, Supervisor, same gateway | Offered-tool allowlist unchanged; same worker/write gate | restricted-tool failure; all turn kinds held before context |
| Check/Luck button or command / persisted decision owner | observed callback + conversation lock; guarded legacy handlers; deterministic resolver | Reject before dice, then normal identity/owner/timeline checks | authoritative entry matrix; callback hold; original check/Luck IDs |
| Purchase confirmation / authenticated buyer or authorized sudo subject | guarded purchase handler | `_mutate_and_save_state`, then group DB gate | direct purchase entry + router matrix; existing purchase tests |
| Map change / current player | guarded map handler; `_resolve_map_action_transaction` | State lock admission before resolving movement | direct map + deterministic entry matrix; S2 movement redesign deferred |
| Character switching / character owner | guarded character handler | `save_state` and DB admission | direct switch entry + router matrix |
| Newgame / existing command authorization | guarded system handler + conversation lock | Hold checked even when revision replacement is intentional | direct newgame and repository matrix |
| Rollback / existing KP authorization | system handler + `checkpoints.rollback` | Admission before checkpoint transaction; scoped DB writes/deletes recheck | rollback entry matrix |
| Correction and uploads / existing handler principal | guarded correction/PDF/map/role handlers | Conversation lock and scoped persistence gate | direct correction/upload matrix |
| Memory and digest maintenance / original source snapshot | `_persist_memory_maintenance_state`, `run_scene_digest_maintenance`, `create_digest` | Hold + existing timeline/summary/log guards; digest source revision/timeline checked before write | maintenance hold, stale digest rejection, old digest cleanup |
| Direct persistence / internal caller | `save_state`, scoped `db.set_json[_tx]` and deletes | Recheck conversation hold and owner timeline, including mirror/checkpoint/memory writes | repository newgame hold; maintenance tests |

Audited read commands (`help`, `status`, `characters`, `purchases`) and other conversations remain usable. The read command scope cannot bypass DB write admission. Query timeout remains bounded but retains ownership if its thread is alive; dice are not classified as retryable reads. A hold becomes visible before cancellation releases conversation admission. Late worker evidence is retained in the existing marker list when persistence succeeds, without replay. Shutdown reports actual outstanding worker ownership even if asyncio Tasks have already ended.

**Limits:** holds are process-local, not a durable operation ledger. A stopped worker with an unknown result requires state inspection; there is no automatic tool-enabled recovery or guaranteed re-delivery of late private/output intents. Persistence failure in late evidence recording is logged. S7/S8 remain necessary for restart-safe reconciliation and a durable outbox. These limits are not reported as completed recovery support.

### 13.4 Verification

Isolated complete suite: **1044 passed, 1 skipped, 33 subtests**. Ruff passed; mypy **86 source files passed**; `git diff --check` passed. Baseline was 987 passed, 1 skipped. Existing mechanism, button identity, purchase, correction and provider tests remain enabled. No new fixed model stage, live API run, production data mutation, or deployment was performed.

## PR #97 review corrections

Preserve check visibility and the authoritative owner through resolution, Luck, chained checks, event/context construction and delivery. Route private feedback and images to that owner even when invoked from a public command; DM failures must not fall back to public output. Preserve filtered damage_combatant injury/healing results in recovery without exposing enemy HP. Test deterministic check/Luck resolution, chained SAN/INT checks, split and combined delivery, and failures after combat tool commits.

## 15. S3 implementation evidence (2026-09-27)

Branch: `refactor/state-mirror-s3`. S0/S1 use a separate branch; S2 waits for S0/S1. The approval does not extend to S4–S9 or early stopping.

`character_mirror_projection` derives exact owner/character aliases from serialized group payloads, retaining retired/manual cards and existing ordering. The transaction reads only the union of old/new keys in bounded `IN` batches. It compares the actual rows, so missing mirrors are repaired even when the character data did not change. Unchanged rows retain `updated_at`. A foreign ownership collision rejects the transaction; unknown historical orphans are preserved for an explicit audited migration, not deleted by prefix.

`StateCommit` publishes revision/timeline and success logging only after the outer transaction commits. Normal save, memory maintenance and checkpoint rollback use it; group/mirror/pre-rollback changes remain atomic. State-size metrics reuse the serialized group write; metric-only duplicate serialization was removed. No schema or production migration was run.

```text
state lock -> BEGIN IMMEDIATE -> old group + exact old/new keys
  -> read matching mirrors -> write changed/missing; delete proven stale
  -> group write -> outer COMMIT -> publish StateCommit to caller
       failure: rollback; caller revision/timeline unchanged
```

Verification: isolated full suite **994 passed, 1 skipped, 33 subtests**; Ruff passed; mypy **83 files passed**. Added commit failure, mirror failure/atomicity, missing-alias repair, unchanged timestamps, retired aliases, key collision and bounded-read tests. Existing maintenance/checkpoint tests remain enabled.

Run `python3 scripts/benchmark_state_mirrors.py` for an isolated synthetic probe (20 fixed-size log-only saves per size). Measured 1/10/100/1000 conversations: **2 mirror rows read, 0 written, 0 deleted** at every size; median local transaction times **0.564/0.575/0.584/0.475 ms**. These are local observations, not live API or end-to-end latency claims.

## PR #98 review correction

Non-conflict save exceptions must emit state_save_failure with a hashed group ID, reason, elapsed time and traceback, then propagate the original exception. Text diagnostics remain available with structured logging disabled. Fault injection covers mirror writes, companion transactions and commit failures; rollback and unchanged in-memory revision remain required.
