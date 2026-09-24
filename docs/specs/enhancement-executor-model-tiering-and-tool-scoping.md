# Spec: Executor model tiering + dynamic tool scoping

**Status: discussion only, nothing implemented yet.** Split out of
`docs/specs/enhancement-conversation-lock-and-tool-loop-latency.md` (items
9/10/12/13 there) once that doc's own first-batch decisions (typing
indicator, queue-ack, `MAX_TOOL_ITERATIONS`) were ready to implement on
their own — this follow-up needs more real-API trials before it's
implementation-ready, so it's kept separate rather than blocking or
bloating that doc further.

## Changeset Tracking
- **main_v2 start**: origin/main_v2:10972c1a8966f48cd7bbcb0f7c850b1ac8558310
- **implementation end**: N/A — discussion branch

## Background

While evaluating a latency-optimization proposal for the Keeper's LLM
turn loop (see the parent doc), two related ideas came up that need their
own real-API verification before they're safe to implement:

1. **Model/reasoning-effort tiering for the Executor role** — `app/agents/
   executor.py`'s tool-calling loop never needs to produce narration text
   (that's `app/agents/narrator.py`'s job, a separate LLM call); the
   Executor's own text output is discarded, only its tool calls' side
   effects matter. In principle it could run on a cheaper/faster model or
   with reasoning turned down without touching narration quality. But this
   only applies to the *Supervisor/Executor path* (`router.py:584`) — the
   older `keeper.run_turn` single-call path (still handling every skill-
   check-result narration, confirmed via the live log's `agent="keeper"`
   tag) has no separate Narrator, so this can't apply there.
2. **Dynamic tool scoping** — the real production tool list is 35 tools
   (`keeper._tools_for_speaker_role("player")`: 34 base + `search_scenario`
   when RAG is on), most of which are irrelevant to any given turn (combat
   tools when not in combat, etc.). Filtering this down before sending it
   to the model should cut prompt size and, per the trials below, may
   *also* improve correctness by removing distracting near-miss options.

## Real-API verification done so far (from the parent doc, consolidated here)

All of this used real calls against the real OpenAI API (small real cost
each time), not guesses from documentation.

### Round 1 — model/effort comparison, single scenario (dual skill check)
| Config | Run 1 | Run 2 | Correctness |
|---|---|---|---|
| `gpt-5.6-luna` / `medium` (current prod) | 3362ms / 4191ms | | ✅ both |
| `gpt-6-luna` / `none` | 2510ms / 1999ms | | ✅ both |
| `gpt-6-luna` / `low` | 1549ms / 2327ms | | ⚠️ 1 of 2 — once called `roll_dice` directly instead of `skill_check`, skipping the actual mechanic |
| `gpt-4o-mini` / `none` | — | | ❌ HTTP 400: `reasoning.effort` not supported by this model at all |
| `gpt-4o-mini` / no reasoning param | 5084ms | | ✅ but slowest of everything |

### Round 2 — gpt-4o-mini repeat trials + tool-count sensitivity
| Config | Runs | Avg | Correctness |
|---|---|---|---|
| `gpt-4o-mini` / 34 tools | 3847 / 6450 / 4126ms | 4808ms | ✅ 3/3 |
| `gpt-4o-mini` / 4 tools (scoped) | 3144 / 2429 / 2741ms | 2771ms | ✅ 3/3 |

Confirms the round-1 result wasn't a fluke, and that tool scoping helps
this model ~42% — but even scoped, still not clearly faster than
`gpt-6-luna`/`none` at the *full* 35-tool count.

### Round 3 — 8-scenario sweep (real production tool list, 35 tools)
Scenarios: single/dual skill check, SAN check, start combat (1 NPC), start
combat (3 NPCs — the exact shape behind the original "語塞" bug), deal
damage, spend Luck, scenario lookup.

