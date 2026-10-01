# Combat implementation task graph

Canonical spec: [design](combat_turn_state_machine_design_spec.md). Implementation authorized by operator `$implement-spec` on 2026-10-01. No GitHub Issues; this document is the ticket tracker. Integration branch: enhancement/combat-turn-state-machine. Base: 189bc8e. Each implementer uses its own published branch/worktree; a merger integrates completed tickets.

```text
T1 Catalog ───────┐
T2 Dice ──────────┼── T4 Combat action runner/injury ─┐
T3 Working state ┘                                 ├── T6 Integration/regressions ─ T7 Review/verification
       └── T5 Resource/check/tool/router wiring ────┘
```

| Ticket | Blocks on | Scope / ownership | Acceptance | Status |
|---|---|---|---|---|
| T1 | none | app/combat_rules.py; app/data/combat*.json; catalog tests; private source verification notes | Reviewed pinned generic weapons and severity lookup, typed definitions, scenario override, exact alias/unique matching, distances/DB/impale, examples excluded, unknown requires ruling; no mutable runtime fetch | merged |
| T2 | none | app/dice.py; compound dice tests | Bounded compound parsing and max calculation preserve existing RollResult/impale API, no eval; integer/DB/half DB policy supported, no arbitrary unbounded input | merged |
| T3 | none | app/models.py new durable combat fields; app/combat_resources.py; state/resource/settlement tests | Working HP/Luck/SAN/MP/weapons/status/injury snapshots, baseline conflicts, append ledger, effective read/mutation, stable receipts, settlement/rollback/correction helpers, postcombat obligation serialization; legacy state explicit admission, no guessed prebattle baseline | merged |
| T4 | T1,T2,T3 | app/combat.py; app/combat_flow.py if warranted; action/injury/effect tests | Supported declarations/runner, NPC direct mechanics, PLAYER_CHOICE/ROLL/LUCK/NEEDS_RULING waits, initiative guards, source-backed damage, stable retries, structured major-wound/dying/dead and ongoing effects; no client hit/damage authority | merged |
| T5 | T3 (finish after T4 APIs) | app/keeper.py; legacy_commands.py; keeper_tools; command handlers/router; prompts/registry; tool/resource wiring tests | Every battle-time resource/status/ammo read/write effective; new high-level tools and existing player controls integrated; bot-only administrative tools, current actor/wait owner, manual/autoroll/Luck gates retained, postcombat processing reachable; no bypass via legacy damage/end | merged |
| T6 | T4,T5 stable API checkpoints; final completion requires their final merges | End-to-end persistence/admission/restart/correction/postcombat tests, implementation validation docs, catalog status | Durable save before reply, repeat receipts after restart, atomic mirrors/absolute settlement and conflict, only one unclosed battle, expired controls rejected, future checks survive end/new battle, no half-provisional path | implementing |
| T7 | T6 | two-axis code review, single review-fix implementer, full checks and PR publication | Full pytest/ruff/mypy/compileall/diff-check, source catalog evidence, measured tool flow, all review issues closed; PR ready, implementer worktrees cleaned | blocked |

## Shared implementation contracts

Keep rule functions transport-free. Existing tool mutations use keeper.mutate_tool_state and repository group revisions/BEGIN IMMEDIATE; do not add parallel state stores. T3 may add small typed models, but keep state_serialization backward tolerant. Reserve stable character and combat IDs, not display names.

T3 public resource seam: `initialize_working_state(state)` for new live combat; `effective_character(state, character)` returns a resource-effective Character copy during managed combat (original outside); `adjust_resource(state, character, field, delta, *, event_id, reason)` returns result and records once; `set_resource` equivalent absolute mutation; `adjust_ammo`; `set_status_tag`; `record_event`; `record_roll` caches by stable action roll ID; `get_settlement`, `commit_settlement`, `rollback_combat`, `correct_event`. Implementer may improve signatures based on existing conventions but documents final APIs in private notes for dependent tickets. Pending/due-obligation checks may call back through owning rule module only via public functions, no cross-private imports.

T3 CombatState fields should expose `combat_id`, `pipeline_version`, `phase`, `revision`, `working_resources`, `baseline_resources`, `events`, `roll_receipts`, `interaction`, `actions`, `settlement` (typed where closed values apply). GroupState owns persistent `postcombat_obligations` and retained closed combat receipts. Character may own structured injury/weapon-instance pin metadata without losing name→ammo mapping. Baseline resources distinguish character changes from unrelated GroupState checkpoint revisions. Existing CombatState without managed metadata must fail closed on unsafe migration, retaining pending and historical state; newly started battles are managed by default.

T1 public seam: resolve weapon by stable ID/name/aliases with scenario definitions and optional pinned instance; explicit outcome resolved/needs_ruling plus candidates. Definitions supply dice/DB policy/distance bands/skill/attack-mode/source. Severity resolver requires explicit severity ID, returns exact dice and source, never derives category from prose. Source validation notes live outside repo; committed provenance includes URL/revision/hash without wholesale copyrighted text.

T2 keeps existing tests and RollResult outputs compatible. Roll receipts and persistence are T3/T4, not a second RNG store in dice.py.

T4/T5 coordinate through one committed API note after T4 begins: declaration input uses stable action ID, actor/target IDs, weapon reference, semantic supported action and trusted distance; result carries durable interaction/receipt. Player transport submissions bind current check/interaction/owner; no arbitrary outcome or client damage. Existing check-registration ownership gate remains mandatory.

Every implementer reads AGENTS.md, CODING_STANDARDS.md, CONTEXT.md, relevant ADRs and full approved spec. Tests target observable behavior, not helper implementation. Scope is entire approved spec, not a disconnected prototype. Do not silently weaken approved behavior to keep old immediate-persistence tests passing; update intentional changed expectations and retain other regression contracts.

T6 integration tests start against published API checkpoints a435685/953c1ca while T4/T5 finish their remaining rule/compatibility cases. Final T4/T5 were merged normally at integration checkpoint `f1be316`. T6 retains actual public SQLite/router regressions for every discovered integration defect and cannot complete until those assertions and the validation docs pass. No unfinished checkpoint is presented as final delivery.
