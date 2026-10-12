# An action the scenario cannot do is explained in the story

[繁體中文](explain_blocked_in_story_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c46e341`. Found in the 2026-10-12 Scritch-Scratch rerun3 (turns 57, 69, 81, 93). The maintainer asked for it.

## Problem

The soak script asked the party to go to "the Muscoby library" twelve turns apart. The village has none. The first four times the Keeper said so in the story and named the nearest one (Appleford, twenty miles away). From the fifth time the Executor ended the turn `blocked` after searching the scenario, and the player got only 「這次行動目前無法繼續。依目前的劇本與場景狀態，這個行動無法進行；請改試別的做法。」, which says neither why nor where to go.

## Change

`prompt_config.explains_in_story(result, state)` is true when the turn is `blocked`, its fallback reason is `unsupported_action`, and it changed nothing: no pending check or Luck decision, no state change, no dice, no resolved check, no fight opened, no scenario-evidence block, no public refusal text of its own, and no battle running. For such a turn:

- the Narrator runs (`narration_replaced` is false) instead of the fixed text being written without it;
- the Narrator's facts block carries 【這個行動在劇本裡無法進行】: say why in the story, point only to the places and people the scenario has, invent nothing, and never write the action as done;
- `enforce_mechanic_check_consistency` keeps the Narrator's text. If the Narrator produced none, the fixed text is the fallback as before.

Every other blocked or incomplete turn is unchanged, and a running battle keeps its whose-turn guidance.

## Risk

The Narrator is trusted to follow the block; the Guard and the obligation gate still run over its text as for any reply. The retry that already runs once for `unsupported_action` is unchanged.

## Verification

`tests/test_turn_fallback.py`: such a turn is explained by the Narrator's text and the facts block says so; an empty narration falls back to the fixed text; any sign of a change (dice, resolved, state, combat opened, a pending check or Luck decision, an evidence block), another reason, an `incomplete` turn, or a running battle keeps the fixed text.
