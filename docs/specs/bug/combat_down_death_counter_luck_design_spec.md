# The unconscious stay down, a kill is said, a settled counter is shown, and defence Luck only when it matters

[繁體中文](combat_down_death_counter_luck_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `707ad26`.

## Problem

From the 2026-10-11/12 soak runs at 707ad26:

- **An unconscious investigator kept fighting.** Haunting rerun4: George failed the major-wound CON roll at turn 38 and attacked at 39 and 43. With autoroll on, `request_injury_check` set the injury but not the combatant's `defeated`; only the manual roll path did.
- **An enemy's death went unsaid.** Lightless rerun2 turn 56: a 20-point shot killed the hybrid and ended the fight; the line read 「受到 20 點傷害」 and the narration never said it fell. `_wound_tail` read only an investigator's injury record.
- **A counter that hit was never shown.** Haunting rerun4 turn 42: James chose 反擊, the engine rolled it, Corbitt lost 3 HP; the player read 「已選擇「反擊」。已依系統紀錄處理」. Only a no-defence choice took the path that tells the blow.
- **Luck offered where it changed nothing.** Lightless rerun2: six defence rolls offered Luck when the attack missed anyway, when nothing beats a Critical, or when the roll already kept the blow off.

## Change

- **Down is down.** The autorolled CON failure sets the combatant's `defeated` as the manual roll does.
- **A kill is said.** `_wound_tail` adds 「，倒下了」 when the damage receipt says the target was defeated and no investigator injury applies.
- **A choice the engine settled shows the blow.** Any choice whose outcome is completed with no roll left for the player takes `_settled_without_roll` (the ⚔️ line and the Keeper's narration), not only 不閃躲. Its event records the defence roll the engine made, so history and narration never claim no roll happened.
- **Defence Luck only when it changes the blow.** `dice.useful_defence_luck`: for a Dodge or Fight Back, no offer when the attack misses a Dodge anyway, when no tier beats it, or when the roll already does; otherwise only tiers at or above the one needed. Applied on the player-rolled and the autorolled defence. Other rolls keep every offer (Luck stays a product decision).

## Verification

`tests/test_combat_down_death_counter_luck.py`: autorolled CON failure marks the investigator defeated; the kill line; the defence Luck filter cases; an engine-settled counter shows ⚔️ (fails on the old condition). Updated `tests/test_combat_turn_friction.py`: a Regular dodge is no longer offered against a Hard attack.
