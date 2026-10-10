# Pregenerated investigators bring their own weapon numbers

## Problem

The Haunting's pregenerated Evelyn Carter carries a .38 revolver. The reviewed weapon catalog (`app/data/combat_weapons.json`) has no .38: its pinned source, the Foundry CoC7 wiki weapon list, lists .22, .25, .32/7.65mm, Luger and .45 handguns only. A 200-turn run on `d6a03bb` (2026-10-09) therefore played her gun as the catalog's .32/7.65mm revolver, 1D8 instead of the 1D10 her sheet gives. Her damage was lower than the sheet says, and the narration named a gun she did not carry. The engine could already take an owned weapon's own definition ahead of the catalog (`Character.weapon_instances`, read by `managed_combat._owned_weapon_evidence` and `combat_flow._weapon_actor_evidence`), but nothing ever wrote one.

## Change

- `app/pregen_weapons.py` builds one reviewed definition per sheet weapon from the numbers the sheet writes (`definition_row`):
  - The skill comes from the sheet's skill, or failing that from the weapon's name: 手槍／左輪 is a handgun; a rifle, shotgun, SMG, machine gun, bow or throw, or one of the Fighting specialisations, maps the same way. The damage must parse in the catalog's damage grammar (`combat_rules.validate_damage`).
  - A trailing 「+DB」 (or 「+半DB」) on the damage sets the damage-bonus policy; without one, a gun adds none, a thrown weapon half and a melee weapon the full bonus.
  - A gun is a `single_shot` attack that spends one round per shot and impales on an Extreme success. It takes its range (yards, or metres converted), capacity and malfunction from the sheet. A malfunction written 「00」 is 100.
  - A thrown weapon is a ranged `single_shot` with no ammunition, its range the thrower's STR/5 unless the sheet gives one.
  - What the sheet leaves out comes from the catalog entry the name resolves to, if any, including a slow weapon's reload cadence (`rounds_per_shot`, a crossbow's two rounds).
  - A weapon whose numbers cannot be played (no damage, a range-banded shotgun damage such as 4D6/2D6/1D6, no skill a name can imply) gets no definition and resolves from the catalog as before.
  - Aliases are the sheet's original name and, for a revolver or handgun, 左輪／手槍 and their English forms, so a player who says 「左輪」 fires this gun.
- The definition is pinned to the text it was read from: source `scenario:pregen-sheet`, the text's sha256 and the date. `combat_rules.parse_weapon_definition` now accepts a `scenario:` source URL.
- Both import paths carry the numbers:
  - A hand-written role sheet's weapon block reads 技能／傷害／射程／彈容量／故障.
  - The scenario extraction's report tool gains a `weapons` list (name, skill, damage, range, capacity, malfunction, copied from the card only).
- `pregen_to_character` stores each definition as an owned instance (`weapon_instances[name] = {definition_id, catalog_version, scenario_definitions: [row]}`). A gun with no stated capacity is loaded from the definition's capacity.
- `managed_combat._owned_weapon_evidence` finds that instance when the reference is not its exact name but names its definition (「左輪」 for 「.38 左輪」), as long as exactly one owned instance does.

## Not done

- Pregens already extracted and saved before this change carry no definitions; extracting the scenario's pregens again picks them up.
- When a hand-written sheet and the extraction both list a pregen, the hand-written weapons still win as a whole; a definition only the extraction found is not carried over.

## Tests

`tests/test_pregen_weapons.py`; the shot fired by 「左輪」 with the sheet's 1D10 is in `tests/test_combat_wiring.py`.
