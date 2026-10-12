# A fight that began cleanly is not followed by a request to start over

[繁體中文](fight_started_state_conflict_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `112a22a`. Found in the 2026-10-12 Haunting rerun3 (turn 28).

## Problem

The party opened the wall and Corbitt rose. The Executor called `get_enemy_stat_block` and `initialize_combat`, and the fight was running. Its handoff then named a check or Luck decision nobody had (the text log now says it: 「要求處理的檢定／Luck 決定不存在或已過期」, validation code `pending_identity_mismatch`), so the turn was classified `state_conflict`. The player read 「戰鬥已然開始，Felix，你先行動」 and, right after it, 「這次行動尚未完整處理…目前的狀態和這次行動不一致；請依目前劇情重新操作」, which tells the table to start again in the middle of a fight that is going on.

## Change

`prompt_config.fight_started_cleanly(result)` is true when the fight was opened this turn (`combat_opened`), the fallback reason is `state_conflict` with validation code `pending_identity_mismatch` (the named check does not exist; an unverified cancellation, `cancellation_not_verified`, is also a `state_conflict` but keeps the notice), every tool call succeeded, no check or Luck decision is still waiting in the game state (the public result hides a private one), and the turn left nothing pending and rolled nothing (no pending check or Luck decision, no dice, no resolved check). The consistency step then returns the Narrator's text as it is, without the unfinished-turn notice. Any other incomplete turn keeps the notice, as does one that rolled dice or left a check waiting.

The fallback is still recorded as `state_conflict`, so the soak reports keep counting it.

## Verification

`tests/test_turn_fallback.py`: the narration alone is returned for that turn; a pending check, a roll or a resolved check, or another fallback reason, keeps the notice.
