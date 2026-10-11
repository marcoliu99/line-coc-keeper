# The Luck instruction was added to replies that already gave it

[繁體中文](luck_instruction_once_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `e4a9a1f`.

## Problem

When a roll waits on a Luck decision, the reply ends with a system line, 「骰值 N 還在等 Luck 決定：請按 Luck 按鈕，或輸入 /coc luck skip 保留原結果。」 `rerun8_player_replies_design_spec` left that line out when the narration had already given the command, and decided that by looking for the command and the roll as a whole number in the narration.

The narration gives the command almost every time and the roll almost never. In the four 100-turn runs of 2026-10-11 (Scritch Scratch, Haunting, Camp Sunny, Lightless) the line was added to 82 of 400 replies, and every one of the 82 already carried `/coc luck skip` (Lightless 46 of 100, Haunting 16, Camp Sunny 12, Scritch Scratch 8). The player read the offer, with its costs, and then read it again:

> …請使用 `/coc luck regular` 花費 28 點 Luck，或輸入 `/coc luck skip` 保留原骰結果。林間的後續仍未揭開。
> 骰值 93 還在等 Luck 決定：請按 Luck 按鈕，或輸入 /coc luck skip 保留原結果。

## Change

- **A reply that gives the way to keep the roll is told.** `InteractionRef.told_in` is true when the narration contains `/coc luck skip`, or the Luck button (「Luck 按鈕」, 「幸運按鈕」). The roll's number is not needed: what the player needs is the way to decide, and the narration seldom repeats the number. A narration that states a roll other than the real one (「擲出 60」 for a roll of 6) keeps the line, since it is the only text carrying the engine's number; a cost (「花費 6 點」) is not a claimed roll. The claim test looks at the number right after a roll label (擲出, 擲得, 骰出, 骰值, 原始骰值, with 是, 為 or a colon allowed) and is best effort: a narration that states the roll in other words is not caught.
- **A reply that does not give it still gets the line.** A narration that lists only costs (`/coc luck regular（26 點）`) or only talks about Luck leaves the player without the plain option, so the line is added.

## Not changing

- The line for a pending check (「已有待處理檢定／選擇，請使用檢定按鈕或 /coc check。」) and the consistency repair that adds the Luck instruction when the narration has none (`prompt_config`) are unchanged; the latter already treated `/coc luck` and the button as enough.

## Verification

`tests/test_rerun8_player_facing.py`:
- A narration with `/coc luck skip` and the costs but not the roll gets no line; so does one that names the button.
- A narration that lists only costs, or only mentions Luck, still gets the line.
- A narration that gives the command and the roll gets none, and one that gives neither gets it, as before.
