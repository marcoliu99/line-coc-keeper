# Spec: Executor reasoning effort for combat turns with ongoing effects

## Changeset Tracking
- **main_v2 start**: origin/main_v2:cb11eb87c02ff850b8ee6c86e0d5a34b2c92e75c
- **implementation end**: TBD

## Purpose & Scope

Split out of `docs/specs/bug-continuing-damage-rolls-corrupt-luck-stat.md`'s
"Open design question" section (that branch landed its stopgap in PR #65
without resolving this — see that spec for the full incident writeup).
This branch owns the actual decision: **should a combat turn where
`state.combat.active` and a continuing effect (burning, poison, bleed,
etc.) is on record use a higher `reasoning_effort` than
`EXECUTOR_REASONING_EFFORT_OPENAI`'s current global default of `none`**,
even at the cost of some of the speed/cost gains `none` was chosen for in
`docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md`?

This is a narrower question than that tiering spec's own scope (which
covers the Executor's reasoning effort in general) — it's specifically
about whether combat-with-ongoing-effects turns deserve a *different*
tier than everything else, not a re-litigation of `none` as the general
default.

## Evidence carried forward from the originating branch

Real OpenAI API trials (real cost each), same full-production-scale
scenario (real 36-tool list, real static/dynamic prompt, an NPC on fire
from a Molotov, "already 2 rounds into hand-rolling this effect" history):

| Effort | Trials | Correctly called `add_combat_effect` | Actively wrong (corrupts state / ends combat / misattributes) |
|---|---|---|---|
| `none` | 4 | 0/4 | **4/4** — 4 different wrong outcomes each trial, including one that called `end_combat()` outright on a still-alive, still-burning NPC |
| `low` | 7 | 2/7 | **0/7** — the 5 "misses" made no tool call at all, instead narrating that the fire damage isn't resolved yet and handing the decision back to the player |
| `medium` | 1 | 1/1 | 0/1 |

**Reading carried forward**: `low` is not simply "worse than `medium`,
better than `none`" — it's a genuinely different failure profile. `none`
is unreliable *and* unsafe (every failure actively corrupts state or ends
combat wrongly). `low` is unreliable but safe (a miss just punts back to
the player, never writes wrong data) — though a punt still leaves the
mechanical effect unresolved, so the same situation resurfaces next turn.

**This data is explicitly under-sampled** for a real decision — the
originating spec's own assessment was N=7/4/1, well short of the tiering
spec's established bar of N≥3-5 per condition, "given this project's
already-documented high run-to-run variance at low effort tiers." Nothing
here should be treated as conclusive without more trials.

## Open questions (need answering before implementation)

1. **How to detect "a combat turn with an ongoing effect"** — is this
   `state.combat.active and state.combat.effects` (or equivalent — needs
   checking the real `GroupState`/`CombatState` shape) at the point the
   Executor is invoked? Does a turn where the *player's own action* would
   introduce a new ongoing effect (e.g. throwing a Molotov this turn)
   count, or only turns where an effect is already on record from a prior
   turn? The real incident was the latter (an already-established fire);
   unclear if the former needs the same treatment.
2. **Which tier to target**: `medium` (1/1 in the limited sample, but
   only 1 trial) vs. `low` (worse hit rate but a qualitatively safer
   failure mode) vs. some other tier not yet sampled. Needs real-API
   trials at N≥3-5 per condition, per this project's own established bar,
   before deciding — the table above is a starting hypothesis, not
   sufficient evidence on its own.
3. **Where the override lives** — does this need a new config surface
   (e.g. `EXECUTOR_REASONING_EFFORT_OPENAI_COMBAT_ONGOING_EFFECTS`) parallel
   to the existing per-provider tiering config in `app/config.py`, or does
   the Executor agent itself compute this conditionally from
   `EXECUTOR_REASONING_EFFORT_OPENAI` plus a fixed bump rule? Needs
   checking how `app/agents/executor.py`'s `_EXECUTOR_MODEL_OVERRIDES`
   mechanism from the tiering work is structured before deciding whether
   it already supports a per-turn-condition override or needs extending.
4. **Cost/latency tradeoff acceptance** — how much of the speed/cost gain
   `none` was chosen for (see the tiering spec's own real-API rounds) is
   the user willing to give up for combat-with-ongoing-effects turns
   specifically? This is a judgment call for the user, not something
   real-API trials alone can answer.

## Testing Strategy (draft — needs real-API verification before any
production text/config change)

- Real-API trials at whichever candidate tier(s) emerge from question 2
  above, same real-incident reconstruction already used in the
  originating branch (or a fresh equivalent scenario, to avoid
  overfitting to one exact prompt), N≥3-5 per condition per this
  project's established bar.
- If a config surface is added: a straightforward unit test that the
  Executor actually requests the bumped effort when the detection
  condition (question 1) is true, and the normal default otherwise —
  pure application logic, no real API call needed for this part.
- Standard four checks (ruff, mypy, compileall, pytest) before considering
  this done.

## Notes
- Not yet implementation-ready — open questions above need the user's
  input first, same as this project's other backlog specs
  (`enhancement-macro-combat-initialization-tool.md`,
  `enhancement-executor-dynamic-tool-scoping.md`).
- See `docs/specs/bug-continuing-damage-rolls-corrupt-luck-stat.md` for
  the full original incident and root-cause investigation this split off
  from, and `docs/specs/enhancement-executor-model-tiering-and-tool-
  scoping.md` for the broader Executor reasoning-effort/model-tiering
  context this narrower question sits inside.
