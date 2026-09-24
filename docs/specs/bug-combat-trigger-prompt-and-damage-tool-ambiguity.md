# Spec: Combat-trigger prompt and damage-tool description ambiguity

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
- **implementation end**: bug/combat-trigger-prompt-and-damage-tool-ambiguity:a05fd3c — ruff/mypy/compileall/pytest all green (476 passed)

## Purpose & Scope

Found while running real-API verification for `docs/specs/enhancement-
executor-model-tiering-and-tool-scoping.md` (rounds 4-7). Two known-hard
scenarios (`start_combat_single_npc`, `damage_npc`) showed high run-to-run
variance across multiple model/effort configs — including the *current
production model* (`gpt-5.6-luna`/medium) failing on some runs — which
pointed at the prompt/tool-schema wording itself rather than any specific
model's capability. Confirmed empirically: round 7 patched the wording
(test-only, not yet applied to `app/keeper.py`) and reran N=3 trials
against both the production model and a candidate; both went from mixed/
coin-flip results in round 6 to a clean 6/6 pass rate on both scenarios.
Full data: `docs/specs/enhancement-executor-model-tiering-and-tool-
scoping.md`, "Round 6" / tool-schema inspection / "Round 7" sections.

This is a real, independent production-prompt bug — separate from that
still-discussion-only tiering spec, and worth landing on its own since it
measurably improves correctness/speed for whichever model ends up running
the Executor (or the legacy Keeper single-call path, which uses the same
static prompt text).

## Root causes (both confirmed by the round 7 A/B test)

1. **`start_combat` (zero parameters) is bundled into the same sentence as
   `add_npc_to_combat`'s "look up armor/attacks/abilities first" caveat**
   in the combat-trigger rule (`app/keeper.py:3054`). The lookup
   requirement is genuinely only relevant to `add_npc_to_combat` (which
   has armor/attacks/abilities parameters worth researching); `start_combat`
   has nothing to look up (`"input_schema": {"type": "object",
   "properties": {}}`). The model plausibly over-generalizes "look it up
   first" onto `start_combat` too, producing spurious/looping
   `search_scenario` calls before (or instead of) `start_combat`.
2. **The tools have different damage semantics, but the descriptions did
   not distinguish them safely.** `damage_combatant` sends negative deltas
   through the armor-aware `apply_combat_damage` implementation, so it
   does not accept an already-final damage value. `apply_combat_damage`
   accepts raw damage and calculates armor. The original wording suggested
   using `damage_combatant` for final damage, which applied armor twice and
   reduced HP loss incorrectly.

## Changes

- `app/keeper.py`'s combat-trigger prompt text (`_build_static_prompt`,
  the sentence around line 3054 containing "當敘事中出現「打起來了」的
  場面"): split into two sentences — one stating `start_combat` takes no
  parameters and needs no lookup first, a separate one for `add_npc_to_
  combat`'s lookup requirement — using the exact wording verified in round
  7's `REVISED_COMBAT_TRIGGER_RULE` (see the tiering spec's script,
  session scratchpad, for the verbatim text used in the real-API test).
  Keep the PR #55 same-species-naming rule intact, unchanged.
- `app/combat.py`: add `apply_final_combat_damage`, which follows the same
  authoritative HP synchronization, damage-trigger, and major-wound flow
  as `apply_combat_damage` while skipping armor reduction because the
  supplied number is already final.
- `app/keeper.py`: distinguish raw and final damage in tool descriptions;
  expose `apply_final_combat_damage` as a formal tool and allow it for KP
  Assistant turns alongside the existing formal damage tool. Update
  combat prompts so raw damage uses `apply_combat_damage`, final damage
  uses `apply_final_combat_damage`, and `damage_combatant` is not presented
  as a final-damage path.
- Keep `apply_combat_damage`'s existing raw-damage behavior unchanged.

## Testing Strategy

- Existing test suite must stay green (ruff/mypy/compileall/pytest) —
  this changes prompt strings and tool descriptions only, not any
  behavioral code path, so no existing test should need updating unless
  one happens to assert on the exact old prompt/description text (grep
  for the changed strings across `tests/` before editing).
- Automated tests confirm armor is applied once on the raw-damage path and
  not applied again on the final-damage path, including the KP Assistant
  tool allowlist and canonical-result behavior. The prompt wording
  selection remains empirically verified via the real-API trials (round 7,
  N=3 per scenario per config, 6/6 pass rate).
- Optional follow-up (not blocking this branch): once this lands, round 3
  of the tiering spec's 8-scenario sweep could be re-run against the now-
  fixed static prompt to confirm no other scenario regresses from the
  wording change — not required before merging this fix itself.

## Notes
- Independent of `docs/specs/enhancement-executor-model-tiering-and-tool-
  scoping.md`'s model/tier decision — that spec remains discussion-only;
  this fix benefits whichever model runs the Executor, and also the
  legacy `keeper.run_turn` single-call path, since both read the same
  static prompt text and tool list from `app/keeper.py`.
- The two-turn-simulation test methodology (feed back a synthetic tool
  result via `previous_response_id`/`function_call_output`, check the
  next call) and the round 4-7 raw data live in the tiering spec, not
  duplicated here.
