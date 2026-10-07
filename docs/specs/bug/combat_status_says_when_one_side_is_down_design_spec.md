# The combat status says when one side is down

## Problem

Nothing told the Keeper that a battle was over. In a Haunting run the rat pack had fallen, and the three investigators who could no longer act were down, yet the Keeper kept advancing the battle for many turns before it asked for a settlement preview. The status text, which the Keeper reads at the start of every combat turn, only listed the combatants.

## Change

`status_text` adds one line at the end when one side is out: "敵方已全數倒下，戰鬥可以結算了。" when every enemy is down, or "我方已全數倒下或離場，戰鬥可以結算了。" when every investigator and NPC ally is down or away. It says the battle can be settled; it does not settle it and does not name a tool, because the status is also what players see. Nothing is said while both sides still have someone standing.

## Not done

Nothing settles the battle on its own, and a preview is not refused or required: a battle can also end with enemies standing (they flee or surrender). The Keeper's prompt already says to preview and then confirm when combat ends.

## Tests

`tests/test_combat_flow.py`: no line at the start, the enemy line when the only enemy is down, the investigator line when the only investigator is away.
