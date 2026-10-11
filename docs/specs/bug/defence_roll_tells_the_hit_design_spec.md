# The line after a defence roll says the hit, the damage and the wound

[繁體中文](defence_roll_tells_the_hit_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c85efbd`.

## Problem

Haunting 2026-10-11 rerun2, turn 41: Corbitt's dagger attacked Clara; she chose Fight Back, rolled 96, the attack landed, she lost 9 of 12 HP (a major wound) and the engine rolled her CON check (autoroll, passed). What she read, after `/coc luck skip`, was 「Clara 的反擊落空，拳頭只掠過 Corbitt 乾枯的身影…現在輪到 George Finch 行動。已結算檢定：骰值 96，結果為「失敗」。」 Nothing said she had been hit. The 60 turns after never mentioned her 3 HP.

The engine had the words: `managed_checks._blow` builds 「命中，Clara Bennett 受到 9 點傷害」 and puts it in the roll reply and the resolved event's `combat_receipt.blow`. But a defence roll that waits on a Luck decision is told through the follow-up narration, whose only fixed line is 「已結算檢定：骰值 N，結果為「…」」; the Narrator may leave the blow out, and nothing checks for it. The major wound and its CON roll were not in the blow at all.

## Change

- **The resolved-check line carries the blow.** `turn_delivery.finalize` appends `combat_receipt.blow` to 「已結算檢定：骰值 N，結果為「…」。」 as 「⚔️ …」, so whatever the narration says, the player reads whether the blow landed and what it did.
- **The blow says the wound.** `_blow` ends with what a heavy blow did beyond its points: 「，倒地瀕死」, 「，倒地昏迷」, or for a major wound 「——重傷，還要過 CON 檢定」 when the CON roll is still the player's, and 「——重傷，CON 檢定過了，仍站得住」 / 「——重傷，CON 檢定失敗，昏迷倒地」 once the engine has rolled it (autoroll stores it under the blow's damage event).

## Not changing

- The Narrator is still asked to narrate the blow; the fixed line is the floor, not a replacement.
- A CON roll the player makes by button is narrated with that roll, as before (`_is_injury_roll`).

## Verification

`tests/test_combat_turn_friction.py`: a failed dodge against an 8-point claw tells 「命中，調查員p1 受到 8 點傷害——重傷，還要過 CON 檢定」 in the roll reply, in the resolved event's blow, and in the follow-up's 「已結算檢定」 line; the wound tail covers dying, unconscious, no wound, CON pending, CON passed and CON failed.