| Scenario | Baseline (35 tools) | Candidate, full (35 tools) | Candidate, scoped (6 tools) |
|---|---|---|---|
| single_skill_check | 3009ms ✅ | 2680ms ✅ | 1727ms ✅ |
| dual_skill_check | 3444ms ✅ | 2909ms ✅ | 2609ms ✅ |
| san_check | 2499ms ❌ (`search_scenario`) | 2046ms ✅ | 1651ms ✅ |
| start_combat_single_npc | 2697ms ❌ (`search_scenario`) | 2515ms ❌ (`offer_npc_attack_defense_choice`) | 2148ms ❌ (`skill_check`) |
| start_combat_multi_npc | 2373ms ✅ | 1774ms ✅ | 1651ms ✅ |
| damage_npc | 2510ms ❌ (`get_combat_status`) | 2822ms ❌ (`adjust_ammo`) | 2301ms ✅ |
| luck_spend | 2420ms ✅ | 1956ms ✅ | 1459ms ✅ |
| scenario_lookup | 1536ms ✅ | 2234ms ✅ | 1855ms ❌ (tool not in this run's 6-tool scope — a test-design gap, not a model failure) |

**Honest reading:** speed ordering is consistent (scoped < candidate-full
< baseline) across every scenario. Correctness isn't a clean "smaller
reasoning = worse" story — baseline (today's actual production config)
got 3/8 wrong, candidate-full got 2/8 wrong (one, `adjust_ammo` for a
stated fixed-damage hit, is clearly nonsensical), candidate-scoped got 1
genuine miss (the other "miss" was this test's own 6-tool set not
including `search_scenario`). `start_combat_single_npc` failed for *all
three* configs — most likely because this test's `STATIC_INSTRUCTIONS`
is much shorter than the real production static prompt (missing the
explicit "看到「打起來了」...呼叫 start_combat" combat-triggering rule),
not evidence against any specific model/effort tier.

### Bug found while building round 3, separate from this spec's scope
`app/agents/tool_gateway.py`'s `TOOLS` constant is a bare `keeper.TOOLS`
reference (34 tools), **not** `keeper._tools_for_speaker_role("player")`
(35, RAG-aware). This means `app/agents/executor.py` — the newer
Supervisor/Executor path — never gets `search_scenario` in its tool list
at all when `SCENARIO_RAG_ENABLED` is on, even though the static prompt it
sends explicitly instructs the model to use that tool for any scenario
detail. Confirmed by reading the import chain (`executor.py:6` → `tool_
gateway.py:40` → `keeper.TOOLS`), not just log inference.

**Fixed**: PR #57 (`bug/executor-tool-list-missing-search-scenario`,
its own spec at `docs/specs/bug-executor-tool-list-missing-search-
scenario.md`). Turned out worse than "missing one tool" —
`context_builder.py` skips its automatic proactive RAG search entirely
while `state.combat.active`, so mid-combat Executor turns had *zero*
scenario grounding at all before this fix, not just a missing follow-up
tool. Round 3's `scenario_lookup` scenario result above (recorded as "this
test's own 6-tool scoping choice, not a model failure") was actually
hitting this same underlying bug, not just a scoped-set omission — worth
re-running round 3 once PR #57 merges to confirm the conclusions still
hold against the now-correct 35-tool baseline (expect no material change,
since the direction — `gpt-6-luna`/`none` ahead on both axes — was already
consistent before this fix).

## Decisions

1. **Scoped tool-set size**: settled on the designed Tier A/B/C split
   as-is (see below) — not forcing a fixed "~10" count. Non-combat scope
   = A+B (21 tools), combat scope = A+C (26 tools).
