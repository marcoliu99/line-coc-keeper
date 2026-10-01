# Postmerge ingestion and combat integrity remediation

[繁中](postmerge_ingestion_combat_integrity_design_spec_zh.md)

Status: backlog; decision frontier closed, awaiting final shared-understanding/implementation confirmation. Runtime implementation is not authorized by these interview answers. Design premise: PR155 and PR156 are both integrated into main. The real documentation base is main_v2 at 6024adf (PR156 merged); PR155 was still open at 1c93712 when checked. This document does not claim a combined checkout was executed.

## Source and evidence

Operator review: /Users/marcoliu/Downloads/pr155_pr156_full_code_review.md, dated 2026-10-01, pinned PR155 cf62607 and PR156 897409d. Recheck findings against current heads before treating them as regressions. At PR156 1f89f6b, isolated probes confirm R156-01 (postcombat major-wound CON becomes stale on retry) and R156-02 (malfunctioned owned firearm reaches PLAYER_ROLL). The second probe covers declaration, not a complete malfunction/repair flow. PR155 1c93712 was statically checked: the later native-anchor changes do not alter the R155-01–11 paths; R155-06 is partially repaired by PR156 replacement guards. This is not a fresh dynamic or combined-tree validation. The old merge-conflict observation is integration history, not a remaining postmerge runtime defect.

## Confirmed decisions

- Q1 A: staged delivery. First repair check lifecycle, accepted asset preservation, stale publication and deterministic reading order. Then complete source-version/lifecycle support for new data, remaining domain boundaries and release verification. Each stage has executable acceptance and explicitly tracked remaining findings; no partial fix is presented as complete.
- Q2 A: a running game stays on its explicitly selected scenario version. Another group's reparse does not update it. Switching versions is explicit and coherent across text, images, maps and NPC/character source evidence.

- Q3 superseded: the operator will delete old scenarios before rollout, so historical scenario/source-version migration is out of scope. The cleanup boundary is settled by Q7: old scenarios and their related game progress, drafts, checkpoints and assets. No deletion has been performed by the agent. Newly created data still requires immutable references, safe retries and lifecycle invalidation.
- Q4 A: finish current combat, player checks and Luck before changing selected source version. Outstanding continuing obligations require explicit compatibility handling; this does not authorize clearing them. Future-obligation handling is settled by Q8.
- Q5 A: newgame/new-scenario transitions invalidate the old import worker and its activation binding while retaining the draft, accepted assets and diagnostic evidence. Reuse requires explicit reimport/admission under the new context, never automatic rebinding or stale authoritative publication.
- Q6: excluded at the operator's request. No Python compatibility work, version-requirement change or additional runtime matrix is part of this remediation. Existing verification environments remain as they are.

- Q7 A: the operator will perform a clean reset of old scenarios and related game progress, drafts, checkpoints and image/map assets before rollout. Historical scenario/source-version and old-session recovery migration are excluded. No cleanup or deletion has been executed by the agent. The fresh-start precondition must be verified before rollout; newly created data still needs durable restart/retry contracts.

- Q8 A: an explicit same-scenario version update may retain compatible future obligations after current due checks/waits are resolved. Each retained obligation keeps its original source version, target/ownership, logical timing and roll receipts. Incompatibility blocks the update until explicitly resolved; no reset/free interval/reroll. This does not relax newgame or different-scenario replacement guards.
- Q9 A: repair malfunction admission/execution guards and support only evidence-backed, recorded clearing/rulings. Do not add a full repair skill/duration/turn subsystem. Bind malfunction to stable owned weapon identity, preserve it through persistence/settlement/rollback, and leave other weapons usable. Unsupported NPC malfunction/repair semantics enter NEEDS_RULING.
- Q10 A: clear harmless incidental omissions and evidence-backed possession omissions may use existing correction handling; uncertain or consequential retrospective changes go through the existing review authority. Assess the whole claim, not only the item keyword. Preserve reasonable present-time acquisition and conditional Keeper review from system evidence when no KP Assistant is registered; no fixed per-turn judge or blanket human approval.
- Q11 A: retain all newly published versions in this remediation. No revision pruning/cleanup tool is implemented; existing broad cleanup paths must not silently remove referenced versions. The operator's one-time pre-rollout reset is distinct from normal runtime reset/cancel.

