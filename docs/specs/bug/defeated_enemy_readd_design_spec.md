# Re-adding a defeated enemy's name: confirm it, and keep the two apart

[繁體中文](defeated_enemy_readd_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `1a169a3`. Depends on #118 (`combat.add_combatant`).

## Problem and evidence

The duplicate guard only matches enemies that are **not defeated** (`combat.find_live_enemy`). Adding the name of an enemy that has already been defeated, directly or through a `/coc index` alias, therefore always creates a new combatant with full HP. That is sometimes right and sometimes a mistake, and nothing distinguishes the two:

- **Right:** the first 深潛者 died, and a second one climbs out of the water.
- **Wrong:** the Keeper re-searched the scenario mid-fight, didn't notice the 深潛者 was already defeated, and added it again. The dead monster is back at full HP.

A second defect makes even the *right* case go wrong. Both combatants get the same `display_name`, and `_find_combatant`'s exact match returns the **first** in `state.combat.order`, which is the defeated one when DEX is equal. Reproduced on `main_v2`:

```text
add_npc("深潛者", 50, 10); first.defeated = True; add_npc("深潛者", 50, 10)
enemy:…90e9efdc 深潛者 hp=10 defeated=True
enemy:…da62bb4d 深潛者 hp=10 defeated=False
damage_combatant("深潛者", -3) -> hits …90e9efdc (the defeated one), hp 10 → 7
```

After a same-name re-add, every name-based tool call (`damage_combatant`, `apply_combat_damage`, effects that target by name) can land on the corpse, while the live monster is untouched.

## Decision (from review)

Keep allowing a defeated enemy's name to be added again, but **say so**, and make the two combatants distinguishable.

## Scope

### 1. The add reports a defeated namesake

`combat.add_combatant` also looks for a **defeated** enemy matching the name or any index alias, using the same exact-match alias expansion as `find_live_enemy_by_any_alias`. When one exists, the combatant is still added, and the caller is told:

- Keeper `add_npc_to_combat`: the response `note` (appended after any index-HP note) says「「深潛者」先前已在這場戰鬥中被打倒；已加入一隻新的「深潛者 2」（HP 10）。如果這其實是同一隻，請用 damage_combatant 把「深潛者 2」的 HP 歸零，並依原本倒下的狀態敘事。」
- `/coc combat addnpc`: the reply adds the same first sentence for the KP.

### 2. The new one gets a distinct name

When a combatant with the same `display_name` is already in `state.combat.order` (defeated or not, which only allies can be, since live enemies are de-duplicated), the new combatant's `display_name` gets the next free number: `深潛者 2`, `深潛者 3`, …. `name` stays unchanged, so index lookups, HP canonicalisation and `find_live_enemy` keep working on the base name.

### 3. Name lookups prefer the living

In `_find_combatant`'s exact-match pass, when several combatants match, return the first **non-defeated** one, and fall back to a defeated one only when no living combatant matches. Healing a defeated combatant by exact `combatant_id` or numbered `display_name` still works, because those match exactly one.

## Testing

1. Re-adding a defeated enemy's name, directly and through an index alias: a new combatant with full HP, `display_name` `<name> 2`, and a `note` in the Keeper response and the handler reply.
2. Re-adding a name no one had: no note, no number.
3. A live enemy's name: still reused, unchanged from #118.
4. With a defeated `深潛者` and a live `深潛者 2`, `damage_combatant("深潛者", -3)` hits the live one; `damage_combatant("深潛者 2", …)` hits the same one; the defeated one's HP is unchanged.
5. Allies: two allies named 嚮導 get `嚮導` and `嚮導 2`; neither note fires, since the notice is only for enemies.

## Limits

- There is still no tool to *remove* a mistaken combatant; zeroing its HP is the correction path the note gives. A removal tool is out of scope.
- Numbering applies only when the display name would repeat. Re-adding through an index alias (`柯比特` after a defeated `Walter Corbitt`) already reads differently, so it isn't numbered, but a later lookup by the *other* name (`Walter Corbitt`) still exactly matches only the defeated one. Name lookups don't expand index aliases; doing that for combat targeting is a separate change.
- Numbering applies only to combatants added after this lands. Existing saved fights with two identical display names still benefit from step 3's living-first lookup.
