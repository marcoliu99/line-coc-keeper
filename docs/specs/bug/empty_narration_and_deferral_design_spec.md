# A turn that changed the game is not a wait, an empty narration still sends its lines, and two repeats removed

[繁體中文](empty_narration_and_deferral_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `4e63718`.

## Problem

From the 2026-10-11 soak runs:

- **「這次行動尚未完整處理」 after a turn that did its work.** Haunting rerun3 turn 31: a Sanity roll and the fight opened, then the Executor answered `deferred` (waiting on the next actor). The deferral failed verification because the turn had changed the game; the model's retry failed the same way; the player read the warning after 56 s although nothing was pending.
- **「守密人暫時無法完成敘事」 on Luck turns.** Haunting rerun3 turns 32 and 45: the narrator answered with only the decision JSON; the scrub left nothing and the reply was replaced by the failure text, losing the Luck line the player needed. No fallback was recorded.
- **A wrapper tail glued to its stray word.** Camp Sunny rerun4 turn 52 ended 「…還是進入 lobby？」}}.Begin」; the tail stripper needed whitespace before the word.
- **The roll instruction said twice.** 79 of 100 Lightless replies ended with 「已有待處理檢定／選擇，請使用檢定按鈕或 /coc check。」 after narration that already gave the command.

## Change

- **A deferral after the turn's own change is that change.** In `validate_resolution`, a deferral that fails verification becomes `resolved_without_check` citing this turn's tools that changed the game, when nothing new is pending for anyone, the actor has no pending check or Luck, and the turn left the game different from where it began. It is then held to every check a completed turn is held to (an unverified inventory change elsewhere still fails). No model retry is spent. A deferral that opened a check still fails as before.
- **An empty narration still sends its lines.** `_scrub_leaks` records a `narration_failure` fallback and leaves the text empty; the steps after it add the roll and Luck lines; `_finalize` sends the failure text only when nothing at all is left.
- **The stray word may follow the closers directly or after a dot.** `_STRAY_WORD` accepts `}}.Begin` and `}}Begin` as it did `}} bheidh`.
- **The generic roll line is not repeated.** `InteractionRef.told_in` treats a reply that already carries `/coc check` (the command the reply's own check line always includes) as having told the player.

## Not changing

- The Executor's existing feedback branches stay; they still serve the cases this conversion leaves out.
- No narrator retry on an empty narration.

## Verification

`tests/test_turn_consistency_handoff.py` (a deferral after a committed change is that change with no retry; enemy damage under a deferral is resolved; a compensated spend is never a wait); `tests/test_leaked_decision_and_redeclare.py` (empty narration recorded and left for the pipeline's lines, failure text only when nothing is left; `}}.Begin` and `}}Begin`); `tests/test_turn_fallback.py` (the roll line is not repeated).
