# Starting a fight counts as the turn's effect

## Problem
A player threatens a dormant enemy (for example Corbitt's body). The Keeper starts the fight with `initialize_combat` and writes the wake-up narration, but the turn is judged `resolved` with no verified effect (`missing_resolved_effect`). The player gets the generic "tool result was not accepted" reply and never sees the enemy rise.

## Change
In `turn_resolution._mutation_evidence`, a successful `initialize_combat` or `add_npc_to_combat` that the decision cites, with combat active afterwards and at least one combatant added between that call's before and after snapshots (counted per ID, so a second same-name ally counts), counts as a verified change. A call that only reuses an existing enemy adds nothing and does not count. A `resolved` decision with such a call becomes `resolved_without_check` and the narration is delivered.

## Not changed
Failed or uncited calls still do not count; no `sanity_check` is forced.
