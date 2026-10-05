# Splitting `supervisor.run_turn` and ordering the reply steps

[繁體中文](reply_pipeline_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `e4c6044`.

## Problem

`supervisor.run_turn` was one 277-line function. Inside it, the steps that turn the Narrator's text into the reply a player reads ran in an order fixed only by line position: consistency repair, the Guard, consistency repair again, the obligation gate, the party-size correction, delivery validation, display mapping. Each step has a reason to be where it is (#181 and #185 each inserted one), and the reasons lived in comments. The next person to add a step could put it after the one that validates delivery and change text that had already been validated. See `docs/architecture/main_v2_architecture_review.md` (F4).

## Constraint: the player's turn does not change

This is a move. Every model call, lock hand-off, fallback record and commit happens in the same order with the same inputs; no step awaits that did not await before; no reply text, log line or timing changes. The reply still needs the whole text, so it still cannot stream: that is the safety boundary, not something this change touches.

## Layout

`run_turn` keeps its signature and decorators and now reads as its stages:

| Stage | Does |
| --- | --- |
| `_prepare` | blocked-by-correction reply, held-Luck answer from state, context build, intent routing; the KP Assistant route ends here |
| `_mechanics` | Executor, its one recovery, the reducer, the narration hand-off of the mutation lock |
| `_narrate` | the Narrator (once per auto-rolled consequence), or the pending-state reply; a failed opening ends here |
| reply steps | `app/agents/reply_pipeline.py`, below |
| `_commit` | one transaction appending the turn to the log; a stale timeline delivers nothing |

Delivery to Discord and the background maintenance stay with the caller (`router`), so there is no stage for them here. The stages share a private `_Turn` record instead of a dozen locals. What the obligation gate compares against is still taken before the Narrator runs, because a tool-enabled Narrator can move that state.

## The reply steps

`reply_pipeline.STEPS` is an ordered tuple: `consistency`, `guard`, `consistency_after_guard`, `obligations`, `party_size`, `finalize`, `player_text`. `ORDER_RULES` says, for each adjacent constraint, what must precede what and why, and `validate` is run at import: a list that breaks a rule, lacks a ruled step, or has a step after `finalize` that is not marked `display_only` raises `ValueError`. `player_text` is the one step allowed after `finalize`, marked `display_only`: it maps tier names and removes internal ids for display. **It is not lossless**: a validated line that carried a raw tier name or a labelled id would still be rewritten after validation. That is exactly what ran before the split (`player_text` always followed `finalize`), no projected line does so today (`finalize` labels outcomes itself and `player_text` is idempotent), and validating the mapped text instead would be a behaviour change, not part of this move.

Only the Guard and the obligation gate wait on anything; a test asserts that, so a step that starts to await (a new round trip in the player's turn) is a visible change.

## Verification

`tests/test_turn_stage_sequence.py` was recorded against the pre-split `run_turn` and passes unchanged after it: the order of the Executor, hand-off, Narrator, Guard, obligation gate, party-size, delivery validation and commit calls for an ordinary turn, a turn with an obligation candidate, a resolved-check follow-up, a stale timeline, a failed opening and the KP Assistant route. `tests/test_reply_pipeline.py` covers the order rules. Tests that patched `supervisor.guard` or `supervisor.turn_delivery` now patch `reply_pipeline.guard`/`reply_pipeline.turn_delivery` (the same module objects). `ruff check .`, `mypy app` and the full `pytest` pass.
