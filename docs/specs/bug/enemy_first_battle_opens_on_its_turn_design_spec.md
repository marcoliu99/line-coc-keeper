# A battle whose first turn is an enemy's plays that turn

## Problem

When an enemy had the highest DEX, the battle opened on its turn and nothing played it: the engine runs the next enemy only after someone ends a turn. Players could not act (it was not their turn), and the Keeper had to find out, plan and run the enemy's action itself. In a Haunting run the first six player turns all failed this way (skipping the enemy's turn, a plain check created for the wrong actor, missing ids), and players said the same from play: when an NPC goes first, the fight stalls until the Keeper is asked.

## Change

`initialize_combat` plays the first enemy's turn when the new battle opens on an enemy: it plans and runs it like advancing to an enemy does, and returns the result as `opening_enemy_turn` (with enemy hit points removed for players). If the action waits for a player's defence, that wait is created as usual; if it needs a ruling, that is reported there. This happens only when the call starts the battle, before anyone has acted, and only once: repeating the call reuses the enemies and plays nothing. The tool description and the Keeper's prompt say so, so the Keeper does not plan the same turn again.

## Not done

Mid-battle additions (`add_npc_to_combat`) do not play anything, and a battle that opens on an investigator is unchanged. Nothing lets an enemy that is not first act early.

## Tests

`tests/test_combat_wiring.py` (opens on the enemy only when it is first and only once) and the existing first-NPC tests in `tests/test_combat_mechanics_coverage.py` and `tests/test_combat_state_machine_integration.py`, now starting the battle with the enemy's roll in place.