## Preserved contracts

Keep ADR0003 whole-battle provisional resources, bot Keeper settlement/rollback, original dice receipts, due ownership and future obligations. Keep presentation distinct from authority (ADR0002). Do not add a fixed per-turn model judge, automatically clear combat, guess missing source history, or conflate successful extraction/publication with activation in a game. OCR remains import-time work; gameplay consumes validated imported evidence.

## Design foundation

A logical scenario retains its stable identity while published versions remain immutable. Runtime and rollback consumers must refer to a selected version rather than a moving latest pointer. Initial historical-source migration is excluded per the revised Q3; rollout cleanup follows Q7; all new versions are retained under Q11. Publication eligibility must be checked before shared-source side effects; successful game-state commit and failed derived-image refresh must be reported separately.

Tests must exercise restart/retry/failed-save and real repository boundaries. Keep review categories confirmed, already_fixed, not_reproduced and blocked explicit. Test totals quoted in the external review are historical, not validation for this future work.

## Final delivery decision

- Q12 A: use separately reviewable commits/PRs, but complete all scoped repairs and integrated acceptance before the operator's clean-start production rollout. Do not create production data under an intermediate source schema and later require an excluded migration. No data deletion, deployment or additional PR creation is authorized by this interview.

## Finding coverage and evidence

The operator's review remains the reference for its detailed examples; this spec is independently actionable through the contracts and tests below. Static evidence is not relabeled as a newly executed runtime failure.

| Finding | Current evidence | Required repair |
|---|---|---|
| R155-01 | Static at 1c93712; dynamic original-review probe | Symmetric geometry precedence around spanning headings |
| R155-02 | Static at 1c93712 | Complete accepted page snapshots survive later failures |
| R155-03 | Static at 1c93712 | Import ownership bound to lifecycle, not only attempt |
| R155-04 | Static existing source-library debt | Immutable complete source versions and pinned consumers |
| R155-05 | Static existing publication debt | Eligibility preflight before shared-source side effects |
| R155-06 | Partially guarded by PR156; combined behavior untested | Named transition policy and active-history projection |
| R155-07 | Static existing correction debt | Whole-claim consequence/evidence admission |
| R155-08 | Static map admission/validator debt | Shared type-safe graph structure and reference validation |
| R155-09 | Static extraction segmentation path | Classify every source region; never drop card prefix |
| R155-10 | Static at latest PR156 | Safe alias/schema boundaries before normalization |
| R155-11 | Static async import path | One immutable off-loop OCR identity per attempt |
| R156-01 | Dynamic at 1f89f6b | Distinguish major-wound CON from periodic dying |
| R156-02 | Dynamic declaration; execution traced statically | Stable-instance malfunction guards and recorded clearing |
| MERGE-01 | Historical fixed-head catalog conflict | Preserve both spec entries; not a postmerge runtime defect |

Recheck every finding on the actual integrated implementation base. Record confirmed (static/dynamic), already_fixed, not_reproduced or blocked with evidence; absence of a failing probe is not proof of repair. Review observations about safe-short-native coverage and complete action fingerprints remain hypotheses until tests establish a defect. The earlier owned-weapon/NPC-card findings were repaired in PR156 1f89f6b and must remain regression-protected.

## Domain contracts

### Source versions and publication

