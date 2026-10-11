# Three rule errors: Fight Back ignored the weapon, a bonus die could roll 100, creation gave 5× the skill points

[繁體中文](fight_back_weapon_dice_pool_and_creation_points_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `25b6d13`.

## Problem

The main_v2 code review of 2026-10-11 found three places where the engine departs from Call of Cthulhu 7e in a way players feel.

1. **Fight Back ignored the weapon in hand.** The defence choice offered 反擊 only as 格鬥（鬥毆） and the counter always did `1d3`: `counter_damage` was declared on the action record but never set. An investigator holding an axe fought back with bare hands.
2. **A bonus die could turn 30 into 100.** `roll_percentile_with_dice_pool` compared the tens dice as digits. With a ones die of 0, a tens die of 0 reads 100 (the worst roll), yet it was the smallest digit, so a bonus die picked it; a penalty die, picking the largest digit, let the player off it.
3. **`/coc create` handed out EDU×20 occupation points and INT×10 interest points.** The rulebook gives EDU×4 and INT×2. With the 90% cap, a created investigator could max nearly every skill.

## Change

- **Fight Back uses the weapon in hand.** The counter option carries the weapon's skill, value, damage and DB policy. In hand means: the weapon of the defender's own last melee attack this fight; otherwise the carried melee weapon they are best with (weapons entries and carried items resolved against the catalog); otherwise bare hands, 1d3 + DB as before. The option's label names it (「反擊（小刀）」) so the button and the threshold hint say what the player swings; a typed 「反擊」 still picks it. The receipt names the weapon (`counter_weapon`). An extreme counter still does normal damage, as before.
- **Bonus and penalty dice compare whole rolls.** Each tens die is composed with the ones die into the roll it would make (00 → 100); a bonus die keeps the lowest roll, a penalty die the highest.
- **Creation points follow the rulebook:** EDU×4 and INT×2.

## Not changing

- Which weapon is "in hand" is inferred, not tracked; a player who wants a different weapon can say so and the Keeper rules it.
- The occupation pool may still be spent on any skill, and Credit Rating is not required; both are the Keeper's to police.

## Verification

- `tests/test_combat_flow.py`: a defender carrying a knife is offered 「反擊（小刀）」 at Brawl and counters for 1d4; the weapon of the last attack wins over a better-skilled carried one; no weapon means 反擊 at 1d3.
- `tests/test_dice_pool_and_creation_points.py`: a bonus die with ones 0 and tens 3/0 gives 30, a penalty die gives 100, ordinary rolls unchanged; a created investigator with EDU 60 / INT 60 gets 240 occupation and 120 interest points.
