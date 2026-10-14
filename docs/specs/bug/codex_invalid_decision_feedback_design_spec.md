# A rejected Codex decision tells the model what was wrong, and the log says which field

[繁體中文](codex_invalid_decision_feedback_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `0b203bc`. Found in the 2026-10-14 Haunting random run1 (turn 29).

## Problem

The first fight of the run: the Executor looked up the enemy, rolled Sanity for all four investigators (already committed), looked up a weapon, then sent a decision the host rejected as `invalid_schema`. The host's one repair note said only "return one decision matching the actual tools and input_schema", so the model sent a near-identical decision (1326 and 1333 bytes), it was rejected again, and the turn ended as an internal error after 56.8 s, 27.8 s of it in the two rejected requests. The player got no answer, the fight started a turn later (another 32 s), and the log could not say which field was wrong: the rejection event carried only `reason=invalid_schema`.

## Change

`codex_provider.run_conversation`, in the rejection branch only:

- the validator's own message and the path of the wrong field (`arguments.enemies.0.source`, with `arguments` marking a tool's own arguments as against the decision envelope) go into the repair note as `validation_error` (message cut at 300 characters), and the instruction tells the model to fix what it names. A non-schema failure (broken JSON, a duplicate key, an empty final) gives its error class and text;
- the `codex.decision.rejected` event gains `error_type`, `path` and `validator` (the field and the rule, never the model's content);
- nothing else changes: still one repair, then `codex_invalid_decision`; no tool is run for a rejected decision.

Whether the rejected decision in turn 29 was an `initialize_combat` whose optional `source` lacked one of its five required fields is a guess from the schema; the raw output was not logged. The new fields will say.

## Verification

`tests/test_codex_provider.py`: a decision missing a required argument gives the retry `validation_error` with the path and the "required property" message; a wrong type names `arguments.target` and rule `type`, broken JSON names its error class; the log event carries path, rule and error type and no message. All three fail without the change.
