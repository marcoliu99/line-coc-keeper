# Spec: Executor dynamic (combat-state-dependent) tool scoping

**Status: discussion only, nothing implemented yet.**

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
- **implementation end**: N/A — discussion branch

## Purpose & Scope

Split out of `docs/specs/enhancement-executor-model-tiering-and-tool-
scoping.md` (that spec's original title covered both model tiering *and*
tool scoping together). That spec's model-tiering half is done — PR
merged `EXECUTOR_MODEL`/`EXECUTOR_REASONING_EFFORT` config for `app/
agents/executor.py` (OpenAI: `gpt-6-luna`/none). Per explicit instruction,
the tool-scoping half was deliberately deferred rather than implemented
in that same pass, and now gets its own mini-spec to pick up later.

The real production tool list is 35 tools
(`keeper._tools_for_speaker_role("player")`), most of which are irrelevant
to any given turn (combat tools when not in combat, etc.). Filtering this
down before sending it to the model should cut prompt size and, per the
parent spec's trials, may also improve correctness by removing
distracting near-miss options — round 2 there showed a scoped 4-tool set
cut latency ~42% for `gpt-4o-mini` specifically, though that gain didn't
hold up as the deciding factor once `gpt-6-luna`/none turned out fast
enough at the full tool count anyway (see the parent spec's "Model
decision" section) — so this work's case now rests more on prompt-size/
cost reduction for the common (non-combat) case than on being required
for correctness or speed.

## Design (carried over from the parent spec, already reviewed there)

Classified all 35 real tools into three tiers by reading every tool's
actual description (`app.keeper.TOOLS` + `_SEARCH_SCENARIO_TOOL`), not
guessing from the name alone:

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

Non-combat scope = Tier A + B = 21 tools (down from 35, a 40% reduction).
Combat scope = Tier A + C = 26 tools (a smaller reduction — combat
genuinely needs most of the tool surface: initiative, damage, enemy AI
planning, status effects, and defense choices are all separate tools,
which is *why* combat turns are the ones that tend to burn through
`MAX_TOOL_ITERATIONS` in the first place).

User confirmed: use this Tier A/B/C split as designed, not a forced "~10
tools" count (an earlier ambiguous target from an external proposal —
getting to a literal ~10 would mean cutting things players routinely do
outside combat, a feature regression dressed up as an optimization, not a
safe trim).

## The unresolved design blocker: per-iteration rescoping, not per-turn

Found while planning the parent spec's round 4 test harness, still
unresolved — **this is the reason this work wasn't folded into the model-
tiering implementation pass**, not just a scope-cut of convenience.

A single real turn can legitimately call `start_combat` (Tier B) and then
`add_npc_to_combat` (Tier C, requires `state.combat.active`) in the *same*
turn — this is the exact original bug shape from `docs/specs/bug-add-npc-
to-combat-duplicate-name-guard.md` (`search_scenario` → `start_combat` →
`add_npc_to_combat` × 2, all in one turn). If the tool list offered to the
model is computed **once at turn start** (before any tool calls that
turn), based on pre-turn `state.combat.active`, then `add_npc_to_combat`
would never be available in that same turn, since combat wasn't active
yet when the list was built.

This means "dynamic" scoping has to mean **recomputed before each
iteration of the tool-calling loop** — re-checking `state.combat.active`
after every tool execution, the same way the Keeper already reloads fresh
state each round via `_mutate_and_save_state` — not computed once per
turn. Concretely, this means:

- `app/agents/executor.py`'s per-turn `tools = tools_for_speaker_role(...)`
  call (see `app/agents/tool_gateway.py`, fixed for the RAG/kp_assistant
  case by PR #57) needs to become something the tool-calling loop itself
  re-evaluates between iterations, not a value computed once before the
  loop starts.
- Each provider's `run_conversation` (`app/providers/{openai,anthropic,
  gemini}_provider.py`) currently takes a single static `tools: list[dict]`
  argument for the whole call. Supporting per-iteration rescoping means
  either: (a) passing a callback the loop invokes each iteration to get
  the current tool list (reading live `state.combat.active`), or (b)
  moving the tier-scoping decision inside each provider's iteration loop
  itself, given a state reference. Neither has been designed in detail
  yet — this is real, non-trivial surface area across all three provider
  adapters, not a one-line change.
- Needs its own real-API verification once implemented: a multi-round
  conversation test (start combat, then add 2+ NPCs, in one simulated
  turn) confirming the tool list genuinely updates mid-turn — a single-
  shot test can't validate this, the same lesson the parent spec's round
  4-5 methodology work already learned the hard way for a different
  question (see that spec's "Important methodology finding" and "Round 5"
  sections).

## Open questions for whoever picks this back up

1. Callback-based re-scoping (b above) touches all three provider
   adapters' core loops — is that worth doing for a 40% prompt-size cut
   on the *non-combat* case only, given the model-tiering work already
   captured most of the available speed/correctness win without touching
   tool scoping at all? Worth re-confirming this is still worth doing
   before investing in the provider-loop refactor.
2. If pursued, should all three providers gain this at once, or should it
   land for OpenAI only first (matching the model-tiering work's own
   "OpenAI first" precedent), given Anthropic/Gemini have no verified
   real-API data for this change yet either?

## Notes
- No code changes in this branch — spec/discussion only.
- Full real-API verification history (rounds 1-8, the wording-fix bug
  found and fixed as PR #58, the `luck_spend` scenario invalidation, and
  the model decision) lives in `docs/specs/enhancement-executor-model-
  tiering-and-tool-scoping.md` — not duplicated here, since none of that
  evidence was specific to tool scoping (it was gathered testing full vs.
  scoped tool sets together with model comparisons, before the two
  workstreams were split).
