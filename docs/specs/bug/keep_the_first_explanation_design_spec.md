# A retry that ran dry does not replace the first attempt's answer

[繁體中文](keep_the_first_explanation_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `6e76772`. Found in the 2026-10-13 Scritch-Scratch rerun3 (turn 22).

## Problem

The players ask to research in the village library; the scenario has none (the library is in Appleford, twenty miles away). The first Executor attempt worked this out after three searches and ended `blocked`, which the Narrator turns into a story answer (「穆斯科比沒有圖書館，最近的在 Appleford」). A `blocked` first attempt is a recoverable fallback, so the turn ran the Executor once more. That retry shares the turn's search allowance (five), so its second search was refused, it ended `incomplete`, and the retry's result replaced the first attempt's: the player read the fixed 「劇本裡沒有足夠的內容可以據以裁決這個行動」 instead of the answer the first attempt had.

## Change

In `_recover_blocked_turn`, when the first attempt was a validated `blocked` (an `unsupported_action`) and the retry did not end `blocked` and is still a fallback, the first attempt's result is kept. The recovery is still logged as `unresolved`, and the Narrator explains from the first attempt's evidence as it would have without a retry. A retry that blocks again, or that resolves the turn, behaves as before.

## Verification

`tests/test_turn_fallback.py`: a blocked first attempt followed by a retry whose search was refused is answered by the Narrator, with the fallback logged as `unsupported_action` and the recovery `unresolved`. The test fails without the change.
