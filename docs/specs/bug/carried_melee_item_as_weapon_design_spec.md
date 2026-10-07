# A carried melee item can be used as a weapon in combat

[繁體中文](carried_melee_item_as_weapon_design_spec_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **implemented**. Base: `main_v2` at `0c2b022`.

## Problem

In a real *The Haunting* run the investigator 承翰 carried a 警棍 (a police baton) and, on his turn, attacked the enemy with it. The reply was "這個行動無法進行". Two things in the combat engine caused it:

- The reviewed weapon table has no name for it. The matching entry is "Club, Small" (a night stick), whose only alias was "small club".
- `declare_action` accepts any weapon except unarmed only if the character has an owned entry (`weapons` or a weapon instance). An item in `carried_items` is not one, so a baton the character really holds was refused as "not owned".

## Change

- `app/data/combat_weapons.json`: "Club, Small" also answers to `nightstick` and `警棍`.
- `app/combat_flow.py` (`_weapon_actor_evidence`): a melee weapon that uses no ammunition counts as owned when one of the investigator's carried items contains the weapon's name, the declared reference or one of its aliases (a fuzzy match on purpose, so "老舊警棍" counts as a 警棍). Accepted trade-off: an unrelated item whose name merely contains a weapon name ("baseball batting gloves") also counts as that weapon.

## Not done

Guns and anything that uses ammunition still need an owned entry with ammunition. Other carried items (a cane, a chair) still have no weapon definition and still need the Keeper's ruling. No new weapon table entries beyond the alias.

## Tests

`tests/test_combat_engine.py`: an investigator who carries a 警棍 declares an attack with it and gets the "Club, Small" definition; the test fails without the ownership change.
