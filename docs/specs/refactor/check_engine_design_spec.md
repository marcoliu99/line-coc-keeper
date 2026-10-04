# Check engine

[繁體中文](check_engine_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented** (phase 2 of the [four-phase architecture refactor](architecture_refactor_phases_1_4_design_spec.md)). Based on `main_v2` at `2affd06` (2026-10-04), stacked on the [game-state transaction](state_transaction_design_spec.md).

Before this change a check could be settled through three codes that each carried their own copy of the rules: the Keeper tools (`keeper_tools/checks.py`), the `/coc check` / `/coc luck` resolvers in `legacy_commands.py` (about 700 lines; buttons call the same functions), and a combat-owned variant of each. Tier wording, the Luck offer, the SAN → INT madness chain, the resolved-event seed and its persistence existed more than once, and the event persistence was written twice (`keeper.py`, `legacy_commands.py`). The goal is one check engine whose rules are applied identically whichever door the check came through. No extra model call is introduced.

## Shape

```text
/coc check, /coc luck, buttons        Keeper tools (skill_check, sanity_check)
  app/commands/handlers/checks.py       app/keeper_tools/checks.py   <- adapters: parse, reply, narrate
              \                          /
               app/checks/service.py        <- stateful: applies the rules to the state a transaction hands it
               app/checks/rules.py          <- dice + arithmetic behind a DicePort, no GroupState
               app/checks/luck.py           <- who may spend Luck (policy)
               app/checks/events.py         <- the resolved event and its consequence origin
               app/checks/narration.py      <- result wording
                        |
               app/repositories/state_transaction.py
```

* `app/checks/dice_port.py` — `DicePort` (`skill_check`, `sanity_check`, `roll_madness`). Production uses `ModuleDice`, which looks the function up on `app.dice` at call time, so existing patches of `dice.skill_check` still reach the engine; tests inject a scripted port. `dice.evaluate_roll` is the pure half of `dice.skill_check`.
* `service.resolve_player_check(state, user_id, text, ...)` and `service.resolve_luck_decision(...)` take the **latest** state from `state_transaction.mutate`, change it in place and return a `CheckOutcome`. `outcome.changed` says whether the transaction has anything to write; a refusal leaves the pending entry exactly as it was and writes nothing. `service.autoroll_skill` / `autoroll_sanity` are the Keeper's autoroll, built from the same helpers (tier, Luck offer, madness chain, event seed), so a player's roll and an autoroll cannot disagree.
* Checks owned by the combat engine (a pending entry carrying `combat_context`, `postcombat_context` or `medical_context`) go to a `ManagedChecks` adapter. `app/checks` does not import combat code; `app/services/managed_checks.py` (`ManagedCombatChecks`) sits above both. Phase 3 folds it into the combat engine.
* `app/commands/handlers/checks.py` is the only command adapter: one `state_transaction.mutate` for the roll, then (for a settled check) the Keeper narrates, then the resolved event is recorded. The roll is committed before narration starts, so a failed or retried narration never rerolls.

## Rules kept

1. Intent → eligibility → roll → tier → Luck offer or final result → resolved event → consequences, in that order. Every refusal precedes the roll; nothing is rolled for a request that is refused.
2. A Luck decision keeps the original roll. The balance is read from the latest state when the decision is made, so Luck spent elsewhere since refuses the spend (`Luck 只有 N 點…`) with nothing deducted and the decision still open.
3. A settled check is recorded once under the action id `check-event:<event_id>` (shared with the Keeper path); the consequence origin is published before the follow-up turn and is idempotent. A retried continuation writes nothing.
4. A check that another check made necessary (the INT check after a 5-point SAN loss; a triggered Dodge) gets its own check id and records `caused_by_check_id` pointing at the original; the original event is never overwritten and other players' pending entries are untouched. Several players keep independent pending entries.
5. Autoroll stays off by default. With it off, a Keeper request registers a pending entry and rolls nothing and applies no consequence.

## Luck policy

One function, `checks.luck.luck_allowed`, states it. Sanity checks and the INT check that decides madness never offer Luck; a Pushed Roll is final; a Fumble cannot be bought off (`app.luck`); a pending entry with `allow_luck: False` (set by the combat engine for injury, dying and stabilisation checks) offers none. The Luck decision is chosen by the server from the stored options; an `allow_luck` value coming from model input is not read.

**Decision (product owner), no code change:** the requirements had described combat checks as Luck-free, but the managed combat flow has always offered Luck on attack and defence rolls (`combat_flow._request_check` sets `allow_luck: not injury`) and `tests/test_combat_wiring.py` pins that behaviour. The decision is to keep it and amend the requirement: attack and defence rolls may use Luck; sanity checks and the injury checks (major wound, dying, stabilisation, which already pass `allow_luck: False`) may not. The conflict is recorded in the [phase 2 report](../../refactor/phase2-result.md).

## Compatibility

Tool names, schemas and outputs, Discord custom ids, command text and stored pending/Luck/event shapes are unchanged. New fields are optional on read: `caused_by_check_id` on an event and a chained INT pending entry, `source_check_id`/`source_event_id` kept on a triggered check's Luck decision. `app.commands` still exports `handle_check_command` and `handle_luck_decision`.

## Verification

`tests/test_check_engine.py` (C1–C11 on real SQLite with scripted dice), `tests/test_architecture_checks.py` (dependency direction, one owner per function, old duplicates deleted), plus the existing check, Luck, opposed, consequence and combat-wiring suites retargeted to the new owners. Report: [phase 2](../../refactor/phase2-result.md).
