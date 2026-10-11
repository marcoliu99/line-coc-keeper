# Sanity on first sight, rolls the player can read, and no second roll for a settled goal

[繁體中文](sanity_and_check_lines_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `4e63718`.

## Problem

From the 2026-10-11 soak runs:

- **No Sanity check when a horror appears.** Lightless rerun3 made no `sanity_check` at all although the youngling's stat block reads "Sanity Loss: 1/1D4"; Camp Sunny rerun4 none for Billy (1D6/1D20); Haunting rerun3 one investigator of four when Corbitt moved.
- **A Sanity loss the player could not see.** The autorolled line read only 「Helen Mercer 的檢定已結算：骰值 30，等級 一般成功。」; the narration said 「看不出別的明顯危險」. A skill line also omitted which skill was rolled.
- **The same goal rolled again.** Scritch Scratch rerun5: a Chemistry hard success at turn 18 rolled again at turn 78, failed, and the player read 「你還無法判明」; Lightless rerun3 rolled Spot Hidden on the same wreckage five times. In CoC 7e a failed roll is pushed, not retried, and what is known needs no roll.
- **A mixed skill name rolled at the wrong value.** 「偵查與聆聽」 matched both skills and took the first on the sheet: Spot Hidden 65, rolled at Listen 25, 30 read as a failure.
- **Drive rolled as Pilot.** 「駕駛」 is Pilot (1%) by design; a car chase rolled it instead of 「汽車駕駛」.

## Change

- **Sanity per witness on first sight.** The Keeper rules say: every investigator present rolls once, with the scenario's loss, the first time; the same sight or kind of monster seen again costs no roll. `get_enemy_stat_block`'s hint repeats it for a block that lists a Sanity loss. The model reads the block; no text matching decides it.
- **The roll lines say what was rolled.** `observe_tool`: 「Helen 的理智檢定：骰值 30／40，一般成功，SAN −1（40→39）。」 (or 「SAN 沒有損失。」); a skill line names the skill: 「Alan 的偵查已結算：…」.
- **A settled goal keeps its result.** The Keeper rules say: the same investigator on the same task keeps the earlier result; on a failure offer a push, never a new check; what is already known needs no roll.
- **A name covering two skills takes the better one.** `resolve_skill_value`'s substring fallback returns the highest value among the skills the name matches, instead of the first. An exact name is unchanged.
- **The skill field says one skill, and drive is not pilot.** The `skill_check` schema: one skill per check, no combined names; 「汽車駕駛」 for a car, 「駕駛」 for aircraft and boats.

## Not changing

- No parser for Sanity lines in scenario text; the stat block is given to the model whole.
- Luck offers stay as they are (a product decision).

## Verification

`tests/test_sanity_and_check_lines.py`: the Sanity line with and without loss; the skill line names the skill; a combined name takes the better skill and an exact name is unchanged; the Keeper rules and the skill field carry the guidance.
