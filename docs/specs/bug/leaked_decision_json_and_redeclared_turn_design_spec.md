# A leaked decision object reached players, and a finished turn was reported as deferred

[繁體中文](leaked_decision_json_and_redeclared_turn_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `0908a16`.

## Problem

Two things from the 2026-10-11 soak runs were still reaching players.

**1. An internal decision object in the reply.** Scritch Scratch turn 91 sent the player a reply that began with the Keeper's own turn decision, a JSON object with `"disposition"`, `"reason"` and tool evidence, followed by the narration. The logs do not show which stage produced it, so the fix cannot be at the source.

**2. A declared attack repeated for a character who had already acted.** In Camp Sunny (three times) the Keeper correctly refused to declare a second attack for a character whose turn was done, called `advance_combat_turn`, and then finalised the turn as `deferred`. A turn that advanced the fight carries a committed change, so validation rejected `deferred`; the generic retry hint ("add the missing evidence") did not say what to change, and the player ended on the warning line instead of the narration.

## Change

- **The decision object is removed from the reply before anything else reads it.** A new first reply step, `scrub_leaks`, drops any JSON object that contains a `"disposition"` key from the draft text, together with a code fence it leaves empty, and tidies the blank lines. Only objects with that key are removed, found anywhere in the text (before, between or after the narration); other braces, code and JSON are untouched. It runs before the consistency repair so no later step has to see it, and it is registered in the pipeline's order rules.
- **The retry says what to do.** When the Keeper has passed the turn (an `advance_combat_turn` that changed the game and is not held at a pending check) and then answers `deferred`, the one final retry says: the advance is committed, answer `resolved_without_check` citing it, and narrate that this player has finished the round (not repeating their attack) and who is up next.
- **The refusal says it too.** The message returned when a declaration is refused for an already-completed turn now names the next step (advance with a new event id) and how to report it (`resolved_without_check`, not `deferred`).

## Not changing

- Whether the Keeper may call `deferred` after a committed change stays refused; the Keeper is told the right answer instead.
- No attempt to find which stage emitted the object: the cause is unproven, and removing it at the end of the line protects every path.

## Verification

- `tests/test_leaked_decision_and_redeclare.py`: the object is removed whether before, between or after the narration, nested, inside a code fence, or repeated; braces without `"disposition"` stay; the pipeline step removes it; the "turn passed" test is true only for an `advance_combat_turn` that changed the game.
- `tests/test_reply_pipeline.py`: the shipped order starts with `scrub_leaks` and the order rules hold.
- `tests/test_combat_flow.py`: declaring again after the turn was taken names the advance and `resolved_without_check`.
