# CoC7 glossary in the dictionary

[繁體中文](coc7_glossary_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `7dc932c`.

## Problem

The self-learning dictionary (`app/dictionary.py`) started with about 50 English skill names, so a player who typed a Simplified or alternative Chinese spelling (侦查, 話術, 喬裝, 估價) or named a Mythos being got nothing back. There was no shared table for CoC7 terms beyond skills.

## Change

- **`app/data/coc7_glossary.json`**: 238 CoC7 terms in English, Traditional (`zh_tw`) and Simplified (`zh_cn`) Chinese, with alternative renderings and the sources they came from. It covers 15 characteristics, 86 skills, 38 rule terms, 71 Mythos beings and 28 tomes and places.
  - The sources are community translations pinned to a commit: the Foundry VTT CoC7 system (zh-TW, zh-CN), SealDice's CoC7 template, COC不全书, the HKTRPG dice bot, the Roll20 sheet, the Komica wiki and a TRPG character sheet.
  - Each entry has a `canonical` name, the one this project writes:
    - **Skills:** this project's own skill name (for example 話術 and 话术 → 快速交談, 喬裝 → 偽裝, 鎖匠 → 開鎖), so existing sheets keep their names.
    - **Other terms:** the Traditional name, except where the project already has its own wording (極難成功, not 極限成功; 守密人, not 守祕人).
  - Two zh-CN-only entries were given their plain Traditional spelling (永久性瘋狂, 食屍教典儀), as were six marked `zh_tw_converted`.
- **Dictionary.** Every spelling of a skill entry resolves to its canonical skill through `lookup_skill`, and so through `skill_aliases.canonical_skill_name`, which checks and roll lines already use. Other entries form a new `terms` table, read with `lookup_term`.
  - A one-character spelling (斧, 劍) is never used.
  - A spelling two entries claim is never used.
  - A characteristic's name is never read as a skill.
  - What a deployment has stored or learned still wins over the built-in tables, and a dictionary saved before this change gains them on load.

## Open points

- Where sources disagree, the most common rendering is first and the others are alternatives. Examples: Extreme success 極難成功/極限成功; HP 生命值/耐久值; Innsmouth 因斯茅斯/印斯茅斯.
- 「不可名狀的教團」 for *Unaussprechlichen Kulten* was mapped by meaning and should be confirmed.
- Nothing yet shows the terms table to the Keeper. The table is ready for the scenario-name rules in `scenario_names_consistent_design_spec.md`.

## Verification

`tests/test_coc7_glossary.py`:

- Every entry has a canonical name and known sources.
- Skill entries resolve to project skill names, and other spellings reach them.
- One-character spellings and characteristics are not skills.
- Terms read as one Traditional name.
- A stored dictionary keeps what it learned and gains the glossary.
