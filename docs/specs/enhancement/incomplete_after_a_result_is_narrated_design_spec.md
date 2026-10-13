# An incomplete turn after committed results is told in the story

[繁體中文](incomplete_after_a_result_is_narrated_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `8d7a55a`. Found in the 2026-10-13 Scritch-Scratch 200-turn run (turn 21; the run before had the same at turn 67).

## Problem

The party walked the public footpath behind the cottage. The Executor rolled the Spot Hidden (an Extreme success), rolled Sanity for the four investigators who saw the hanging rats, and then left the turn incomplete: the scenario pairs Spot Hidden with Listen, and it did not roll the Listen. The Narrator was skipped for an incomplete turn, so the player read the four roll lines and 「這次行動尚未完整處理…剛才的結果已經記錄，但這次行動的後續守密人還沒處理完；請接著說明你想怎麼做」, with nothing of what the investigators saw.

## Change

`prompt_config.narrates_what_was_done(result, state)` is true for an incomplete turn that gave up on its own (`model_incomplete`, fallback reason `executor_no_action`), whose tools did not really fail (a search the turn's allowance refused does not count, a witness of a batched Sanity call that did not roll does, `turn_fallback.failed_a_tool`) and rolled or changed something that the players read (a public result line; a turn that only kept a KP-only note has nothing to tell, and its facts hold the secret) (`dice_rolled`, `resolved` or `state_changed`), with nothing waiting on anyone (no pending check or Luck decision, in the result or in the game state), no fight on or opened this turn, and no blocked scenario evidence. For that turn the Narrator runs (`narration_replaced` is false), the consistency step returns its text, and the roll lines still ride with it as for any turn. The Narrator's facts leave out the line of a search the allowance refused (internal bookkeeping), and say that the listed results have happened and to tell what they showed and hand the next step back to the players; what was not rolled or done must not be written as done, and it must not say the system failed. If the narration itself fails, the failure text is dropped (as for an explained refusal) and the fixed notice answers.

Such a turn does not go through the obligation gate (as an explained refusal does): a check the gate opened after the story would make the turn ineligible again and the story would be replaced by the notice, and the Executor has already settled this turn's mechanics.

Not changed: a turn with a failed tool, a pending wait, a fight, or no result keeps its notice.

## Verification

`tests/test_turn_fallback.py`: the predicate is true for a clean incomplete turn that rolled and false for each exclusion (nothing done, a failed tool, a pending check, a fight opened or on, another fallback reason, a waiting check in the game state); the narration is returned instead of the notice, the facts carry the instruction, and a failed narration still gets the notice; end to end, the reply is the Narrator's text.
