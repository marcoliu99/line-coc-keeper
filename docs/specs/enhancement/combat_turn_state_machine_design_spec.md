# Table-driven combat turns and provisional settlement

Status: design interview; Q1–Q17 confirmed, two remaining domain decisions. No runtime implementation authorized by this interview. Branch `enhancement/combat-turn-state-machine`; base `main_v2` at `189bc8e`.

## Goal and scope

AI interprets intent and narrates. Code resolves supported mechanics using reviewed catalogs, recorded dice and explicit combat states; neither model prose nor caller-supplied damage establishes a mechanic. First version covers melee, dodge/fight-back, single-shot ranged attacks, damage, major wounds and dying. Burst/full-auto, combat maneuvers and unsupported special attacks enter NEEDS_RULING. No guessed weapons, ranges, stat values or severity.

Two lookup capabilities are in scope: weapon damage definitions and Other Forms of Damage severity definitions. Catalog lookup is distinct from support for automatically resolving every special damage rule. Human/bot ruling ownership remains Q18 below. No OCR/PDF runtime work belongs to this branch.

## Confirmed decisions

| Question | Agreed contract |
|---|---|
| Q1 | Player-triggered rolls by default; retain autoroll and an explicit PLAYER_ROLL wait. NPC rolls are server generated. |
| Q2 | Whole battle provisional; persistent investigator resources change only at settlement. |
| Q3 | All battle-caused resource/injury/healing changes share one working state, including HP, Luck, SAN, MP and ammunition. |
| Q4 | Durable checkpoints retain working state, rolls and waits; restart never silently rerolls. |
| Q5 | Ordinary players cannot rollback; end requires pending checks/Luck/injury handling completed. Exact controller role clarified by Q18. |
| Q6 | Manual means player clicks existing bot check control, not user-submitted physical die values. |
| Q7 | Missing/ambiguous mechanics enter NEEDS_RULING instead of using guessed defaults. |
| Q8 | Ordinary battle-time resource adjustments route into working state with events rather than bypassing it or being categorically rejected. |
| Q9 | MVP is melee, dodge/fight-back, single-shot, injury/dying; other attacks require ruling. |
| Q10 | Pending-empty produces settlement preview; designated controller confirms before atomic commit. Controller role is Q18. |
| Q11 | Corrections append records, preserve original rolls, recalculate affected state; rerolls must be explicit. |
| Q12 | Timeouts retain waits; reminders/controller intervention do not silently select or roll. Existing autoroll remains. |
| Q13 | Only current actor/wait owner advances; duplicates return prior result, not queued future actions. |
| Q14 | Recorded controller initiative changes only between completed actions; no interruption of pending action. |
| Q15 | Queries display effective working state, marked provisional; controller can inspect persistent differences. |
| Q16 | Concurrent persistent character changes block settlement until explicit reconciliation, never overwrite. |
| Q17 | An investigator may belong to only one unclosed battle. |

## Source and catalog contracts

WeaponDefinition describes a type; WeaponInstance describes owned weapon identity, ammo and overrides. Stable IDs, canonical names, explicit aliases and provenance are separate from examples. Scenario explicit definition outranks pinned definition and generic catalog. Resolve exact/unambiguous mappings only; ambiguous handgun descriptions produce candidates and NEEDS_RULING. Preserve current name→ammo inventory during migration; do not silently select a weapon type from its name.

A reviewed definition contains skill ID, attack mode, normal damage expression, DB policy, impaling/extreme-success rules, distance-dependent damage bands where applicable, ammunition usage and provenance/catalog version. Trusted distance/ruling selects a band; existing abstract range_bands do not automatically supply physical distance for shotgun damage. Catalog terms are data, not evaluator code. Never eval arbitrary dice text.

Foundry compendiums are source/reference material, not an unreviewed wholesale production import. Directory includes en-items, en-skills and en-wiki-weapons. The inspected en-items includes prototype/example weapons: those must not become generic authoritative weapon rows merely because they are present. Pin actual source revision/hash and review each shipped definition during implementation; do not fetch mutable develop during gameplay. Full weapon-catalog verification is still implementation work, not claimed complete here.

Other Forms of Damage: severity maps minor→1d3, moderate→1d6, severe→1d10, deadly→2d10, terminal→4d10, splat→8d10. Choose severity by an explicit authorized ruling or already verified scenario rule. Environmental prose alone cannot choose it. Record effect ID, source, scope (incident/round), trigger timing, defense and stop condition. Fire/fall/poison/drowning labels do not imply identical rules. Unsupported special behavior pauses for ruling; generic damage-table lookup must not erase CON gates, poison reduction or drowning's zero-HP death exception.

