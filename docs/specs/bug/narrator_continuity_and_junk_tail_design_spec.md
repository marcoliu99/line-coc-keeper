# The fallen stay down, the past is not replayed, and a wrapper tail is cut

[繁體中文](narrator_continuity_and_junk_tail_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `6b59dc5`.

## Problem

Three slips from the 2026-10-11 soak runs, all in what the player reads:

- **A fallen enemy kept coming.** Lightless Beacon fresh5: Thomas's shot dropped the youngling at turn 39; at turns 44 and 56 the player still read 「幼體與深潛者混血仍在雨幕中逼近」. The status block the Narrator sees lists the youngling's row as 「HP 未公開（倒下）」, which was not enough.
- **The blow was handed to the wrong enemy.** Turn 39: the hybrid's swing at Mei Lin was narrated as 「幼體的利爪撕向 Dr. Mei Lin」. A defence check was registered without an `action_context`, so the resolved-check block read 「行動情境：未提供」 and the Narrator guessed the attacker.
- **The landing was narrated again, and the opening once more.** Turns 60 and 61 (「我們登上 Beacon Island」, said twice after the fight on the island) each got a fresh landing; turn 93 (the opening premise restated) got the opening narration from the top, date and all.
- **A wrapper tail reached the player.** Fresh5 turn 1 ended 「…又為何前往羅克波特？」}} bheidh」 and Camp Sunny rerun3 turn 57 ended 「…你要怎麼查看？」}}」: the model wrapped its narration as a JSON string and the closers survived. `strip_leaked_decision` only removes a decision object, which this is not.

## Change

- **The status block says it in one line.** When some enemies are down and others stand, `combat.status_text` adds 「已倒下、不會再行動：幼體（深海夢魘）。仍在戰鬥的敵人：深潛者混血。」 after the rows. All down or all standing: unchanged.
- **A defence check names its attacker.** The defender's choice and the Dodge / Fight Back roll that replaces it are both registered with `action_context` 「深潛者混血 攻擊 Dr. Mei Lin；這是 Dr. Mei Lin 的防禦」 (「開槍射向」 for a shot); the resolved event is built from the roll, so the resolved-check block carries it.
- **Two Narrator rules.** An enemy marked （倒下） or listed as 已倒下 does not close in, attack or act; the attacker of a blow is the one the resolved-check block names. A player restating something already past (landing again after the fight on the island, the opening voyage) is not replayed or re-opened: one sentence that it is behind them, then the present place, time and what just happened.
- **The wrapper tail is cut.** `presentation.strip_junk_tail`, run with `strip_leaked_decision` at the start of the reply pipeline, removes the closing `}`/`]` at the end that nothing in the text opened (only as many as are unmatched), a stray short Latin word after them, and a closing 」 left dangling by the cut. Balanced braces before them and a quote the narration opened stay.

## Not changing

- Luck prompts in combat stay as they are (the player's decision).
- No rewrite of narration after the fact: the status line and the rules are inputs; the model still writes the prose.

## Verification

`tests/test_leaked_decision_and_redeclare.py` (the two soak tails, a quote kept, balanced text untouched, the pipeline step); `tests/test_combat_flow.py` (the status line with one enemy down, absent with none or all down); `tests/test_combat_turn_friction.py` (the defence check's context names the attacker); `tests/test_narrative_boundary_prompts.py` (the Narrator rules are in the prompt).
