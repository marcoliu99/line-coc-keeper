# A stray citation or an early await does not cost a retry, the first opening is played, addressed NPCs answer, and met triggers fire

[繁體中文](executor_retry_opening_npc_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `707ad26`.

## Problem

From the 2026-10-12 soak runs at 707ad26:

- **A whole retry for one stray citation.** Camp Sunny rerun5: a ruling cited `state` and `scenario_context` with no scenario retrieved, and was refused whole as `invalid_evidence_reference`.
- **await_check at a table where nothing waits.** Camp Sunny rerun5, twice: with autoroll on, the Keeper answered `await_check` before any dice. Nothing was pending, so it failed `pending_identity_mismatch`, and the generic retry hint did not say to roll.
- **The opening was skipped.** Haunting turn 1: the player's first line recounted the set-up, and the narrator rule against replaying what already happened made it skip the opening that had never been told.
- **Addressed NPCs stayed silent, and written triggers never fired.** Players spoke to an NPC in the scene and got scenery back; scenario events written as "when the investigators do X, Y happens" did not start once X was done.

## Change

- **Stray citations are dropped.** `validate_resolution` drops evidence refs that are not available when at least one valid ref remains; the dropped refs are logged. A ruling with no valid ref is still refused. The later checks still ask for the evidence each disposition needs.
- **An autoroll table is told to roll now.** When `await_check` fails `pending_identity_mismatch` with autoroll on, nothing pending and no dice yet this turn, the retry says nothing waits for a roll: call `skill_check` / `sanity_check` now and return `resolved`, or `no_mechanics`.
- **Only what was told is not replayed.** The narrator rule now covers only what conversation history or past memory already played; what has not been played (the first opening, a first arrival) is played in full.
- **Two prompt rules:**
  - In the shared pacing section, which the Executor and Narrator both read: an NPC in the scene who is addressed answers in the same reply. They may evade, lie, refuse or ask back, but they are never silent and never answer next turn.
  - In the Executor instruction only, since a trigger may need combat or a roll and only the Executor has the tools: when retrieved scenario text shows the players' situation meets a written trigger, the event starts this turn through the tools it needs, not before. The obligation gate that runs after narration leaves a SAN check, damage or forced check alone when the Executor already applied that kind this turn, so a rule both read is not applied twice.

  There is no scene clock and no phrase matching.

## Verification

- `tests/test_turn_consistency_handoff.py`:
  - a stray citation beside a valid one is dropped, and a lone stray one is still refused;
  - an early `await_check` at an autoroll table gets the roll-now feedback.
- `tests/test_narrative_boundary_prompts.py`:
  - the first-opening carve-out;
  - the NPC rule on both paths, and the trigger rule on the Executor's only.
