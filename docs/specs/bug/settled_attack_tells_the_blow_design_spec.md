# An attack the engine settles on the spot is told in one line

[繁體中文](settled_attack_tells_the_blow_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c85efbd`.

## Problem

The Lightless Beacon 2026-10-11 fresh5 run (autoroll on): Thomas's shot at turn 39 dropped the youngling from 4 HP to 0, and the engine then played the hybrid's attack on Mei Lin; the player read 「Thomas，你的回合尚未到來；先等 Mei Lin 選擇並擲出眼前的防禦檢定。」 and nothing about the kill. At turn 49 Celia's punch landed for 3; the Executor answered `deferred`, the retry got the generic hint, and the player read 「Celia，你的拳頭尚未揮出」. Turns 33, 41, 52 and 54 likewise narrated nothing about the attack just resolved.

`turn_delivery.observe_tool` deliberately returns no public text for `declare_combat_action` (a fixed 「戰鬥機制操作已記錄」 line read as noise), so the only carrier of an attack the engine settled in the same call is the Narrator, which drops it whenever the turn's reply is a wait line or a deferral.

## Change

- **A settled attack gets one fixed line.** When `declare_combat_action` or `run_combat_action` returns `completed` with a `result` carrying `hit`, the observed outcome is public: 「⚔️ 命中，幼體（深海夢魘） 受到 9 點傷害，倒下了。」, 「⚔️ 這一擊沒有命中。」, 「⚔️ 命中，但傷害全被 … 的護甲擋下。」, 「⚔️ 對方反擊得手，Celia 受到 2 點傷害。」, or 「⚔️ 武器故障，這一擊沒有打出去。」; the provisional mark follows as for other combat outcomes. An attack still waiting on the player's roll says nothing, as before: the roll reply tells it.
- **A deferral over a settled attack is sent back with the answer.** `final_feedback` adds `attack_settled_not_deferred`: the attack is a committed change, answer `resolved` citing the declaration, and narrate the blow before who is next.

## Not changing

- The other combat tools still produce no fixed line; the narration tells the fight.

## Verification

`tests/test_settled_attack_tells_the_blow.py`: hit with the fall, hit without it, miss, armour, counter, malfunction; an attack waiting on the roll stays internal; `_attack_settled` is true only for a completed receipt with a result.
