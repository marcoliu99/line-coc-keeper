# The blow is told once, a Luck offer shows its roll, settled lines read alike, a follow-up can roll Sanity, and research ends in a result

[繁體中文](delivery_lines_followup_sanity_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `b0c11f3`.

## Problem

From the 2026-10-12 soak runs at b0c11f3:

- **The ⚔️ blow twice.** Lightless rerun3: all 25 combat results showed the blow on the roll card (「後續結果由守密人處理中……⚔️ …」) and again on the settled line after the narration.
- **Luck offers without the roll.** Scritch Scratch rerun2: 11 of 17 Luck prompts never stated the roll. The narration named `/coc luck skip`, so the engine's line 「骰值 N 還在等 Luck 決定」 was dropped, and the player chose blind.
- **Narration contradicting the engine.** Lightless turn 55: the engine said 「這一擊沒有命中」 for an attack the target dodged, and the narration had the fist land.
- **Two settled-line formats and English skill names.** Lines read either 「Rachel Ward 的偵查已結算：骰值 98，等級 失敗」 or 「已結算檢定：…」, with names such as 「的Mechanical Repair」 or 「的SAN」.
- **A horror revealed by a roll went without Sanity.** Scritch Scratch turn 7: a Spot Hidden follow-up showed the hanging rat corpses, but follow-up turns could not call `sanity_check`, so the roll came 12 turns later.
- **Research that never ended, and invented blockers.** Scritch Scratch: eight library visits ended at 「你開始尋找相關記錄」 with no roll and no result. 「鑰匙與門鎖細節仍待向議會確認」 appeared eight times, though the scenario says no such thing.

## Change

- **Blow told once.** `managed_checks._feedback` marks the blow as already told whenever the roll card carries it. The settled line no longer repeats it, but the narrator still receives it.
- **Luck line kept unless the roll is stated.** `InteractionRef.told_in` for a Luck decision drops the line only when the narration gives the keep command or button and also states the engine's roll.
- **Hit or miss is the engine's.** The narrator's 【這一擊的結果】 rule now says that a success on an attack, dodge or counter does not by itself mean a hit or a dodge. When it reads 「沒有命中」, the narration writes a miss, not a landed blow.
- **One settled-line format, Chinese names.** The skill-check observation now uses the follow-up's format, 「已結算檢定：X 的Y，骰值 N，結果為「…」。」. `presentation.skill_label` maps skills and characteristics to the sheet's Chinese names (Mechanical Repair → 機械維修, SAN → 理智, INT → 智力), on both lines.
- **Sanity in a follow-up.** `sanity_check` is offered to resolved-check follow-ups. The obligation gate reads that turn's observed outcomes (the follow-up Narrator's calls with the Executor's), so a SAN rule the Narrator already rolled is not applied again.
- **Two prompt rules (shared).**
  - A research action ends in a result this turn: a roll, or what was or wasn't found.
  - A limit the scenario doesn't write doesn't exist, so the Keeper doesn't invent blockers.

## Verification

- `tests/test_combat_turn_friction.py`: the settled line no longer repeats ⚔️, and the narrator still has the blow.
- `tests/test_rerun8_player_facing.py`: a Luck reply with the command but no roll keeps the line; one stating the roll drops it; a cost is not the roll.
- `tests/test_sanity_and_check_lines.py` and `tests/test_sanity_witnesses_told_skill.py`:
  - one format, with Chinese names (偵查, 智力, 理智);
  - follow-ups offer `sanity_check`;
  - the research and blocker rules are in the prompt.