- Preserve logical scenario identity; a published version fixes text/PDF identity, images, maps, indexes, pregens and extraction provenance as one artifact set. Text hash alone cannot identify it when other artifacts change.
- Active games, pending selections, checkpoints, facts, templates, character/NPC source pins and image refreshes use explicit scenario/version references. A pending choice for V2 installs V2 or fails; publication of V3 never redirects it.
- Build and validate a complete version in staging, then publish immutably. Same-identity retry validates identical artifacts; it cannot overwrite different bytes. Resolve a version once per context load so files cannot mix revisions.
- Preflight current timeline/generation, draft/attempt ownership, permissions and explicit expected revision before publication. Preserve final state transaction/conflict checks. Do not put long OCR inside the conversation lock or treat unrelated state revisions as lifecycle changes.
- Filesystem and SQLite are not a cross-resource transaction. An unactivated immutable orphan may remain diagnosable after state-commit failure; it cannot alter selected references. Never expose new images before authoritative commit. If commit succeeded but image refresh failed, report that distinction and allow refresh retry rather than repeating activation.
- Retain all new versions, including references from closed receipts and future obligations; no version pruning feature. Missing references created after rollout fail explicitly, never fall back to latest. Historical-source and old-session migration are excluded under the operator's clean-reset precondition.

### Import recovery and lifecycle

- A cached page is a coherent source/identity/page snapshot containing selected text, diagnostics, disposition, required assets, image/map and derived descriptions. Distinguish assets not provided in this checkpoint from intentional absence; exceptions cannot erase accepted assets or promote unfinished pages.
- Revalidate required map/image integrity on resume. Invalidate only affected incompatible pages; preserve PDF, diagnostics, budgets and unaffected native pages. Atomically failed checkpoint writes retain the prior readable checkpoint. Derived descriptions are not repeatedly appended.
- Persist lifecycle binding with the draft. Reset/new scenario/rollback/cancel invalidates incompatible attempt ownership and authoritative commit eligibility. Keep Q5 retained inert drafts for explicit reimport; no automatic rebinding. Stop new paid dispatch after invalidation; a late worker cannot overwrite a later draft or install into another timeline.
- Move the entire blocking OCR-identity probe off the event loop. Reuse one immutable identity snapshot for resume filtering and extraction within an attempt. Artifact/config changes invalidate compatibility or require an explicit new attempt; do not weaken hash verification or permit runtime downloads.

### Scenario transitions and history

Use an owning-module transition policy rather than handler-specific reset lists:

| Transition | State/source behavior |
|---|---|
| New scenario / newgame | Existing combat and unresolved-obligation replacement gates apply first; invalidate old import binding; new timeline/opening context; retain only product-authorized investigator continuity |
| Same-scenario version update | Finish current combat/check/Luck/due work; explicitly select new complete source; verify positions, claims and templates; compatible future obligations retain original source/timing/receipts |
| Next chapter | Same scenario version/timeline, explicit chapter window and map context; no arbitrary reset of rolls, combat or legal pending controls |
| Rollback | Restore checkpoint source/state and existing timeline-lineage rebind contract; invalidate old controls/import binding; retain original roll evidence |

Historical storage and model context are separate. Keep history, but all providers and summaries project only valid current timeline/rollback lineage. Fresh-start data carries explicit identity; no old untagged-history migration is required. A same-version correction must not invent a new game, discard receipts or retroactively rewrite committed mechanics. Ambiguous future-obligation compatibility blocks switching and requires explicit reconciliation. It never cancels obligations implicitly.

### Injury checks and weapon condition

- Give immediate major-wound CON, periodic dying CON and effect ticks distinct closed durable purposes. A major-wound check is not resolved merely because the investigator is not dying. Purpose-specific termination, pending ownership and completion update coherently in the existing transaction.
- Same-event/same-round/different-event retry and restart preserve pending/check/roll identities and outcomes. HP>0 major wound and HP=0 dying stay distinct. Autoroll/manual and multiple effects respect the one-owned-check/Luck gate. No reroll or duplicate damage/CON admission.
- Bind malfunction to stable owned weapon identity. Declaration, resumed ruling and execution recheck it before new RNG/ammo; aliases cannot select the same instance under another name to evade the flag. Another owned weapon remains usable.
- Preserve malfunction across checkpoints, provisional settlement and rollback with other state. Clearing requires evidence-backed explicit recorded authority; no invented repair skill/time subsystem. For unsupported NPC malfunction/repair, enter NEEDS_RULING rather than silently treating the weapon as usable. Preserve source-backed consumption for the original malfunctioning shot.