References: [Chaosium damage/injury rules](https://cthulhuwiki.chaosium.com/rules/hit-points-wounds-and-healing.html#other-forms-of-damage-table); [Foundry directory](https://github.com/Miskatonic-Investigative-Society/CoC7-FoundryVTT/tree/develop/compendiums); [inspected example definitions](https://github.com/Miskatonic-Investigative-Society/CoC7-FoundryVTT/blob/develop/compendiums/en-items.yaml); operator draft `/Users/marcoliu/Downloads/coc7_combat_rules_state_machine_design.md`. Record reference access date 2026-10-01; do not copy complete descriptions into catalog.

## Existing seams and necessary changes

`app/combat.py` already plans enemy turns, tracks effects/timings and guards some pending major-wound work. Preserve these validated behaviors. Its `_sync_pc_hp` immediately mutates Character; existing Luck and inventory adjustment paths also write persistent resources. All must use effective-resource access during battle. `end_combat` currently clears CombatState; replace this flow only after approved settlement/continuing-state contract.

`app/dice.py` already has structured RollResult but its general expression parser accepts one dice term plus integer, not arbitrary compound dice. Extend bounded parsing at this seam and retain existing result contracts. Roll identity is allocated from a stable action/interaction before RNG, never a new random receipt ID per retry. Persist result and state effect together before publishing it. `resolve_enemy_action` cannot treat caller-supplied hit/damage as authoritative.

`app/repositories/group_state.py` serializes group revisions with a locked BEGIN IMMEDIATE transaction and character mirror writes. Reuse this transaction/locking seam; do not assume a character revision API already exists. Add baseline resource fingerprints/appropriate revisions for settlement conflicts. Battle checkpoints may advance group revision without changing committed Character resources; do not mistake these for external character conflicts.

Current injury handling excludes some hits that reach zero from major-wound detection. Correct against the official single-hit threshold; distinguish unconscious, major wound, dying and dead rather than generic defeated. A single hit reaching maximum HP is fatal; multiple lesser hits do not retroactively create a major wound. Player CON checks remain player owned, including injury transitions.

## Proposed durable state and flow

Combat owns combat_id, schema/rule version, participant IDs and baseline snapshots/fingerprints; working resources/injuries; initiative/current actor/round; declared actions; one owned interaction; roll receipts; append-only event records; effects/timing receipts; settlement preview and final receipt. Store only necessary combat evidence, not a general-purpose event-sourcing platform.

READY → declaration/validation → PLAYER_CHOICE or PLAYER_ROLL as needed → LUCK_DECISION where allowed → RESOLVE → INJURY_CHECK when required → completed action → next actor. NPC planner executes supported mechanics server-side until a human boundary. Unsupported inputs enter NEEDS_RULING with no speculative resource deduction. Pauses retain action identity and existing dice.

Each admitted transition runs under existing state mutation controls, rechecks actor/interaction/revision, records receipts and saves the checkpoint atomically. Duplicate commands yield the durable prior result. Invalid/stale/out-of-turn commands do not consume dice, ammo or Luck. A crash after durable save but before response returns the same receipt on retry. Unpersisted random draws are not publishable results. A running loop has a finite transition budget and yields safely rather than creating an endless NPC/effect loop.

Corrections retain original receipts and append explicit overrides. Recompute dependent state from verified records; do not silently replay RNG. If a correction invalidates subsequent choices/targets, pause for explicit reconciliation rather than retaining impossible actions or silently discarding player decisions. Rollback preserves audit history, closes the battle, releases participant reservations and invalidates its controls; it cannot erase messages already delivered.

Settlement previews show baseline→effective values and injuries, bound to current battle revision and settlement ID. Confirm only if current, pending-empty and persistent baselines still match. Write absolute final values, character mirrors, receipt and closed combat state in one repository transaction. Repeat confirmation returns the same committed receipt. Conflict keeps battle open; reconciliation generates a fresh preview. Normal narration does not independently publish provisional mechanics as committed scenario facts.

## Remaining interview frontier

Q18: repository glossary defines Keeper as bot and KP Assistant/KP as human. Prior questions used Keeper for approval/ruling/rollback. Explicitly decide whether these administrative decisions require registered human KP authority or intentionally belong to the bot; do not silently reinterpret a confirmed answer. ADR-0001 allows some narrative corrections without KP, not arbitrary mechanical rollback.

Q19: determine whether combat may settle while an investigator is still dying or has a continuing effect with a future obligation. Transfer these structured obligations to postcombat play, or block settlement until stabilized/ended? Pending-empty alone does not decide future round checks. Never drop the obligation when clearing CombatState.

## Validation and delivery

Spec/glossary/ADR first, explicit shared-understanding/implementation confirmation after resolving frontier. Incremental implementation: catalogs and lookup tests → bounded dice/receipts → effective resources/checkpoints → supported action/interaction runner → structured injury/effects → settlement/correction/resume integration. Do not ship half-provisional runtime where HP is shadowed but Luck/ammo writes remain immediate. Legacy active battles require explicit migration/version admission, with no guessed reconstructed baseline or lost pending roll.

Tests: scenario overrides and weapon ambiguity; DB/compound expressions, shot distance and unsupported rules; table severity lookup without freeform guessing; defense ties and existing autoroll; exact actor/check ownership; HP/Luck/ammo/SAN/MP/healing routing; zero-HP injury distinctions; effect timing once; retries/restarts at each roll/Luck/checkpoint/settlement boundary; no duplicate deductions; external conflicts; two-battle admission; appended corrections; cancelled-control invalidation; pending-empty settlement; Q19 continuing-state outcome; privacy of enemy HP; existing narrative correction authority. Run full pytest, ruff, mypy, compileall and diff-check on implementation. Compare tool-round-trip counts on real traces; draft performance estimates are not measured evidence.
