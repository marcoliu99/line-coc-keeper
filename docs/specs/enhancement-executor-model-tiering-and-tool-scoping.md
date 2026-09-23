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
gateway.py:40` → `keeper.TOOLS`), not just log inference. **Needs its own
bug-fix branch**, same category/urgency as PR #55's duplicate-NPC-add fix
— tracked here only because it was discovered here, not because fixing it
belongs in this spec.

## Open design questions for this mini-spec

1. **Scoped tool-set size**: user's target is **~10 tools** (not the 4-6
   tested so far) — wide enough to cover skill checks, SAN, combat
   start/damage/turn-advance, and scenario/character lookups without
   needing separate scoped sets per scene type. Needs a concrete list
   before implementation — draft below, needs review.
2. **Per-provider defaults for `EXECUTOR_MODEL`**: Gemini's original code
   sketch hardcoded `"gpt-4o-mini"` as the default, which would silently
   break the moment `LLM_PROVIDER` is `anthropic` or `gemini` (no
   equivalent-tier model name to fall back to). Any real implementation
   needs a per-provider default, not one hardcoded string — and per the
   round-1/2 data, `gpt-4o-mini` doesn't even look like the strongest
   OpenAI-side candidate anyway (`gpt-6-luna`/`none` came out ahead on both
   speed and correctness in every trial so far).
3. **How many more trials before this is implementation-ready?** 8
   scenarios × 1 run each is a direction indicator, not validation. Given
   `gpt-6-luna`/`none` has now been ahead of baseline on every single trial
   across 3 rounds, the marginal value of *many* more single-shot trials
   is probably lower than: (a) fixing the `STATIC_INSTRUCTIONS`-too-short
   test artifact so `start_combat_single_npc` gets a fair test, and (b)
   running the survivors 2-3× each for consistency instead of once, the
   way round 1 did.

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
