# Combat implementation validation

[Approved design](combat_turn_state_machine_design_spec.md) · [task graph](combat_turn_state_machine_tasks.md) · [中文](combat_turn_state_machine_validation_zh.md)

The operator authorized implementation on 2026-10-01. T1–T5 are merged at `f1be316`, with exact advance-receipt follow-up at `d6f870c`; T6 verifies the existing repository transaction and player controls. OCR and PDF ingestion are outside this change. Final review and publication checks belong to T7.

## Implemented authority and persistence

Reviewed catalogs and bounded server dice propose mechanical outcomes. Stable combat/action/check identities, current-actor and owned-control gates decide which action may run. Manual player dice remain the default; autoroll is opt-in. Unsupported weapons, distances, modes and source rules enter NEEDS_RULING. An explicit source-bound controller cancellation can advance initiative without inventing damage.

All participating HP/Luck/SAN/MP, ammunition, statuses and injuries use durable combat working snapshots. Existing character inventory names and persistent mirrors remain unchanged until settlement. A preview keeps the battle open; confirmation validates its exact revision, absolute totals, participant baseline and obligation projection, then writes group state and both character mirrors in one SQLite transaction. Repeated confirmations and old receipts during another battle cannot apply costs again or close the new battle. External participant changes require explicit reconciliation; an unrelated checkpoint revision is not a resource conflict.

Corrections append evidence and preserve original draws. HP corrections in both directions pause for explicit injury/action reconciliation; they cannot publish stale injury state. Legacy active trackers cannot reconstruct a guessed baseline: explicit closure retains historical evidence and the already committed character state.

Future dying/effect obligations retain source, target, stop condition, logical trigger and roll receipts across settlement/restart/new battle. Due controls block rollback. A later battle admits even a zero-HP continuing target into working state. Rollback restores the prior schedule while retaining cached original rolls; same-round catch-up does not grant another roll or erase the prior consequence. Source-bound stopping is provisional during a new battle and persistent outside battle. Successful First Aid is tied to an owned healer, exact patient and current dying obligations; a result cannot stabilize another patient or be consumed twice.

## Executable evidence

`tests/test_combat_state_machine_integration.py` uses an isolated real SQLite database, public tool handlers, the public check/Luck router and repository reload after each boundary. It mocks only server RNG and outbound narration/transport. It does not replace load/save, receipt admission, resource mutation or settlement with fake stores. Controlled scenario fixtures are synthetic mechanics, not actual player sessions.

The tests cover provisional resource and inventory queries; manual/autoroll/Luck; NPC-first bootstrap and manual defense; ranged ammunition; unsupported rule cancellation; exact retry receipts and dropped replies; ownership/privacy; baseline conflicts; corrections; legacy admission; atomic storage failure; old receipts in new battles; prior effect and dying catch-up; distinct-healer stabilization; and source-scoped stopping. Related existing completed-evidence and safety/handoff tests retain enemy privacy, armor/HP and provider-failure assertions while using reviewed source routes instead of caller-supplied damage.

A manual melee trace uses two bot tool calls (initialize, declare), then two successful player control submissions (roll, Luck skip). The NPC defense roll is executed directly by the deterministic runner. An injected lost reply after the saved roll is recovered by replaying the same control: the saved roll/value is displayed without another RNG draw or resource cost. These are executed deterministic bot-flow traces; no model latency, token reduction or real-player performance comparison is claimed.

## Catalog evidence

The shipped weapon catalog version is `coc7-reviewed-2026-10-01`: 45 rows (44 wiki weapons and separately reviewed human unarmed). Foundry is pinned to `7974aaca08dd15e78959e71f8ce2e0a0ee008a01`; source file hashes and access dates accompany each row. Prototype/example rows are excluded. This verifies the shipped subset, not all compendiums. The range unit is yards, supported by pinned Foundry language evidence.

The six explicit Chaosium severities are minor 1d3, moderate 1d6, severe 1d10, deadly 2d10, terminal 4d10 and splat 8d10. The snapshot SHA-256 is `6498d208539738b1f0a04c764032110b1b5acf40f0d225163397b7630bbfdfbf`, accessed 2026-10-01. Fire/poison prose does not select a severity or imply a special rule. Runtime reads shipped data locally; it does not fetch mutable references.

Executable catalog/dice tests validate every shipped expression, reviewed lookup precedence/ambiguity, DB policy, range bands and bounded parser behavior. Complete copyrighted source descriptions are not committed.

## Verification status

T6 full-suite checkpoint: **1821 passed, 2 skipped, 152 subtests passed** (15.02 seconds). The final 36 public SQLite integration cases and related six-file compatibility selection passed; source-stop, unsupported-NPC cancellation, distinct-healer stabilization, prior pending-control rollback and exact advance retries are included. Repository-wide ruff passed; mypy passed for 126 source files; compileall and git diff --check passed. Final T7 totals and ruff/mypy/compileall/diff checks will be recorded after final integration review. Dependency deprecation warnings are retained in test output.

Exact initiative-advance retry now returns the original NPC owned-defense interaction receipt. The regression compares complete responses, then verifies no extra draws or resource costs. No remaining T6 integration defects are known; T7 still performs final review and fresh integrated verification.
