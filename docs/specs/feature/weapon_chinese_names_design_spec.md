# Chinese names for the combat catalog's weapons

[繁體中文](weapon_chinese_names_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `30d8094`, after `coc7_glossary_expansion_design_spec.md`, which left weapons out.

## Why they were left out, and what changed

The one English–Chinese weapon table found first (Keeper_CoC-TRPG) merges weapons by calibre: its English labels are its own (「Small Auto」) and one Chinese cell can hold several weapons. It is not used. Two other sources are: the Chinese rulebook's weapon table as RuleWhisper reproduces it (zh-CN, 98 rows, in the rulebook's order), and a Traditional translation of the Keeper Rulebook's p. 401 table with the English rows beside it (TRPGLine-system-rule, MIT). Its rifle table is shifted by one row (the English `.30-06 Semi-Automatic Rifle` has no Chinese row) and some names are not the rulebook's (`瓶式槍` for bolt-action, `海狼棒` for blackjack), so it is a check on the first, not a source of its own.

The maintainer asked that the project's own `app/data/combat_weapons.json` be extended instead of adding a file.

## Change

Each weapon of `combat_weapons.json` the rulebook has a row for (48 of 49; bare fists are a skill) gains:

| Field | Meaning |
|---|---|
| `name_zh` | the rulebook's name in Traditional Chinese |
| `name_zh_cn` | the rulebook's name in Simplified Chinese, as RuleWhisper has it |
| `name_sources` | ids in `coc7_glossary/sources.json` (`s22` RuleWhisper, `s23` TRPGLine) |
| `name_zh_converted` | present when the Traditional spelling is the Simplified one converted with OpenCC, because the Traditional source gives a different name |
| `name_zh_generic` | present when the rulebook names a class of weapons, not a weapon (小型刀具, 大型劍, 伐木斧 for the catalog's `Axe`) |

- **The name is the rulebook's.** Where the two sources agree (12 weapons, ignoring variant characters) the Traditional text is used as written. Where they differ (34) the Simplified rulebook name is converted and flagged; the other table's wording is not added as a spelling. Crossbow and Vickers are not in RuleWhisper, so their Traditional name comes from the second source alone (2).
- **Combat is untouched.** `weapon_catalog()` drops the five new fields before it builds a definition. Each `name_zh` is also added to `aliases`, so a player who types the rulebook's name reaches the weapon; a test checks every one resolves to its own weapon (an automatic weapon or a garrote still needs the Keeper's ruling, as before).
- **The dictionary reads them, for a lookup by name only.** `glossary_entries()` adds a `weapon` entry per named weapon, kept in a separate `weapons` table read by `lookup_weapon()`. The `terms` table is the one matched in a scenario's text and used to rename the model's terms, and a weapon's English label is also a surname (「Detective Thompson」), a card game (「blackjack」) or a ship's bow, so no weapon is in it. The entry is: the catalog name and its turned-around form (「Spear, Thrown」 and 「Thrown Spear」) in English, `name_zh` and `name_zh_cn` in Chinese, canonical `name_zh`. Weapons with `name_zh_generic` are left out, so 「a small knife」 is not forced to 小型刀具 and 「a sword」 not to 大型劍. Aliases are not English spellings: 「axe」, 「revolver」 and 「rifle」 are too common to name one weapon.

Not added: weapons the catalog does not have (tasers, mace, grenades, mines, heavy weapons). They need rules, not names, and the catalog is the combat rules' data.

## Verification

`tests/test_coc7_glossary.py`: 45 or more weapons carry a name whose source ids exist; every rulebook name resolves to its own weapon; the catalog loads without the name fields; a weapon reads the same in English and in either script; a class-name weapon is not forced on a text. The 34 converted Traditional spellings have not been checked against a Traditional source.
