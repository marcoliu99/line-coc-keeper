# A Critical attack leaves the defender no Fight Back option

[繁體中文](no_fight_back_against_critical_design_spec_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **implemented**. Base: `main_v2` at `961c9ce`.

## Problem

When an enemy's melee attack is a Critical, no success level the defender can roll beats it, so Fight Back can only lose (see [dodge, Fight Back and ranged rules](bug-dodge-counter-tie-and-ranged-mechanics.md)). The older NPC attack tool dropped the Fight Back option in that case. The managed combat's own defense choice never did, so a player facing a Critical was still offered a Fight Back that is guaranteed to fail, and picking it wasted the turn on a certain loss.

## Change

`app/combat_flow.py` (`_defense_choice`): for a melee attack whose attack roll is a Critical, the defense choice offers Dodge only. Other attacks keep Dodge and Fight Back; ranged attacks (dive for cover or no defense) are unchanged.

## Not done

No change to how Dodge or Fight Back resolve, and no change to the Discord hint, which already omits the line for a Fight Back it cannot show a threshold for.

## Tests

`tests/test_combat_engine.py`: a Critical attack (a roll of 1) offers only Dodge and resolves through to a hit; an ordinary attack still offers Dodge and Fight Back.
