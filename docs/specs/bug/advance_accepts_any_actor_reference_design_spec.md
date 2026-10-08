# advance_combat_turn accepts any actor reference; refusals say what to do

## Problem
In a real run `advance_combat_turn` was refused 12 times because the Keeper passed the character ID or name instead of the combatant ID, and `declare_combat_action` was refused 39 times with "advance initiative" after the actor had already acted. Each refusal cost a tool call and often the whole turn.

## Change
- `combat_flow.advance_combat` resolves `actor_id` with `combat.find_combatant` (combatant ID, character ID or name) and compares it with the current actor. A wrong actor's refusal names the current actor and the ID to pass.
- The "already completed this turn" refusal in `declare_action` tells the Keeper to call `advance_combat_turn` with the actor's combatant ID and a new event ID.

## Not changed
Only the current actor can advance; the skip rules and ownership checks are unchanged.
