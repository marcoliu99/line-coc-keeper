# Check registration lifecycle

[繁體中文](check_lifecycle_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented**. Based on `main_v2` at `83e0c54` (2026-09-28).

Registration of a player-owned check currently spans Keeper tool branches, combat damage, and scripted opening checks. Each caller must remember pending/Luck admission, duplicate identity, mutation ordering, and whether an existing roll may be reused. The goal is one deep registration module whose small interface makes those invariants local and testable. This refactor does not add a model call.

## Current evidence

- `keeper.py` registers manual skill, SAN, and choice checks in separate branches; manual skill has its own pending/Luck logic, while `offer_check_choice` checks pending but has no branch-local Luck check. Autoroll skill and SAN duplicate admission logic.
- `keeper.py` registers a major-wound CON check after `adjust_character`; `combat.py` registers the same kind of check during damage. Both must keep damage and check registration in the same state transaction.
- `commands/handlers/system.py` directly assigns opening checks for every Investigator. These entries omit modern check identity and deliberately preserve compatibility with legacy identity fallback.
- `check_identity.pending_check_blocker` already expresses the shared pending/Luck rule, but callers must know when to call it and when a repeated request is a no-op.
- `legacy_commands.py` has assignments that restore an in-progress pending entry during resolution; those are not new registrations and must not be moved into the new admission path.

The missing branch-local Luck check is a confirmed difference in source. Whether an outer guard makes that difference unreachable requires a targeted regression test; this spec does not claim a reproduced player bug.

## Scope

1. The registration module owns admission against the freshly loaded `GroupState`, pending identity, duplicate/no-op semantics, and the rule that no new dice or state mutation happens before admission. It returns an explicit registered / identical / blocked outcome with the effective persisted check identity and save requirement. Callers translate the outcome into their current tool payloads and player text.
2. Migrate Keeper manual skill/SAN, choice and NPC attack-defense choice registrations; major-wound CON registrations from `adjust_character` and combat; and scripted opening checks. Keep check-specific construction, option filtering, dice, Luck offer calculation, damage, and SAN loss in their owning rule modules.
3. Keep autoroll admission within the same transaction before any roll, including the existing request cache behavior. A cached result may be returned only after validating its current timeline and ownership contract; it never authorizes an unresolved different pending check or Luck decision.
4. Duplicate requests preserve existing behavior: identical manual skill/choice requests reuse the pending entry without a save; ranged NPC attacks do not reuse a prior attacker roll; melee reuse requires matching the full raw options, attacker parameters and range. Older pending entries with only option labels cannot prove that match and are rejected without rerolling. Incompatibilities are rejected with no write or roll.
5. All registration entry points use the existing state lock and fresh reload. The registration module never acquires a second lock or saves independently. Combat and opening registration stay atomic with their triggering mutation. The module must not silently drop a required CON or opening check when blocked: the owning operation must reject or expose an explicit blocked result before committing.
6. Preserve existing pending fields, legacy identity fallback, Discord button tokens, player ownership and timeline checks. New NPC defense choices add an optional `raw_option_request` fingerprint for safe retry comparison; older entries remain readable but cannot claim identical-retry reuse from labels alone. No database migration is planned.

## Flow

```text
Tool / combat / scripted opening
  -> existing state lock + fresh GroupState
  -> build check-specific candidate (no random roll)
  -> registration module: owner + timeline + pending/Luck + duplicate identity
       | blocked   -> explicit result; no mutation or roll
       | identical -> existing identity; no save or roll
       | admitted  -> register pending, or authorize autoroll once
  -> owning rule module applies any allowed roll/effect
  -> one authoritative state commit
  -> existing resolution and Supervisor followup
```

## Non-goals

- Do not change `/coc check`, Luck decision resolution, followup routing, Narrator, or tool schemas.
- Do not add a fixed LLM judge, replay a resolved check, change autoroll defaults, or merge unrelated combat/SAN rules into one generic rule engine.
- Do not treat a pending entry restored during check resolution as a new registration.

## Test plan

- Interface-level registration matrix: each entry point against no pending, same pending, different pending, pending Luck, stale timeline, and a concurrent resolution between outer snapshot and locked reload. Assert persisted revision and that no dice are consumed on blocked/identical paths.
- Combat damage and `adjust_character`: blocked major-wound check must not commit HP changes without the required check; manual and autoroll paths retain current wound effects.
- Opening narration: an existing pending/Luck decision cannot be overwritten; multiple Investigators receive distinct identities, and delivery works with modern and legacy entries.
- NPC choices: repeated melee attack reuses its original attacker roll; ranged repetition and changed options cannot manufacture a new roll or bypass filtered options.
- Run focused existing tests (`test_luck_buyup_gate.py`, `test_npc_attack_latency.py`, `test_scenario_action_check_handoff.py`, `test_combat_cards.py`, `test_state_persistence.py`) and then Ruff, mypy, and the full pytest suite.

## Implementation decisions

- The seam sits inside existing transaction owners so the registration module has no persistence adapter. This keeps atomic HP/check and opening/check commits, but integration tests remain necessary for the transaction contract.
- Opening checks currently have no explicit check ID. The refactor issues modern identities only for newly created opening checks; old persisted entries retain deterministic legacy IDs.
- A multi-Investigator opening is all-or-nothing if any owner is blocked. A partial registration could announce a check to only some players after the opening has been committed; an explicit blocked result lets the opening owner decide how to present that condition.
