# Combat engine

[繁體中文](combat_engine_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented** (phase 3 of the [four-phase architecture refactor](architecture_refactor_phases_1_4_design_spec.md)). Based on `main_v2` at `2affd06` (2026-10-04), stacked on the [game-state transaction](state_transaction_design_spec.md) and the [check engine](check_engine_design_spec.md).

Before this change `combat.py` and `combat_flow.py` imported each other (four function-level `from app import combat_flow` plus the top-level `from app import combat`), and `combat.py` re-asked "is this battle managed?" in 15 places while implementing both modes in the same functions. Tools, handlers, the check adapter and the retire flow each reached into whichever of the two they happened to need. The goal is one entry point that reads the battle's mode once and runs the matching implementation, with the two combat modules in a strict order. No extra model call and no new tool is introduced.

## Shape

```text
Keeper tools (keeper_tools/combat.py, managed_combat.py)   /coc combat   check adapter   retire flow
                          \                                    |              |            /
                           app/services/combat_engine.py   CombatEngine.handle(state, action)
                               |  reads the mode once: IDLE / MANAGED (an active battle that is not managed is refused)
                               +--> combat_flow.py   (MANAGED_OPS: receipts, owned waits, obligations)
                               +--> combat.py        (shared primitives and rules)
                                         \               /
                                          combat_resources.py (leaf: working resources, settlement)   combat_rules.py (leaf: pure rules)
```

* `app/services/combat_actions.py` — one frozen dataclass per thing that can be asked of a battle (`Declare`, `Run`, `Choose`, `Advance`, `ApplyDamage`, `PreviewSettlement`, …). The type parameter is the result type. An action carries intent and stable identities, never a die result.
* `app/services/combat_engine.py` — `CombatEngine.handle(state, action)`. `mode_of(state)` is called once per action; the handler gets the mode and passes `combat_flow.MANAGED_OPS` to the shared rules. A managed-only action in `IDLE` refuses without writing; an active battle that is not managed raises `CombatAdmissionError` ("unsupported legacy combat format. Start a new combat.") before any handler runs.
* `combat.ModeOps` — the steps of a turn that belong to the working-resource pipeline: fixed timings, damage, hit-point sync, the advance and planning guards, the round clock, restoring a blocked advance. The rules take it as a required argument; they never ask which mode they are in (`combat.py` no longer reads the flag at all).
* The layers are checked as an import graph (lazy imports included): `combat_rules` and `combat_resources` are leaves; `combat` reaches only `combat_resources`; `combat_flow` builds on `combat`; only the engine imports `combat_flow`. `app.models` no longer reaches combat code: `GroupState.retire_active_character` takes the turn finisher as an argument.

## Contract kept

1. **Modes.** Every battle is managed. A battle saved before the working-resource pipeline is no longer supported: it is never converted or guessed at, and every action on it is refused (see [removing the legacy combat mode](remove_legacy_combat_mode_design_spec.md)).
2. **Atomic actions.** One state transaction wraps an action, including the check it waits on, resource changes and settlement entries. An action that needs a person's answer returns with that wait saved (`PLAYER_CHOICE`, `PLAYER_ROLL`, `INJURY_CHECK`, `LUCK_DECISION`); the answer arrives as a new action carrying the same identity.
3. **No double settlement.** Damage, ammunition, effects and turn advance are keyed by stable ledger ids (`action_id`, `event_id`). A retry, a double click, or the same hit sent through an old tool and the new entry replays the stored receipt.
4. **Player choices are never made for the player**: defence, Luck, weapon and consumable stay the player's; autoroll stays off by default.
5. **Pending controls are mode-bound.** A control left over from a battle that is gone, rolled back or converted is refused without rolling or consuming anything.

Timing points (declaration, ammunition, malfunction, cancellation, weapon change, defeated participant, expired defence) are listed with the test that pins each in the [phase 3 report](../../refactor/phase3-result.md).

## Compatibility

Tool names, schemas and outputs (except that `offer_npc_attack_defense_choice` and `close_legacy_combat` have been removed from the Keeper's tools), Discord custom ids, command text and stored combat shapes are unchanged; a saved battle from before the working-resource pipeline is refused, not migrated. The raw-outcome tools (`apply_combat_damage`, `damage_combatant`, …) still refuse while a battle runs. `app.combat.apply_managed_damage`, `managed_single_hit` and `is_managed` moved (to `combat_flow` and `combat_resources`); no code in this repository imports them from the old place.

## Verification

`tests/test_combat_engine.py` (B1–B9 and the engine contract on real SQLite with scripted dice), `tests/test_architecture_combat.py` (B10: layers, single mode read, fresh-process imports), and the existing combat rule, flow, wiring, state-machine and settlement suites with their calls retargeted to the engine. Report: [phase 3](../../refactor/phase3-result.md).
