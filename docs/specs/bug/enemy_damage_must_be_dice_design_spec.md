# An enemy's damage must be a dice expression

## Problem

In a Haunting run the Keeper filled a flying knife's `damage` with `1D4+2；極限成功 6+1D4+2`, copying the scenario's sentence. The combat accepted it. At the first attack the engine could not parse it, the battle stopped in `NEEDS_RULING`, and the players could not go on.

## Change

When an enemy is added, every attack's `damage` is checked against the same dice grammar the engine rolls with. A string that is not a dice expression is refused before the fight is touched, with a message that says what to do: put only the dice in `damage`, and give the Extreme-success difference through `source.extreme_rule` (`maximum`, or `impale` for a piercing weapon). The Keeper corrects it and calls again; the other enemies in the same call are still added.

Saved battles are not checked on load.

## Not done

No attempt to guess which part of a free-text damage string is the dice, and no automatic choice of `extreme_rule`.

## Tests

`tests/test_combat_wiring.py` (the public initializer refuses it, changes nothing, accepts the corrected entry) and `tests/test_combat_engine.py` (an idle conversation is not started by a refused entry).