2. **`EXECUTOR_MODEL` per-provider defaults**: yes, build the per-provider
   config surface, but focus the actual default value/tuning on OpenAI
   first (`gpt-6-luna`/`none` per the trials so far) — Anthropic/Gemini
   get the plumbing (so nothing breaks when `LLM_PROVIDER` isn't openai)
   but not necessarily their own tuned candidate yet; that's follow-up
   work once there's real data for those providers too.
3. **More trials needed**: yes, current data (8 scenarios × 1 run each
   against an ad hoc 6-tool test set) isn't enough. Next round tests
   against the *real* tier groupings instead of an ad hoc set — see
   "Round 4" below. Also fixing the `STATIC_INSTRUCTIONS`-too-short test
   artifact first (`start_combat_single_npc` failed for all three configs
   in round 3, most likely because the test prompt was missing the real
   combat-triggering rule, not because of any model/tier difference).

### Round 4 — real Tier A/B/C groupings, fixed STATIC_INSTRUCTIONS, gpt-6-luna/none

Used the exact Tier A (12) / B (9) / C (14) lists from "Dynamic tool
scoping design" below, and the real production combat-trigger prompt text
(verbatim from `app/keeper.py:3054`, including PR #55's same-species-
naming rule) instead of round 3's shortened `STATIC_INSTRUCTIONS`. Also
rewrote `start_combat_single_npc`'s message to be an unambiguous lethal
threat (a deep one lunging for the throat) instead of round 3's "a rat
nips at your ankle" one-liner.

| Scenario | Real state | A only (12) | A+B (21) | A+C (26) | Correct-scope result |
|---|---|---|---|---|---|
| single_skill_check | non-combat | 4172ms ✅ | 2001ms ✅ | 1885ms ✅ | A+B: ✅ |
| dual_skill_check | non-combat | 2833ms ✅ | 2171ms ❌ (only 1 `skill_check`, not 2) | 2838ms ✅ | A+B: ❌ |
| san_check | non-combat | 2630ms ✅ | 2792ms ✅ | 1742ms ✅ | A+B: ✅ |
| start_combat_single_npc | non-combat | 2038ms ❌ (`search_scenario`) | 1901ms ❌ (`search_scenario`) | 1869ms ❌ (`search_scenario`) | A+B: ❌* |
| start_combat_multi_npc | non-combat | 1965ms ❌ (`search_scenario`) | 1648ms ✅ | 1764ms ❌ (`search_scenario`) | A+B: ✅ |
| damage_npc | combat | 1695ms ❌ (`search_scenario`) | 1990ms ❌ (`search_scenario`) | 1250ms ❌ (`get_combat_status`) | A+C: ❌* |
| luck_spend | non-combat | 1928ms ✅ | 1825ms ❌ (`search_scenario`) | 1755ms ❌ (`get_character_sheet`) | A+B: ❌* |
| scenario_lookup | non-combat | 2056ms ✅ | 2032ms ✅ | 1779ms ✅ | A+B: ✅ |

**Important methodology finding, not a model regression (marked `*`
above):** with the real, fuller combat-trigger prompt — which explicitly
tells the model it must look up the scenario for enemy armor/attacks/
abilities before calling `add_npc_to_combat`, and generally "don't
improvise, look it up" — the model now frequently calls `search_scenario`
*first*, as a legitimate first step toward the "correct" tool, not
instead of it. This test only fires **one** model turn per scenario and
checks the single tool call chosen, so it cannot distinguish "wrong tool"
from "right first step of a multi-call turn that would call
`start_combat`/`damage_combatant` next, once it sees the search result."
Round 1-3 didn't surface this because their shorter `STATIC_INSTRUCTIONS`
didn't push the model toward search-first behavior as strongly.

This is the same underlying architecture gap as the per-iteration
rescoping requirement below, from the test-harness side: **single-shot
tool-call tests can't validate turns that legitimately span multiple tool
calls.** `get_combat_status` for `damage_npc` and `get_character_sheet`
for `luck_spend` are similarly plausible legitimate first steps (checking
current HP/ammo before applying damage; checking current Luck before
spending it), not obviously wrong — round 3's shorter-prompt baseline
happened not to trigger this pattern, not because it was more "correct."

**What this means for round 5 (not yet run):** the test harness needs a
real 2-turn simulation for scenarios where a lookup-then-act sequence is
plausible — feed back a synthetic tool result for the first call (e.g. a
fake `search_scenario` result describing the deep one's stats) and check
what the model calls *next*, the same way `app/agents/executor.py`'s real
tool-calling loop would. A single-shot test genuinely cannot separate
"the model picked the wrong tool" from "the model is executing tool call
1 of 2 correctly."

### Round 5 — 2-turn simulation, retesting the 3 flagged scenarios

Used the real OpenAI Responses API multi-turn contract
(`previous_response_id` + `function_call_output`) against each scenario's
correct-scope tool set, feeding back a synthetic but realistic tool result
for whichever lookup tool turn 1 called, then checking turn 2's call — the
same shape `app/agents/executor.py`'s real loop uses.

**Result: the round 4 "legitimate first step" hypothesis does NOT hold.**
This is a real, useful correction, not confirmation:

- **`start_combat_single_npc`** (A+B, gpt-6-luna/none): turn 1 calls
  `search_scenario`; fed back a realistic deep-one stat block (HP/armor/
  attacks); turn 2 calls `search_scenario` **again**, not `start_combat`.
  Genuinely stuck, not "step 1 of 2."
- **`damage_npc`** (A+C): turn 1 calls `search_scenario`; fed back a
  realistic rat stat block; turn 2 calls `get_combat_status` — a second,
  different lookup, still not `damage_combatant`/`apply_combat_damage`.
  Suggests the model may be in a genuine multi-lookup spiral for this
  scenario, not a 2-call sequence — matches this project's own real
  incident history (`MAX_TOOL_ITERATIONS`/`HIGH_ITERATION_WATERMARK` in
  the parent latency spec exist because of exactly this failure mode in
  production).
- **`luck_spend`** (A+B): this run's turn 1 called `skill_check` —
  neither a lookup tool nor the expected `adjust_character` — a third,
  different wrong answer from round 4's `search_scenario`. Confirms
  real run-to-run non-determinism on this scenario even at `reasoning:
  none`, independent of the lookup-first question.

**Revised honest reading:** round 4's charitable "maybe it's just doing
lookup-then-act" explanation was worth testing (and the test harness gap
it identified — single-shot tests can't validate multi-call turns — is
still a real, correct point, see the per-iteration rescoping section
below) but for `gpt-6-luna`/`none` specifically, these 3 scenarios are
genuine, reproducible-ish correctness gaps, not test artifacts. Combined
with round 1's `start_combat_single_npc` failure under `gpt-5.6-luna`/
`medium` (today's actual production config) too, this scenario may simply
be hard for this tool schema regardless of model/effort tier — worth
checking whether the *tool descriptions themselves* (`start_combat`'s
description, or the static prompt's ordering) are the real lever, not
just which model runs them, before concluding anything about `gpt-6-luna`/
`none` specifically vs. the current production model.

## Design requirement found while planning round 4: per-iteration rescoping, not per-turn

Re-examining the Tier B/C split against a real multi-tool-call turn (the
exact original bug shape: `search_scenario` → `start_combat` → `add_npc_
to_combat` × 2, all in *one* turn) surfaces a real implementation
constraint this doc hadn't addressed yet: `add_npc_to_combat` is Tier C
(only offered while `state.combat.active`), but `start_combat` is what
*sets* `state.combat.active` — if the tool list offered to the model is
computed **once at turn start** (before any tool calls that turn), a turn
that calls `start_combat` and *then* wants to add NPCs in the *same* turn
would never have `add_npc_to_combat` available at all, since combat wasn't
active yet when the list was built.

This means "dynamic" scoping has to mean **recomputed before each
iteration of the tool-calling loop** (re-checking `state.combat.active`
after every tool execution, same as the state the Keeper already reloads
fresh each round via `_mutate_and_save_state`), not computed once per
turn. This is a real, non-trivial implementation detail for whenever this
comes off the backlog — noted here so round 4's test harness (which needs
to simulate a real multi-round conversation for the combat-start scenario,
not a single-shot call) reflects it, and so it isn't missed later.

## Dynamic tool scoping design (combat-active vs. not)

User confirmed the direction: dynamic (combat-state-dependent), not one
static list. Classified all 35 real tools into three tiers by reading
every tool's actual description (`app.keeper.TOOLS` +
`_SEARCH_SCENARIO_TOOL`), not guessing from the name alone:

**Tier A — always available (12), regardless of combat state:**
`skill_check`, `sanity_check`, `roll_dice`, `search_scenario`,
`search_memory`, `get_character_sheet`, `adjust_character`,
`record_established_fact`, `record_clue`, `add_status_tag`,
`remove_status_tag`, `clear_pending_check`

(Status tags and pending-check clearing look "combat-ish" by name but
their own descriptions cover both cases equally — e.g. `add_status_tag`'s
example set is "昏迷/倒地/中毒/著火", at least half of which happen outside
formal combat too. Excluding these from non-combat scope would be a real
functional regression, not a safe trim.)

**Tier B — non-combat additions (9), offered only while `not state.combat.active`:**
`start_combat` (the trigger into combat), `add_carried_item`,
`remove_carried_item`, `search_scenario_images`, `show_scenario_image`,
`advance_scenario_chapter`, `send_private_info`, `set_skill`,
`offer_check_choice`

**Tier C — combat additions (14), offered only while `state.combat.active`:**
`add_npc_to_combat`, `get_combat_status`, `advance_combat_turn`,
`damage_combatant`, `apply_combat_damage`, `plan_enemy_turn`,
`resolve_enemy_action`, `add_combat_effect`, `end_combat`,
`offer_npc_attack_defense_choice`, `npc_skill_check`,
`roll_impaling_damage`, `roll_weapon_damage`, `adjust_ammo`

**This is where the "~10" target and full functional coverage conflict,
worth flagging directly rather than force-fitting a number that doesn't
hold up:**

- Non-combat total = Tier A + Tier B = **21 tools**, not 10. Getting to
  10 would mean cutting things players routinely do outside combat —
  picking up items, viewing scenario images/maps, advancing chapters,
  recording clues, searching old conversation history — which isn't a
  safe trim, it's a feature regression dressed up as an optimization.
- Combat total = Tier A + Tier C = **26 tools**, not far off today's
  full 35 — combat genuinely needs most of the tool surface (initiative,
  damage, enemy AI planning, status effects, defense choices all being
  separate tools is *why* combat turns are the ones burning through
  `MAX_TOOL_ITERATIONS` in the first place).

**Two ways to actually land near "~10", need the user's call:**
1. **"~10" means Tier C specifically** (the combat-only *delta* added on
   top of Tier A when entering combat) — Tier C is 14, close enough to
   trim toward 10 by, e.g., deferring `plan_enemy_turn`/
   `resolve_enemy_action` to a still-broader "enemy turn" sub-scope only
   active on the enemy's initiative slot (finer-grained than just
   combat/not-combat), or accepting 14 as "close to 10" without forcing
   an exact count.
2. **"~10" was this doc's own earlier test design** (the 6-tool scoped
   set from round 3, expanded toward what the 8 test scenarios needed) —
   in which case the real target was never "10 total in production", just
   "roughly what round 3 tested", and the honest production number is
   Tier A (12) for non-combat, no further trimming needed beyond removing
   Tier C.

Recommend (1) if the goal is squeezing Executor's prompt as small as
possible even during combat (more engineering, finer-grained scoping);
recommend (2) if the goal is mainly fixing the *non-combat* overhead
(simpler — two static lists, no sub-scoping within combat) since that's
already a 35→21 reduction (40%) for the common case without touching
combat turns' tool availability at all. Needs the user's pick before this
is implementation-ready.

## Notes
- No code changes in this branch yet — spec/discussion only, per explicit
  instruction ("先不實作，spec 先寫完就好").
- Verification scripts used for all three rounds above are throwaway,
  kept in the session scratchpad, not committed to this repo.
