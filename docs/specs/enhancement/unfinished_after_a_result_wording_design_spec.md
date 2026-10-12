# A turn that stopped after rolling dice no longer says the scenario lacks content

[繁體中文](unfinished_after_a_result_wording_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c46e341`. Found in the 2026-10-12 Camp Sunny rerun7 (turns 76 and 80).

## Problem

Turn 76: the party fled by car; the Executor searched the scenario (the search returned the 「Fleeing Camp Sunny」 section), rolled the Driving check (failed), fetched Billy's stat block, then ended the turn `incomplete`. The player read the roll, then 「這次行動尚未完整處理。… 劇本裡沒有足夠的內容可以據以裁決這個行動，守密人沒有替它編造」. Turn 80 was the same after five Sanity rolls. The scenario does have the section and the roll stands, so the sentence is false and tells the player to ask for something else.

## Change

`turn_fallback.guidance(reason, hints, acted=...)`: when the turn rolled dice, resolved a check or changed the game (`dice_rolled`, `resolved`, `state_changed`) and the reason is one whose wording blames the scenario (`no_scenario_evidence`, `executor_no_action`, `unsupported_action`), the player is told 「剛才的結果已經記錄，但這次行動的後續守密人還沒處理完；請接著說明你想怎麼做。」 instead. The scene hints still follow. A turn that did nothing keeps the old wording, and a battle keeps its own guidance.

## Not changed

Why the Executor ends such a turn `incomplete` after the dice are rolled is still unknown: the log did not say. `retry_after_lookups_design_spec.md` adds the model's reason to the text log.

## Verification

`tests/test_scene_hints.py`: each of the three signs of an action gets the new wording and none of 「劇本裡沒有足夠的內容」; a turn that did nothing keeps the old text.
