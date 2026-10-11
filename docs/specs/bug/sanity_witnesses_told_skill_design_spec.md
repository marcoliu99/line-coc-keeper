# Sanity for every witness in one call, a backticked roll command counts, and the settled line names the roll

[繁體中文](sanity_witnesses_told_skill_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `707ad26`.

## Problem

From the 2026-10-12 soak runs at 707ad26:

- **Only the actor lost Sanity.** In all three runs the prompt said 「在場目擊的每一位調查員各擲一次」, yet the Keeper called `sanity_check` once, for the player who acted; the others watched the same horror and lost nothing.
- **The roll hint came back.** #278 drops the generic 「請使用檢定按鈕或 /coc check」 line when the reply already gives the command, but a command written as the code span `` `/coc check` `` did not count, and 33 replies carried the line twice.
- **A roll after Luck did not say what was rolled.** The settled line read 「已結算檢定：骰值 52，結果為「一般成功」」 with no investigator or skill, and after a Luck decision it is the only line the player reads.

## Change

- **`sanity_check` takes `witnesses`.** An optional list of the other investigators who saw the same sight. The tool gateway turns it into one plain `sanity_check` per witness, each rolled or registered, recorded and told exactly as if the model had called it, skipping a name that is the main investigator or already rolled. The Keeper gets back the first result with a `witnesses` list. The prompt and the stat-block hint ask for one call with the witness list. Who saw it stays the Keeper's judgement; there is no sight detection.
- **A backtick ends the command.** `InteractionRef.told_in` accepts `` ` `` after `/coc check`.
- **The settled line names the roll.** 「已結算檢定：Marco 的偵查，骰值 52，結果為「一般成功」。」, from the resolved check's own `investigator` and `skill`.

## Verification

`tests/test_sanity_witnesses_told_skill.py`: one call rolls each witness once (the actor's other spelling is not rolled again) and records a fact and an outcome for each; a lone call is unchanged; the schema offers the list; a backticked command counts and `` `/coc checkpoint` `` does not; the settled line names investigator and skill. Updated `tests/test_combat_turn_friction.py` and `tests/test_sanity_and_check_lines.py`.