### Correction, maps and source coverage

- Check the entire retrospective correction for consequences; an ordinary-item phrase cannot approve a concurrent access/clue/resource/mechanical claim. Use system-held evidence for real omissions and existing conditional correction authority. No ever-growing blacklist as sole guard, fixed judge or mandatory human role. Present-time reasonable acquisition remains valid.
- All graph ingestion/cache/publication paths share type-safe schema and semantic validation: typed unique IDs, valid entry and exits, required same-page and complete-source references. Chapter-window absence is not whole-source absence. Valid one-way/disconnected designs are not rejected by invented connectivity rules. Invalid required graphs remain reviewable, retain evidence and cannot pass publication; successful OCR cannot substitute for spatial analysis.
- Segment every original pregen source region into card input, justified non-card material or unresolved coverage. Use bounded detection normalization but preserve original quote/page evidence; table prefixes and cross-page backstories cannot disappear. An explicitly detected card with an empty extraction is incomplete; genuine zero-card scenarios remain valid. Avoid duplicate cards or resending the entire book unconditionally.
- Validate name/alias types before normalization, persistence and formatting; reads remain safe against malformed inputs. Do not coerce dict/list/number into names or turn a string alias into characters. Keep exact identity/duplicate guards distinct from fuzzy lookup.

## Acceptance and verification

Required tests use real repository/save/reload seams, controlled provider/RNG boundaries, barriers and failure injection; helper-only mocks do not prove authoritative safety.

| Area | Minimum scenarios |
|---|---|
| Geometry | A,H,B accepted; H,A,B rejected; multi-column/multiple headings; overlap unresolved; permutation and source preservation |
| Snapshots | Accepted map then ValueError/general failure; atomic replace failure; downstream index/pregen failure; assets/hash/budget retained; missing required graph reprocessed |
| Lifecycle | Parse paused then reset/permission loss/cancel; late old worker; old continue; ordinary same-timeline activity; restart of a valid attempt |
| Publication/version | Stale operation changes no selected/shared source; group A pending V2 while B publishes V3; same text/different artifacts; atomic load; failed commit/failed image refresh |
| Transitions | Upload/use/continue share replacement guards; future obligations; one new opening; all-provider context isolation; correction/chapter/rollback preserve valid history and mechanics |
| Injury | Original review probe; same/different event retry; restart; CON success/failure once; manual/autoroll; multiple effects; SQLite failure leaves no dangling check |
| Weapon | Same-instance blocked after fault/restart; aliases blocked; other weapon usable; evidence-backed clearing; provisional commit/rollback; unsupported NPC ruling |
| Other boundaries | Harmless notebook versus unsupported grenade/access claim; mixed sentence; malformed graph IDs/endpoints; valid one-way/cross-page graph; three cards including table prefix; malformed aliases/exact duplicates |
| Async/identity | Heartbeat completes while identity probe blocks; one consistent attempt identity; config/model change invalidation; cancellation prevents stale installation |

On the integrated implementation head run full pytest, ruff check ., mypy app, compileall app tests and git diff --check in existing supported environments. No additional Python compatibility matrix or requirement change (Q6). Reuse available real CoC corpus and explicit offline CPU OCR setup/smoke; report unavailable dependencies/corpus honestly. Keep copyrighted pages out of commits; use page/source hashes, metrics and minimal diagnostic examples. No fabricated performance or corpus pass claim. Final validation reports every finding and separate phase completion; staged merging alone is not release approval.

## Delivery and non-goals

See [implementation task graph](postmerge_ingestion_combat_integrity_tasks.md). Scope is all thirteen findings subject to latest-head verification, plus integration/catalog preservation; historical base debt remains explicit rather than silently dropped. No parser replacement, OCR-model change, gameplay redesign, fixed LLM judge, full repair mini-game, source pruning, historical-data migration, Python compatibility work, automatic deletion or deployment. No expansion into the separate automatic-image-display feature.

The decision frontier is empty. Next step is the operator's final shared-understanding confirmation; runtime implementation still requires explicit authorization.
