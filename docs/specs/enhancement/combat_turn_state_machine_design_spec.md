# Table-driven combat turns and provisional settlement

Status: implementation authorized by operator `$implement-spec` on 2026-10-01; Q1–Q19 confirmed. Branch `enhancement/combat-turn-state-machine`; base `main_v2` at `189bc8e`.

## Goal and scope

AI interprets intent and narrates. Code resolves supported mechanics using reviewed catalogs, recorded dice and explicit combat states; neither model prose nor caller-supplied damage establishes a mechanic. First version covers melee, dodge/fight-back, single-shot ranged attacks, damage, major wounds and dying. Burst/full-auto, combat maneuvers and unsupported special attacks enter NEEDS_RULING. No guessed weapons, ranges, stat values or severity.

Two lookup capabilities are in scope: weapon damage definitions and Other Forms of Damage severity definitions. Catalog lookup is distinct from support for automatically resolving every special damage rule. The bot Keeper owns approval/ruling/rollback; a human KP Assistant is optional. No OCR/PDF runtime work belongs to this branch.

## Confirmed decisions

| Question | Agreed contract |
|---|---|
| Q1 | Player-triggered rolls by default; retain autoroll and an explicit PLAYER_ROLL wait. NPC rolls are server generated. |
| Q2 | Whole battle provisional; persistent investigator resources change only at settlement. |
| Q3 | All battle-caused resource/injury/healing changes share one working state, including HP, Luck, SAN, MP and ammunition. |
| Q4 | Durable checkpoints retain working state, rolls and waits; restart never silently rerolls. |
| Q5 | Ordinary players cannot rollback; end requires pending checks/Luck/injury handling completed. The controller is the bot Keeper. |
| Q6 | Manual means player clicks existing bot check control, not user-submitted physical die values. |
| Q7 | Missing/ambiguous mechanics enter NEEDS_RULING instead of using guessed defaults. |
| Q8 | Ordinary battle-time resource adjustments route into working state with events rather than bypassing it or being categorically rejected. |
| Q9 | MVP is melee, dodge/fight-back, single-shot, injury/dying; other attacks require ruling. |
| Q10 | Pending-empty produces settlement preview; designated controller confirms before atomic commit. The controller is the bot Keeper. |
| Q11 | Corrections append records, preserve original rolls, recalculate affected state; rerolls must be explicit. |
| Q12 | Timeouts retain waits; reminders/controller intervention do not silently select or roll. Existing autoroll remains. |
| Q13 | Only current actor/wait owner advances; duplicates return prior result, not queued future actions. |
| Q14 | Recorded controller initiative changes only between completed actions; no interruption of pending action. |
| Q15 | Queries display effective working state, marked provisional; controller can inspect persistent differences. |
| Q16 | Concurrent persistent character changes block settlement until explicit reconciliation, never overwrite. |
| Q17 | An investigator may belong to only one unclosed battle. |
| Q18 | The bot Keeper approves settlement and rollback; human KP Assistant registration/approval is not required. |
| Q19 | Settlement may occur with ongoing injury/effects; transfer future obligations into durable postcombat tracking. |

## Source and catalog contracts

WeaponDefinition describes a type; WeaponInstance describes owned weapon identity, ammo and overrides. Stable IDs, canonical names, explicit aliases and provenance are separate from examples. Scenario explicit definition outranks pinned definition and generic catalog. Resolve exact/unambiguous mappings only; ambiguous handgun descriptions produce candidates and NEEDS_RULING. Preserve current name→ammo inventory during migration; do not silently select a weapon type from its name.

A reviewed definition contains skill ID, attack mode, normal damage expression, DB policy, impaling/extreme-success rules, distance-dependent damage bands where applicable, ammunition usage and provenance/catalog version. Trusted distance/ruling selects a band; existing abstract range_bands do not automatically supply physical distance for shotgun damage. Catalog terms are data, not evaluator code. Never eval arbitrary dice text.

Foundry compendiums are source/reference material, not an unreviewed wholesale production import. Directory includes en-items, en-skills and en-wiki-weapons. The inspected en-items includes prototype/example weapons: those must not become generic authoritative weapon rows merely because they are present. Pin actual source revision/hash and review each shipped definition during implementation; do not fetch mutable develop during gameplay. The shipped catalog now contains 45 reviewed weapon rows: 44 pinned wiki weapon rows and a separately reviewed human unarmed row. It pins Foundry revision `7974aaca08dd15e78959e71f8ce2e0a0ee008a01`; prototype/example rows are excluded. This verifies the shipped subset, not every compendium. See [implementation validation](combat_turn_state_machine_validation.md) for provenance and executable evidence.

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

