# A combat turn can always be ended

[繁體中文](combat_turn_always_endable_design_spec_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **implemented**. Base: `main_v2` at `37e16bd`.

## Problem

A real four-player run of *The Haunting* (155 of 300 turns, stopped by this) got stuck in combat from turn 143 to 155:

- Turn 143: crawling into a crawl space made the Keeper call `start_combat` while no enemy existed. Initiative went to one investigator (soak-D) and every other player was told "請先等待[未填]完成目前的行動" for five turns (145–150). The waited-for player's own turn was exploration, which is not a combat action, so initiative never moved; and the message named `[未填]` (all four sheets had a blank name), so nobody could tell who to wait for.
- Turn 148: the Keeper tried `close_legacy_combat` and `add_npc_to_combat`, a tool failed, and the rat swarm joined only then.
- Turns 151–155: the current investigator tried guarding, kicking, striking with a board, taking cover and retreating. Every reply was "這個行動無法進行"; combat stayed at round 1, READY, with no attack, damage or settlement.

The engine only knows attacks: `declare_combat_action` takes a weapon, and `advance_combat_turn` refuses until the current actor has a *completed* action this round (`combat_flow.advance_combat`). A player who does not attack, or whose action has no tool (guard, take cover, retreat, anything the scenario invents), cannot finish the turn, and neither can anyone behind them. Whether the Executor should have tried the kick (`kick` is an alias of the catalogue's brawl weapon and declares fine on a clean state) is not established by the run's logs and is not part of this change.

## Change

`advance_combat_turn` takes an optional `skip` flag. With `skip=true` and nothing unresolved, the current actor's turn is recorded as taken (a completed, rule-less action for the round: no dice, no resource change) and initiative advances exactly as before, including starting the next enemy's turn. Without `skip` nothing changes. A skip is refused when an action or interaction is unresolved, and only the investigator whose turn it is can skip (not an enemy's turn, not another player's); a successful skip counts as verifiable evidence for the turn resolution. The Keeper prompt gets one sentence: an action with no combat mechanic (guard, take cover, retreat) is narrated and the turn ended with `skip=true`, instead of answering "cannot".

## Not done

- Registering the enemies when the fight starts is [bug-active-enemy-registration-after-combat-start](bug-active-enemy-registration-after-combat-start.md): `initialize_combat` exists (PR #148) but the Keeper still sometimes calls `start_combat` alone. Until that is fixed, an enemy-less battle still holds the table until it is closed.
- The wait message still names the character, so four unnamed sheets all read `[未填]`.
- No dodge, fight-back, flee or chase rules, and no timer.
- Why the Executor made no tool call on turns 151–155 is open: a `/coc combat status` before turn 151 showed a normal managed battle (first investigator current, the rat swarm present), and a clean test state declares `kick` fine. All four sheets were unnamed in that run, which real sheets are not; rerun with named characters before treating it as a defect.

## Tests

`tests/test_combat_skip_turn.py`: without `skip` the turn is refused as before; with `skip` initiative moves; a skip is refused while an action is unresolved and for a non-current actor.

## Follow-up: the Keeper has to know to skip

A real run (the Haunting basement, 100 inputs) left the first investigator's turn open for 75 inputs: the player was searching and talking, the prompt's examples for `skip` were only guard, take cover and retreat, and the Keeper searched the scenario and tried `close_legacy_combat` (which only closes an old legacy snapshot and raised `CombatAdmissionError`) instead of ending the turn. The prompt and the `skip` tool description now also name searching, looking around and talking, and the prompt and the `close_legacy_combat` description say it is not for ending a normal combat turn. Wording only; the engine is unchanged.
