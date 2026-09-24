# Spec: Combat-trigger prompt and damage-tool description ambiguity

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
- **implementation end**: TBD

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
2. **`damage_combatant` and `apply_combat_damage` both plausibly mean
   "deal damage," with no rule for which to prefer.** `damage_combatant`
   takes a flat `name`+`delta` (already-final number); `apply_combat_
   damage` takes `target`+`raw_damage` and computes armor reduction
   internally. Neither description says when to use one over the other,
   causing the model to sometimes detour through `get_combat_status`
   (to check armor / decide) before committing to either.

## Changes

- `app/keeper.py`'s combat-trigger prompt text (`_build_static_prompt`,
  the sentence around line 3054 containing "當敘事中出現「打起來了」的
  場面"): split into two sentences — one stating `start_combat` takes no
  parameters and needs no lookup first, a separate one for `add_npc_to_
  combat`'s lookup requirement — using the exact wording verified in round
  7's `REVISED_COMBAT_TRIGGER_RULE` (see the tiering spec's script,
  session scratchpad, for the verbatim text used in the real-API test).
  Keep the PR #55 same-species-naming rule intact, unchanged.
- `app/keeper.py`'s `TOOLS` list: `damage_combatant` and `apply_combat_
  damage` tool `description` fields get one disambiguating sentence each,
  matching round 7's tested wording:
  - `damage_combatant`: state that when the narrative already gives a
    final damage/heal number, use this tool directly without querying
    combat status first.
  - `apply_combat_damage`: state that it's only for when the system needs
    to compute armor reduction itself, and to use `damage_combatant`
    instead when a final number is already given.
- No other prompt text, tool schema, or behavior changes — this is a
  wording-only fix, confirmed sufficient by the round 7 test without
  needing any model/config change.

## Testing Strategy

- Existing test suite must stay green (ruff/mypy/compileall/pytest) —
  this changes prompt strings and tool descriptions only, not any
  behavioral code path, so no existing test should need updating unless
  one happens to assert on the exact old prompt/description text (grep
  for the changed strings across `tests/` before editing).
- No new automated test is meaningful here: correctness was already
  verified empirically via real-API trials (round 7, N=3 per scenario per
  config, 6/6 pass rate) rather than something a mocked unit test could
  exercise (the failure mode is LLM tool-selection behavior against real
  prompt text, not application logic).
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
