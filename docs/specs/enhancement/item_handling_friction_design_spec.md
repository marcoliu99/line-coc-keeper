# Items: a near-miss name still finds the item, and a no-op call never costs the turn

[繁體中文](item_handling_friction_design_spec_zh.md) | [Docs index](../../README.md)

Category: `enhancement`. Status: **implemented**. Base: `main_v2` at `ac6c943` plus PR #241.

## Problem

A review of the item path against real play found that most of what a player feels as "the bot forgot my stuff" or "I had to say it three times" came from the tools refusing or silently doing nothing, and the turn validator then throwing the whole turn away:

- `remove_carried_item` matched the stored text exactly. "鑰匙" never removed "地下室鑰匙", the tool still answered `ok: true` with the pack unchanged, and `turn_resolution._mutation_evidence` treated an unchanged pack as a failed mutation (`inventory_or_combat_not_verified`). The player read "請再說一次你的行動", said it again, and hit the same wall.
- `add_carried_item` for an item already carried was a silent no-op with the same consequence: an `ok: true` that voided the turn.
- Any refused inventory call anywhere in the turn voided the turn, so the Keeper could not even retry with the right name.
- `add_carried_item` and `remove_carried_item` were not offered to the narrator that runs after a settled check, so an item found by a successful Spot Hidden could not be registered until the player sent another message.
- `/coc sheet` printed melee weapons under 彈藥 with no count, which reads as a firearm.
- The weapon catalog had Chinese aliases for a handful of weapons only; 小刀, 匕首, 菜刀, 開山刀, 球棒, 指虎 did not resolve, so a declared attack with any of them paused the fight in `NEEDS_RULING`. A weapon registered on the sheet under one of its names (`weapons["小刀"]`) did not count as owned because only `carried_items` was scanned.

## Change

- `inventory.match_carried_items(items, reference)`: exact, then case-insensitive, then the entries that contain the reference or (when two characters or longer) are contained by it; a one-character entry such as 信 is inside too many unrelated words (信號槍) to prove it is the thing named. `remove_carried_item` removes the stored entry it names (`removed` in the receipt), refuses with `refusal: item_not_held` and the pack listed when nothing matches, and with `refusal: ambiguous_item` and the candidates when several distinct entries match. Every receipt carries `changed`.
- `add_carried_item` of an item already carried (case-insensitively) returns `ok: true, changed: false, already_carried: true` and writes nothing.
- `turn_resolution`: a no-op (`changed: false`) is neither evidence nor failure; a refused add or remove is ignored when a later call of the same tool on the same investigator for the same item (either spelling contains the other) succeeded: the Keeper retried. An unretried refusal after a real change, and a refused `transfer_item`, still void a completion claim, as before. A turn whose only inventory calls wrote nothing may end as `no_mechanics`. The legacy remove-then-add hand-off verifies against the entry a partial name removed.
- The Executor records no `inventory_change` event for a no-op.
- `add_carried_item` and `remove_carried_item` carry `resolved_check_followup`; the follow-up narrator's instruction names them.
- `Character.weapon_lines` prints 武器 for entries without ammunition and 彈藥 for the tracked ones, on both the sheet and the dynamic prompt line.
- `combat_weapons.json`: Chinese (and a few English) aliases for the melee and common firearm entries, each unique to one definition except the deliberately shared ones (手槍, 霰彈槍, and now 雙管霰彈槍, which names the 12, 16 and 20 gauge double barrels and so still needs the Keeper to pick the gauge). The weapons stay the CoC 7e table entries; a weapon the table marks as needing a ruling still returns `needs_ruling`.
- `combat_rules.resolve_weapon` resolves a reference that is not a catalog name by containment: the longest name or alias inside the reference, or that the reference is inside ("一把生鏽的小刀" is Knife, Small; "I have a large club" is Club, Large). Several definitions explained by names of the same length ("刀" is in every knife) stay `needs_ruling` with the candidates. Players write Traditional Chinese, which has no word boundaries, so containment rather than exact words is the match throughout; one-character aliases (矛, 弩) stay for the same reason.
- `combat_flow._weapon_actor_evidence` also scans the sheet's `weapons` keys, by the same containment, for a melee weapon's names.

`transfer_item` keeps its exact item match: [inventory transfer](../bug/inventory_transfer_design_spec.md) chose that deliberately for a hand-off between two investigators.

## Not done

- The rest of the transfer spec's open steps (removal quantity and reason, the server-side intent gate, items left in a room).
- Firearm references still need the exact sheet key for ammunition.

## Tests

`tests/test_item_handling_friction.py`: the matcher; a partial-name removal; refusals with the pack or the candidates; a duplicate add as a no-op; a retried refusal completing the turn and an unretried one not; a no-op ending as `no_mechanics`; the sheet labels; the aliases resolving and staying unique; the gauge-less double barrel needing a ruling.
