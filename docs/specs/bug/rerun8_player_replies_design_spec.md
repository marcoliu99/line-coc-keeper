# Four replies from one Codex run: a refusal given up on, a fight opening lost, a Luck offer read three times, a refusal after the fight

[繁體中文](rerun8_player_replies_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `a4ef873`.

## Problem

The four-investigator Haunting run on `05cbbb2` (Codex `gpt-6-luna`, 200 turns, autoroll on, 2026-10-10) finished every turn, but four kinds of reply read wrong to the player:

| Turns | What the player read | Cause |
|---|---|---|
| 3 | 「處理這個行動的工具失敗了」 | The scenario says to roll 1D100 against APP or Credit Rating for Mr. Dooley's reaction. `roll_dice` refused the public 1D100 and said to use `skill_check` or a secret roll (`d100_against_a_characteristic_is_a_check_design_spec`). The Keeper ended the turn `incomplete` instead. A `model_incomplete` final never got the in-conversation retry, so the refusal's guidance was never used. In another turn the same refusal was followed and worked. |
| 33 | Only 「你的這次行動尚未執行，請先等待George Finch完成目前的行動」 | Clara's line opened the fight. Initiative put George first, so her attack was `deferred`. The Narrator described Corbitt rising, but `enforce_mechanic_check_consistency` replaced every `deferred` reply with the wait line. |
| 4, 34–55 and most checks | The Luck offer two or three times, ending in 「既有骰值 86 仍等待 Luck 決定…；不要重擲」 | The narration offers Luck, `_pending_luck_instruction` adds the command list when the narration has no `/coc luck`, and the delivery envelope adds its own Luck line on top. The player never re-rolled, so 「不要重擲」 read as a scolding. With autoroll, the `skill_check` receipt also added 「…的檢定已結算：骰值 93」 beside the Luck wait. Turn 44 also showed 「`:luck`」: the id cleaner removed `check-<hex>` and left its `:luck` suffix. |
| 56 | 「這次行動目前無法繼續…依目前的劇本與場景狀態，這個行動無法進行；請改試別的做法。」 | The enemy was already at HP 0. The Keeper confirmed the settlement and blocked the attack, and the reply used the generic fallback. |

## Change

- **A refusal gets one more try.** In the Executor's `final_feedback`, a `model_incomplete` final gets the one existing final retry when this turn's only failed calls refused without writing state and every successful call was a look-up (`INFORMATION_QUERY_TOOLS`). The note says the refusal named the right tool or argument, and asks the Keeper to follow it, or to stay `incomplete` with a reason. A refusal that wrote state, such as a combat action paused on a ruling, does not qualify. The codex provider already limits final retries to one per conversation.
- **The line that started the fight keeps its scene.** A `deferred` turn may change state only by setting up the fight (`turn_resolution._setup_only`). When it did (`state_changed`), the reply is the narration followed by the wait line. A plain deferral is still the wait line alone.
- **One Luck line.** The envelope's Luck line becomes 「骰值 86 還在等 Luck 決定：請按 Luck 按鈕，或輸入 /coc luck skip 保留原結果。」 It is left out of the projected text when the reply already contains `/coc luck` and that roll as a whole number that is not a cost (a roll of 6 is found in neither 「26 點」 nor 「花費 6 點」). The `skill_check` receipt line is dropped while a Luck decision holds the roll, because the Luck line already shows the roll. The id cleaner removes a `:luck` suffix and code quotes along with the id, and tidies a colon left before end punctuation.
- **Attacking after the fight.** A `blocked` turn whose only calls settled the fight (preview, confirm, status; all succeeded) and left no fight running, with someone still standing, answers 「戰鬥已經結束，這一擊不必再出手了。接下來想做什麼？」.

## Not changing

- Fallback replies that follow a failure still say 「不要重擲」: there, re-doing the action is a real risk.
- The Narrator may still mention the Luck cost in its own words. The point is that the command list is not repeated.

## Verification

`tests/test_rerun8_player_facing.py`:
- A refusal after look-ups qualifies for the retry. No refusal, a successful mutation, or a refusal that wrote state does not.
- A deferral that set up the fight keeps the narration and the wait line. A plain deferral is the wait line.
- The Luck line is left out when the reply gives the command and the roll, and has no 「不要重擲」. A roll held for Luck is not called settled. A `check-<hex>:luck` id in code quotes is removed cleanly.
- A blocked turn that confirmed the settlement says the fight is over, unless the whole party is down.
