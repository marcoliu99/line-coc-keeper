# Spec: Executor model tiering + dynamic tool scoping

**Status: model tiering implemented; dynamic tool scoping still
deferred.** Split out of `docs/specs/enhancement-conversation-lock-and-
tool-loop-latency.md` (items 9/10/12/13 there) once that doc's own
first-batch decisions (typing indicator, queue-ack, `MAX_TOOL_ITERATIONS`)
were ready to implement on their own — this follow-up needed more
real-API trials before it was implementation-ready. Rounds 1-8 settled the
model pick (see "Model decision" below); per explicit instruction, the
first implementation pass covers model tiering only — dynamic Tier A/B/C
tool scoping stays a documented, not-yet-implemented design (see that
section) for a future pass.

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
  (rebased onto this after PR #55 merged, mid-branch — see git history)
- **implementation end**: TBD

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

### Round 6 — same 2-turn simulation, 4 model/effort configs on the 3 known-hard scenarios

| Config | start_combat_single_npc | damage_npc | luck_spend |
|---|---|---|---|
| `gpt-5.6-luna`/medium (today's prod) | 8668ms ✅ (turn 2: search_scenario → start_combat) | 4275ms ✅ (turn 2: get_combat_status → apply_combat_damage) | 2384ms ✅ (turn 1) |
| `gpt-6-luna`/none | 1532ms ✅ (turn 1) | 2693ms ✅ (turn 1) | 2897ms ✅ (turn 2: get_character_sheet → adjust_character) |
| `gpt-6-luna`/low | 1145ms ✅ (turn 1) | 3456ms ✅ (turn 2: get_combat_status → apply_combat_damage) | 2672ms ✅ (turn 1) |
| `gpt-6-luna`/medium | 9682ms ❌ (turn 2: search_scenario → **no tool call at all**) | 4317ms ✅ (turn 2) | 1894ms ✅ (turn 1) |

**This is the most important finding of the whole tiering investigation
so far, and it isn't about which model to pick:** every config in this
round passed `start_combat_single_npc` and `damage_npc` except one
(`gpt-6-luna`/medium, which stalled with zero tool calls after its second
turn). Round 5, one run earlier, had `gpt-6-luna`/none fail *both* of
those same two scenarios outright. Same model, same effort, same prompt,
same synthetic tool-result payload — different outcome. **Single-run
(or even 2-run) pass/fail verdicts on these scenarios are not reliable
signal**; the variance run-to-run is large enough to flip a scenario from
FAIL to PASS with no code or config change at all.

This means every earlier round's "config X failed scenario Y" claims
(round 1's `gpt-6-luna`/low mismatch, round 3's baseline `start_combat_
single_npc` failure, round 4's and round 5's `gpt-6-luna`/none failures)
should be read as "failed *that specific run*," not "this config is
unreliable for this scenario" — the sample size per cell has been 1,
occasionally 2. **Before any model/tier decision is implementation-ready,
these specific hard scenarios need N≥3-5 repeated trials per config** to
get an actual pass rate instead of a single coin flip. Cheaper first step
before spending more on that: this also somewhat undercuts the earlier
"maybe the tool schema/description itself is the problem" theory from
round 5's closing note — a schema problem would more likely fail
consistently, not flip between runs; high run-to-run variance points more
toward this being inherent model sampling noise on an ambiguous-enough
scenario, which repeated trials would confirm or refute directly.

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

### Tool-schema/prompt inspection — two concrete, real candidate causes found

Read the actual tool definitions in `app/keeper.py` (`start_combat`:524,
`add_npc_to_combat`:532, `get_combat_status`:573, `damage_combatant`:583,
`apply_combat_damage`:634) rather than continuing to guess from behavior
alone. Found two real, concrete issues — not proof the model is innocent,
but plausible, fixable root causes that fit the observed *variance*
better than a pure model/tier explanation would (a genuinely broken tool
schema would tend to fail consistently; an *ambiguous* one would produce
exactly the coin-flip pattern round 6 saw):

1. **`start_combat` takes zero parameters** (`"input_schema": {"type":
   "object", "properties": {}}`) — there is nothing about it a scenario
   lookup could ever inform. Yet the static prompt's combat-trigger rule
   (`app/keeper.py:3054`) puts the "呼叫 start_combat" instruction and the
   "加入敵人時...必須放進 add_npc_to_combat 的 armor/attacks/abilities；
   不要只填 HP 後靠臨場記憶" lookup requirement in the *same* sentence,
   with the lookup caveat immediately following the two tool names
   together. The lookup requirement is genuinely about `add_npc_to_combat`
   only (which does take armor/attacks/abilities parameters worth
   looking up first), but the sentence structure plausibly lets the model
   over-generalize "look it up first" onto `start_combat` too, even
   though `start_combat` has no parameters that benefit from it — this
   would explain the repeated `search_scenario` calls immediately
   preceding (and sometimes instead of) `start_combat` across rounds 4-6.
2. **Two tools both plausibly mean "deal damage," with no explicit rule
   for when to use which**: `damage_combatant` ("調整戰鬥中某位角色或敵人
   的 HP（受傷用負數，治療用正數）") takes a flat `name`+`delta`, while
   `apply_combat_damage` ("套用正式戰鬥傷害，會分開計算 raw damage、護甲
   抵銷、final damage 與 HP") takes `target`+`raw_damage` and computes
   armor reduction internally. Neither tool's description says when to
   prefer it over the other, and nothing in `STATIC_INSTRUCTIONS` (or, as
   far as this investigation found, the real production static prompt)
   disambiguates them either. A model facing "命中造成 5 點傷害" has a
   real, legitimate reason to hesitate between "this is already the final
   number, use the simple delta tool" and "this is combat damage, the
   correctly-named tool for that is apply_combat_damage, which needs to
   know about armor first" — which would explain the `get_combat_status`
   detours seen for `damage_npc` in rounds 4-6.

**Not yet confirmed empirically** — these are inspection-based hypotheses,
consistent with the variance pattern but not yet tested against a fix.
Cheapest next step once resumed: reword just the combat-trigger sentence
to separate the `start_combat` instruction from the lookup caveat, and/or
add one clarifying line on `damage_combatant` vs. `apply_combat_damage`'s
description, then rerun round 6's exact 3-scenario x N-trial matrix
against the *same* model/effort configs to see if pass rates improve —
isolates "was it the wording" from "was it the model" far more cheaply
than testing more models against the unfixed wording would.

### Round 7 — wording fix, N=3 trials, confirms the hypothesis

Applied both fixes from the tool-schema inspection above, as a **test-only
patch** (not yet touched `app/keeper.py`): split the combat-trigger
sentence so `start_combat`'s "no parameters, don't look anything up first"
is separate from `add_npc_to_combat`'s lookup requirement, and patched
`damage_combatant`/`apply_combat_damage`'s descriptions with one
disambiguating sentence each. Reran round 6's two hardest scenarios
(`start_combat_single_npc`, `damage_npc`) against the two most
decision-relevant configs, N=3 trials each instead of 1, per round 6's
own conclusion that single-run verdicts aren't reliable.

| Config | start_combat_single_npc | damage_npc |
|---|---|---|
| `gpt-5.6-luna`/medium (today's prod) | 3/3 ✅ (all turn 1, 2459-3934ms) | 3/3 ✅ (2 of 3 via a get_combat_status detour that still lands correctly, 3060-4155ms) |
| `gpt-6-luna`/none | 3/3 ✅ (all turn 1, 1242-1477ms) | 3/3 ✅ (all turn 1 — **no detour at all**, 1504-1816ms) |

**This confirms the wording hypothesis, not just "the model got lucky
again":** both scenarios go from round 6's mixed/coin-flip results to a
clean 6/6 across both configs once the ambiguity is removed — and for
`gpt-6-luna`/none specifically, `damage_npc` stopped taking the
`get_combat_status` detour entirely (all 3 trials resolved directly to
`damage_combatant` on turn 1), while the baseline model still took that
detour 2 of 3 times even with the fixed wording (still landing correctly,
just slower). This is a meaningfully positive result for the tiering
question too: with the ambiguity fixed, `gpt-6-luna`/none is both
correct (6/6) and consistently faster (1242-1816ms) than today's
production baseline (2459-4155ms) on exactly the two scenarios that
looked shakiest before this fix.

**Actionable takeaway independent of the model/tiering decision:** these
two wording fixes look like a legitimate, low-risk improvement to make to
the *real* production prompt/tool descriptions in `app/keeper.py`
regardless of which model ends up running the Executor — they measurably
reduced ambiguity for both the current production model and the
candidate. Worth splitting into its own small bug/enhancement branch
rather than bundling into this still-discussion-only tiering spec.

### Round 8 — full 8-scenario sweep against the real fixed prompt (PR #58)

The wording fix landed as its own branch: `bug/combat-trigger-prompt-and-
damage-tool-ambiguity` (PR #58, spec at `docs/specs/bug-combat-trigger-
prompt-and-damage-tool-ambiguity.md`). Reran round 4's full 8-scenario
sweep — not just the 2 scenarios round 7 targeted — against that branch's
real `app/keeper.py` (not a hand-copied string), `gpt-6-luna`/none, each
scenario at its correct-scope tool set (A+B or A+C), to check for
regressions in the 6 scenarios that weren't part of the original
diagnosis.

| Scenario | Result |
|---|---|
| single_skill_check | 3104ms ✅ |
| dual_skill_check | 6092ms ✅ (both skill_check calls landed, unlike round 4's single-call miss) |
| san_check | 2009ms ✅ |
| start_combat_single_npc | 1364ms ✅ |
| start_combat_multi_npc | 1272ms ✅ |
| damage_npc | 1308ms ✅ |
| luck_spend | 2595ms ❌ (`skill_check`) |
| scenario_lookup | 1936ms ✅ |

7/8, no new regressions — the two originally-targeted scenarios and all 6
others (including `dual_skill_check`, which round 4 had failed) pass
cleanly. `luck_spend`'s single-run miss here is consistent with the
already-documented non-determinism on that scenario from round 6 (a
different wrong tool each time it's failed so far: `search_scenario` in
round 4, `get_character_sheet` in round 5, `skill_check` here) — unrelated
to either of PR #58's fixes (`luck_spend` doesn't touch `start_combat` or
the damage tools), not something that PR needed to address, and a
candidate for its own follow-up investigation if `luck_spend` reliability
becomes a priority later.

### `luck_spend` scenario investigated — it isn't model instability, it's a bad test scenario

Traced how spending Luck to improve a skill check result actually works in
production (`app/keeper.py:1899-1976`, `app/legacy_commands.py:1530-1620`,
`app/discord_bot.py:885-951`), instead of continuing to treat the 3
different "wrong" answers across rounds 4/5/8 as evidence of model
flakiness.

**Finding: "花費 Luck 來提升這次偵查檢定的結果" describes an action that
never reaches the Executor's tool-calling loop at all in real production.**
When `skill_check` rolls a near-miss, it *itself* detects buyable Luck
options (`luck.buyable_options`) and sets `pending_luck=True` on its own
result — the player is then shown a Discord button, and clicking it
invokes `handle_luck_decision` → `_resolve_luck_decision_deterministically`,
which pops `state.pending_luck_decisions` and deducts Luck directly. This
entire flow is deterministic application code triggered by a button click,
**not an LLM tool call** — there is no scenario where the real Executor is
asked "the player wants to spend Luck on this check, which tool do I
call?" and the honestly correct answer is "none of them; this isn't an
LLM decision."

This means **this test scenario has never had a correct answer to score
against**, in any of rounds 1-8 — the "instability" observed (three
different wrong tool choices: `search_scenario`, `get_character_sheet`,
`skill_check`) was the model reasonably improvising an answer to an
ill-posed question the real system would never actually ask it, not a
real reliability gap. This scenario should be dropped or replaced before
any future round: `adjust_character`'s own description does legitimately
cover *other*, genuinely freeform Luck expenditures outside the
structured check-buyup flow (its docstring example is literally "花費幸運
點" — e.g. a KP-adjudicated "spend Luck to have happened to bring the
right tool," not tied to any specific just-rolled check) — a corrected
scenario for future rounds should use one of *those* instead, e.g. "玩家
決定花 10 點 Luck，希望這次剛好帶了能撬開這扇門的工具" (spending Luck on
a narrative contingency, not on an already-executed skill check).

**Correction to earlier rounds' scorekeeping:** round 4's "8 scenarios"
and round 8's "7/8, no regression" claims should both be read as "7/7
scenarios with a real correct answer," not "1 real miss" — `luck_spend`
wasn't measuring anything meaningful to begin with.

## Model decision: `gpt-6-luna`/none for OpenAI

With `luck_spend` disqualified as a scenario and the wording fix landed
(PR #58), the accumulated evidence across rounds 1-8 now supports a
concrete pick for the OpenAI-side `EXECUTOR_MODEL` default:

- **Correctness**: `gpt-6-luna`/none passed all 7 scenarios with a real
  correct answer in round 8's full sweep (post-fix, correct tier scope
  per scenario), and went 6/6 in round 7's repeated N=3 trials on the two
  scenarios that had looked shakiest pre-fix — same as the current
  production baseline (`gpt-5.6-luna`/medium, also 6/6 in round 7).
- **Speed**: consistently faster than the baseline across every round
  that measured both — round 8's post-fix sweep: 1272-6092ms for
  `gpt-6-luna`/none vs. round 6's baseline showing 2384-8668ms on the same
  two hardest scenarios (not re-measured post-fix, but no round has ever
  shown baseline faster).
- **Other OpenAI configs ruled out**: `gpt-6-luna`/low showed a real
  miss in round 1 (called `roll_dice` instead of `skill_check`) and
  hasn't been retested post-fix; `gpt-6-luna`/medium had a real turn-2
  stall (zero tool calls) in round 6 and also hasn't been retested
  post-fix — neither has `none`'s clean track record, and `none` is also
  the cheapest/fastest option, so there's no reason to prefer them absent
  new evidence.

**Recommendation: `EXECUTOR_MODEL` (OpenAI) = `gpt-6-luna`, reasoning
effort = `none`.** This is a recommendation, not yet a decision — needs
the user's confirmation before moving to implementation (per this
branch's "spec first" workflow, and per the earlier decision to build the
per-provider config surface but tune OpenAI first).

**User confirmed this model pick.** Scope for the first implementation
pass, per explicit instruction: **model tiering only — dynamic Tier A/B/C
tool scoping is deliberately deferred, not implemented now.** The
Executor keeps using the full real tool list (`tools_for_speaker_role`,
unchanged from PR #57) for this pass; only `EXECUTOR_MODEL`/reasoning-
effort plumbing lands. This sidesteps the not-yet-designed per-iteration
rescoping requirement entirely for now (see that section above) — nothing
about combat-state-dependent tool availability changes in this pass, so
that open design question stays deferred along with the rest of the
dynamic-scoping work, to be picked up as its own follow-up later.

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
