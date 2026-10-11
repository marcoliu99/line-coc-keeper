# A player's shot with no stated distance paused the whole fight, and the log did not say why a combat tool failed

[繁體中文](single_shot_without_distance_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `e4a9a1f`.

## Problem

In the 2026-10-11 soak runs on `d18fffd` (Camp Sunny rerun2, The Lightless Beacon rerun3) a fight stopped moving for several turns while every player read 「戰鬥暫停中，要等守密人裁定後才能繼續」 or 「處理這個行動的工具失敗了」.

- **Camp Sunny turns 77 to 99.** Reidel's first shot was declared as a single shot with no distance. The engine answers that with a ruling (`Single shot requires trusted physical distance and reviewed base range`), which sets the phase to `NEEDS_RULING` for the whole table. Five of his next lines were then refused, and so was Phil's. Only the Keeper can clear a ruling, and it took many turns.
- **Lightless turns 38 to 42.** The fight began with an enemy's opening attack, a poison needle the engine does not support, which paused the fight the same way. Every player's shot was refused with 「Resolve the current action before declaring another」 until the Keeper cancelled the ruling at turn 43. Why the Keeper's two rulings before that failed is not in the log.
- **The log.** The runtime log recorded a tool call as `turn.observed` with only the tool name, so a run could show that a tool ran but not that it failed or why. The Lightless cause above had to be pieced together from the harness note and the engine source.

## Change

- **A player's single shot with no stated distance is a regular roll.** `combat_flow` takes the shot as inside the weapon's base range when the actor is a player character and no distance was given, and the fight goes on. A stated distance is used as before, and a distance past the weapon's extreme range still needs a ruling. The Keeper may still pass `distance_yards` when the scene makes the target far. The same rule applies when a paused shot is resumed.
- **A failed tool call says why in the runtime log.** `turn.observed` now carries `ok`, and for a failed call the error text (first 300 characters) and the phase it left the fight in. A call the gateway rejects (`llm.tool.rejected`) also carries its message. These are rule texts, not what a player wrote.

## Not changing

- **An enemy's single shot with no distance still asks for a ruling.** The Keeper plays the enemy and knows the scene; the existing test for it stands.
- **A weapon whose damage depends on distance** (a shotgun) still needs the distance, as before: that is the damage rule, not the range band.
- **The opening attack of an enemy the engine cannot play** (the Lightless poison needle) still pauses the fight and still waits for the Keeper. This spec only makes the next run's log show what the Keeper's rulings fail on; whether the engine should skip such an action by itself is a separate decision.

## Verification

- A player's single shot with no distance is declared and rolls at `regular`; one with `distance_yards=60` does not roll at `regular`; one with `distance_yards=1000` still needs a ruling and spends no ammunition (`tests/test_combat_state_machine_integration.py`, `tests/test_combat_mechanics_coverage.py`).
- The ruling test that relied on a missing distance now uses a distance past extreme range and still checks that the owned weapon key survives a reload.
- An enemy's single shot with no distance still returns `NEEDS_RULING` (`tests/test_combat_flow.py`, unchanged).
- A failed tool call logs `ok`, `error` and `phase`; a successful one logs `ok` only; a gateway rejection logs its message (`tests/test_turn_phases.py`).
