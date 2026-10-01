# Combat implementation validation

[Approved design](combat_turn_state_machine_design_spec.md) · [task graph](combat_turn_state_machine_tasks.md) · [中文](combat_turn_state_machine_validation_zh.md)

The operator authorized implementation on 2026-10-01. T1–T5 are merged at `f1be316`, with exact advance-receipt follow-up at `d6f870c`; T6 verifies the existing repository transaction and player controls. OCR and PDF ingestion are outside this change. T6 is merged at `29f7f2d`; T7 fixes have passed both independent review axes. Final integrated/publication gates are recorded separately below.

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

T6 merged checkpoint `29f7f2d` passed **1823 tests, 2 skipped, 152 subtests**. After accepted review fixes and prompt expectation migration, fixed runtime/test checkpoint `314b52d` passed **1851 tests, 2 skipped, 152 subtests** (44.31 seconds). The integration file now includes 38 SQLite/transport/prompt cases and 11 serialized-role codec cases. Repository-wide `ruff check .` passed; `mypy app` passed for 126 source files; `python -m compileall app tests`, `git diff --check` and `git diff 189bc8e...HEAD --check` passed. Nine existing dependency deprecation warnings remain in the full output. No benchmark or actual-player timing inference is made from pytest duration.

Exact initiative-advance and choice retries return their original owned interaction responses. Lost roll and choice delivery regressions reload durable state and verify the previous result, no extra draws and unchanged resources. Both independent review axes are accepted with no remaining/new findings. Fresh final integration checks attest the merged HEAD below; PR readiness/cleanup do not authorize merge to `main_v2` or deployment.

## Review findings and resolutions

Standards initially reported two findings: the closed role/stage type boundary (P2) and repeated range selection (P3). Fix `c5547ff` types the actual `CombatState.actions` stage and check context/role interfaces, preserving serialized `injury:<character_id>` identities. All three ranged paths use a small owning rules helper while their ammunition/source admission stays separate. Independent Standards recheck: **0 remaining/new findings**.

Spec initially reported two P2 findings: exact choice replay after lost delivery, and static/active/KP prompts directing legacy caller outcomes. Fix `c5547ff` retains the battle/timeline/owner/character/exact-choice response and original button reply. Identical owned input replays its prior result; foreign/changed-choice/rolled-back/new-battle controls cannot replay it. Prompts now use source-bound runners, owned controls, single ammunition costs and preview/confirmation. Ordinary authorized controller resource adjustments remain available without substituting for weapon adjudication. Independent Spec recheck: **0 remaining/new confirmed findings**. Tests-only follow-up `314b52d` updates three obsolete prompt expectations and retains scenario-trigger, privacy and correction assertions.

Actual player-session traces are unavailable. Evidence is limited to controlled synthetic mechanics using real SQLite and public bot/router flows; no production session throughput, model latency or token savings are claimed.

## Changed file inventory

Relative to approved base `189bc8e`, 58 files are changed, including review fixes:

- Core state/rules/dice: `app/combat.py`, `app/combat_flow.py`, `app/combat_resources.py`, `app/combat_rules.py`, `app/dice.py`, `app/models.py`.
- Catalog data: `app/data/combat_severities.json`, `app/data/combat_weapons.json`.
- Tools/player controls: `app/commands/handlers/buttons.py`, `app/commands/handlers/character.py`, `app/commands/handlers/combat.py`, `app/commands/handlers/system.py`, `app/commands/router.py`, `app/keeper_tools/character.py`, `app/keeper_tools/checks.py`, `app/keeper_tools/combat.py`, `app/keeper_tools/consequences.py`, `app/keeper_tools/inventory.py`, `app/keeper_tools/managed_combat.py`, `app/keeper_tools/registry.py`, `app/keeper_tools/resource_bridge.py`, `app/legacy_commands.py`.
- Agent/prompt/turn integration: `app/agents/context_builder.py`, `app/agents/executor.py`, `app/agents/narrator.py`, `app/agents/tool_gateway.py`, `app/keeper.py`, `app/keeper_prompt_policy.py`, `app/services/canonical_facts.py`, `app/services/prompt_config.py`, `app/services/turn_context.py`, `app/services/turn_delivery.py`, `app/services/turn_resolution.py`.
- Tests: `tests/test_combat_cards.py`, `tests/test_combat_flow.py`, `tests/test_combat_resources.py`, `tests/test_combat_rules.py`, `tests/test_combat_state_machine_integration.py`, `tests/test_combat_wiring.py`, `tests/test_completed_combat_evidence.py`, `tests/test_compound_dice.py`, `tests/test_keeper_tool_registry.py`, `tests/test_kp_assistant_v2.py`, `tests/test_major_wound_con_gate.py`, `tests/test_static_prompt_combat_routing.py`, `tests/test_static_prompt_integration.py`, `tests/test_static_prompt_operational_authority.py`, `tests/test_turn_consistency_handoff.py`, `tests/test_turn_safety.py`.
- Domain/spec/validation docs: `CONTEXT.md`, `docs/adr/0003-provisional-combat-settlement.md`, `docs/specs/catalog.json`, `docs/specs/enhancement/combat_turn_state_machine_design_spec.md`, `docs/specs/enhancement/combat_turn_state_machine_design_spec_zh.md`, `docs/specs/enhancement/combat_turn_state_machine_tasks.md`, `docs/specs/enhancement/combat_turn_state_machine_tasks_zh.md`, `docs/specs/enhancement/combat_turn_state_machine_validation.md`, `docs/specs/enhancement/combat_turn_state_machine_validation_zh.md`.

## Final publication

Final merged checkpoint `bb53045` passed a fresh full suite: **1851 passed, 2 skipped, 152 subtests** (13.96 seconds), plus ruff, mypy (126 files), compileall and both whitespace checks. PR #156 is ready for review. All seven clean, merged combat implementer worktrees were removed; the integration worktree and published branches remain. No main-branch merge or deployment was performed.

## GitHub PR #156 review follow-up

The four GitHub findings (two P1, two P2) are fixed: non-unarmed investigator weapons require owned inventory/instance evidence regardless of ammunition; NPC catalog declarations require a mapped reviewed attack; NPC rulings resume with the card attack skill and verified DB/range/ammo; unknown effect/source stop commands fail without modifying future obligations. Declaration and resume share the ownership/source gate. The transport retains owned instance keys during resume; recorded stop receipts remain idempotent.

Ten additional parameterized/public regressions cover denied/owned melee, skill-only NPC rejection, mapped attacks, DB and single-shot range recovery, no extra draws on replay, active-effect stop identity and real SQLite postcombat stop/owned-instance reload. Full suite: **1861 passed, 2 skipped, 152 subtests passed** (15.50 seconds, 9 existing warnings). Ruff, mypy (126 files), compileall and git diff --check pass. This follow-up changes no gameplay scope beyond the approved source/ownership/recovery contracts.