## Resolved authority and continuing-state contracts

The controller in this spec is the **bot Keeper**, as defined in CONTEXT.md. It may review and confirm settlement, make explicit supported rulings and approve rollback without a registered human KP Assistant. A player request does not directly invoke administrative mutation; the Keeper must issue an explicit scoped command with a reason. Code checks battle identity, command ownership, pending state, current preview, baseline conflicts and duplicate receipts regardless of the Keeper's prose. Rollback cannot be disguised as a retry or a player-side cancel button. Human KP steering/correction remains available under existing authority; no new mandatory human role is introduced. This is a deliberate combat decision, not an inference from ADR-0001's limited narrative-correction policy.

The settlement preview/confirmation phase remains explicit and durable, but the bot performs confirmation; it is not an extra human approval wait. Concurrent mutations or unresolved inputs still block the deterministic transition, even when the Keeper asks to confirm.

Pending-empty means no **currently due, unresolved** player choice/check, Luck decision, injury transition or effect application. It does not mean all injuries must heal or all future effects must cease. A dying investigator or ongoing effect can settle once currently due work is resolved, provided the settlement atomically preserves the injury and transfers every future obligation to structured postcombat tracking.

Postcombat records retain participant/effect identity, injury state, next logical-game-time trigger, condition/stop rule, rule source and processed timing/roll receipts. Continued dying CON checks remain player owned (or existing autoroll); effect damage uses reviewed rules and persistent receipts. Logical round advancement is explicit game progression, not a wall-clock timer. Settlement cannot grant a free interval, restart timing, skip a due check or duplicate a tick. Player-owned unresolved postcombat checks pause applicable progression. End/clear CombatState only after transfer is durable. Restart restores obligations without rerolling; a subsequent battle admits those existing obligations rather than creating duplicates or resetting injuries.

After settlement, obligation resolution applies under the ordinary persistent-state mutation contract; a new battle routes affected resource/injury changes into its working state. Source-battle receipts remain available for audit. Rolling back an uncommitted battle does not transfer its provisional obligations or publish them as committed canonical facts.

There are no remaining business-decision questions in the current interview. Technical schema/parser/catalog verification details are implementation work to be validated at the stated seams; they are not silently delegated to the player.

## Validation and delivery

Spec/glossary/ADR first, explicit shared-understanding/implementation confirmation before runtime changes. Incremental implementation: catalogs and lookup tests → bounded dice/receipts → effective resources/checkpoints → supported action/interaction runner → structured injury/effects → settlement/correction/resume integration. Do not ship half-provisional runtime where HP is shadowed but Luck/ammo writes remain immediate. Legacy active battles require explicit migration/version admission, with no guessed reconstructed baseline or lost pending roll.

Tests: scenario overrides and weapon ambiguity; DB/compound expressions, shot distance and unsupported rules; table severity lookup without freeform guessing; defense ties and existing autoroll; exact actor/check ownership; HP/Luck/ammo/SAN/MP/healing routing; zero-HP injury distinctions; effect timing once; retries/restarts at each roll/Luck/checkpoint/settlement boundary; no duplicate deductions; external conflicts; two-battle admission; appended corrections; cancelled-control invalidation; pending-empty settlement; atomic postcombat obligation transfer, continued dying checks, restart timing, later-battle admission and rollback without transfer; privacy of enemy HP; existing narrative correction authority. Run full pytest, ruff, mypy, compileall and diff-check on implementation. Compare tool-round-trip counts on real traces; draft performance estimates are not measured evidence.

Implementation tickets / 工作圖: [EN](combat_turn_state_machine_tasks.md), [繁中](combat_turn_state_machine_tasks_zh.md).

## PR #156 ownership and recovery review

All non-unarmed investigator weapons require an existing owned instance or explicit inventory mapping, even when ammunition is not consumed. Every declared NPC weapon requires its matching reviewed attack-card entry; a matching skill alone is insufficient. Declaration and source/range ruling resume share these gates. NPC resume reads the mapped attack skill and verified DB/STR+SIZ from the card, retaining rolls and ammunition identity. An effect stop must match the exact source battle/effect or a previously recorded stop receipt; missing or stale targets fail without mutation.
