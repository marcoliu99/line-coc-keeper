# Spec: continuing-damage dice rolls misapplied to the wrong characters' Luck stat

## Changeset Tracking
- **main_v2 start**: origin/main_v2:80f245fc6591a021b40dbbedb9d44d41e6b9ec9f
- **implementation end**: bug/continuing-damage-rolls-corrupt-luck-stat (rebased onto origin/main_v2:cb11eb87c02ff850b8ee6c86e0d5a34b2c92e75c) — ruff/mypy/compileall/pytest all green. **Stopgap only, landing as-is per explicit decision (2026-09-25)**: the deeper root cause (gpt-6-luna/none's unreliability tracking continuing combat state) is NOT fixed by this PR — see "Open design question" below, which continues in a separate branch (`enhancement/executor-reasoning-effort-for-combat-ongoing-effects`) rather than blocking this stopgap.

## Purpose & Scope

Real production log evidence (`/Users/marcoliu/profile-async2.log`,
2026-09-24 15:57:13-15:57:43, `gpt-6-luna`/`none` Executor turn during a
fight with the NPC 柯比特/Corbitt): a Molotov cocktail set Corbitt on
fire, requiring ongoing per-round fire damage. The turn's tool sequence:

```
roll_dice ×3   1d10 -> 6, 7, 10        (meant as each round's fire damage)
adjust_character  investigator=marco, field=luck, value=10
adjust_character  investigator=Ken,   field=luck, value=32
adjust_character  investigator=marco, field=luck, value=15   (contradicts the value=10 call two steps earlier, same turn)
apply_combat_damage ×2  target=柯比特  damage_type=fire  (0, then 1 damage — mostly absorbed)
```

The three `1d10` rolls were meant to represent per-round fire damage
against Corbitt, but instead of being applied to him, `adjust_character`
was called with `field=luck` against **the two investigators (Marco,
Ken) — not the target of the damage at all** — overwriting their actual
Luck stat with values that don't correspond to any real Luck-changing
event. Marco's Luck was set twice in the same turn to two different,
contradictory values (10, then 15). This is real player-character save
data corruption, not just wasted time/iterations — confirmed via the
`StateReducer` log line showing these `adjust_character` calls actually
applied and saved (`state_save_success` between each one).

The player/KP caught the *damage-tracking* half of this in real time —
the Narrator's very next turn opens with 「你說得對，先前的處理不完
整...不應任意改寫柯比特目前的生命值」(acknowledging the fire-damage
handling was wrong) — but **the corrected narration never mentions the
Luck corruption at all**, meaning it went unnoticed even by the humans
present, and Marco/Ken's Luck values are still whatever this incident
left them at unless someone manually re-checked the character sheet.

## Root cause found by reading the actual tool definitions

`app/keeper.py` already has a tool built for exactly this situation —
`add_combat_effect` (`app/keeper.py:676-701`), whose own description's
**first example is literally "燃燒"** (burning): it accepts dice-string
damage (`"1d6+1"`), `damage_type` (`fire` etc.), `timing`
(`round_start`/`turn_start`/`turn_end`/`round_end`), and
`remaining_rounds` — call it **once** when the Molotov hits, and the
system resolves the fire damage automatically every subsequent round.
There was no need for the model to manually `roll_dice` each round at
all.

Two real, verifiable problems, either of which (likely both) contributed:

1. **The model didn't reach for `add_combat_effect` for this continuing-
   damage scenario** — nothing in the static combat prompt explicitly
   says "for a continuing/DoT effect like a Molotov fire, use
   `add_combat_effect`, don't hand-roll it every round." Unlike
   `start_combat`'s trigger rule (PR #58) or `add_npc_to_combat`'s
   stat-lookup rule, there is currently no equivalent trigger sentence
   for "an ongoing environmental/status effect just started."
2. **`adjust_character`'s `field=luck` was used as if it were a generic
   damage-application call**, targeting the wrong characters entirely.
   `adjust_character`'s own description (`app/keeper.py:381-387`) is
   fairly clear about what `field=luck` is for ("花費幸運點" — spending
   Luck points) and does NOT mention applying damage of any kind — this
   looks like the model conflating "I have numbers to apply somewhere"
   with "there's a numeric adjustment tool available" without checking
   that the tool/field/target actually matches the situation, rather
   than a description-wording gap on `adjust_character`'s side
   specifically. Needs real-API investigation to confirm which
   intervention (steering toward `add_combat_effect`, hardening
   `adjust_character`'s description against off-target use, or both)
   actually changes the behavior.

## Real-API verification

Three rounds, all real OpenAI calls (real cost each time).

### Round 1 — scoped-down 4-tool test (inconclusive)

First attempt used a curated 4-tool set (`roll_dice`, `adjust_character`,
`apply_combat_damage`, `add_combat_effect`) with a hand-written prompt,
both for a fresh trigger message and for a simulated "already 2 rounds
into hand-rolling this effect" history. **Could not reproduce the bug in
either shape** — all 4 combinations (OLD/NEW × fresh/history) correctly
called `add_combat_effect` and never touched `field=luck`. Too easy/clean
a test: a small, well-labeled toolset makes the right tool obvious
regardless of wording.

### Round 2 — full production scale (36 real tools, real prompt)

Rebuilt the scenario using the actual `app/combat.py` machinery (real
`start_combat`/`add_npc`, not hand-constructed state), the real
`_build_static_prompt`/`_build_dynamic_prompt` output (10,873 chars), and
the real 36-tool list from `_tools_for_speaker_role("player")`, with the
same "already hand-rolling" history. **Still did not reproduce the exact
`field=luck` write** — but revealed something more important: under the
production-scale tool count, `gpt-6-luna`/`none` didn't even attempt to
handle the fire effect at all in the OLD/NEW comparison run (`get_combat_
status → advance_combat_turn → ...` — just cycling through generic
combat-turn management, never touching the fire).

### Round 3 — isolating `reasoning_effort` as the variable (confirms user's hypothesis)

Same full-production-scale setup, same history, only `reasoning_effort`
varied:

| Config | Result |
|---|---|
| `medium` (1 trial) | ✅ `get_combat_status → roll_dice → apply_combat_damage(target=柯比特, correct) → add_combat_effect` |
| `none` (4 trials total) | ❌ **4 different wrong outcomes, zero correct `add_combat_effect` calls** |

The four `none` failures, each genuinely different:
1. `get_combat_status → advance_combat_turn ×3 → (stops)` — never
   touches the fire effect at all.
2. `get_combat_status → advance_combat_turn → plan_enemy_turn →
   resolve_enemy_action(outcome.tags=["burning"], damage=0) → advance_
   combat_turn` — treats "burning" as if it were Corbitt's own special-
   ability outcome to resolve, not an environmental effect on him.
3. `get_combat_status → get_character_sheet → [search_scenario +
   search_memory + clear_pending_check ×2, all in one round] →
   end_combat()` — **ends combat entirely**, despite Corbitt (HP 30, still
   burning) never being defeated.
4. Same as #1 (repeated for a 4th independent trial).

**This confirms the user's hypothesis, and reframes the bug**: this
isn't primarily a tool-description wording gap (`adjust_character`
vs. `add_combat_effect` disambiguation) — it's that **`gpt-6-luna`/`none`
is unreliable at tracking/resolving a continuing effect that isn't
freshly restated in the latest message**, and fails in a *different* wrong
way almost every trial. The real production incident's specific failure
mode (writing dice results into the wrong characters' Luck stat) is very
plausibly just one more member of this same family of wrong outcomes —
not a distinct, narrower bug that a single description fix would close.

## Open design question: reasoning effort for combat turns with ongoing effects

The wording-only fixes drafted below (an `add_combat_effect` trigger
rule, a hardened `adjust_character` description) would likely help *some*
of these failure modes (e.g. steering away from `adjust_character`
misuse specifically) but **cannot be expected to fix all of them** — a
model that ends combat prematurely or misattributes an effect to the
wrong mechanism isn't failing because of unclear tool descriptions, it's
failing to track state it needs to reason about across turns, which is
closer to what reasoning effort itself is for.

This surfaces a real, not-yet-resolved design question for
`docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md`'s
`EXECUTOR_REASONING_EFFORT_OPENAI=none` choice: should combat turns
specifically (or turns where `state.combat.active` and a continuing
effect is on record) use a higher reasoning effort than ordinary turns,
even at the cost of some of the speed gains `none` was chosen for? This
branch's scope is the immediate bug; that broader tiering question
belongs in the tiering spec if pursued, but is flagged here since this
investigation is what surfaced the evidence for it.

### Follow-up sampling: `low` as a possible middle ground

Ran additional real trials at `reasoning_effort=low` against the same
real scenario (same real state/prompt/tools, same "already 2 rounds into
hand-rolling" history) to see whether a cheaper-than-`medium` tier could
still avoid `none`'s active failures:

| Effort | Trials | Correctly called `add_combat_effect` | Actively wrong (corrupts state / ends combat / misattributes) |
|---|---|---|---|
| `none` | 4 | 0/4 | **4/4** — 4 different wrong outcomes, including one that called `end_combat()` outright |
| `low` | 7 | 2/7 | **0/7** |
| `medium` | 1 | 1/1 | 0/1 |

The 5/7 `low` "misses" were not wrong tool calls at all — the model made
**no tool call**, instead directly narrating something like:

> 「柯比特身上的火焰仍未熄滅，黑煙貼著天花板翻捲；但這一輪的火焰傷害尚未
> 結算...Ken，你要怎麼行動？」

i.e. it acknowledged the fire is ongoing and punted the mechanical
resolution back to the player, rather than guessing wrong. This never
corrupts state, but the effect still never gets set up mechanically —
this scenario will resurface the same unresolved situation next turn.

**Reading**: `low` looks like a genuinely different failure profile from
`none`, not just "a bit better" — it trades reliability (2/7 fully
correct) for safety (0/7 actively wrong), whereas `none` was unreliable
*and* unsafe (4/4 actively wrong). This makes `low` worth its own
consideration as a middle-ground candidate for combat-with-ongoing-
effects turns specifically, separate from whether `medium` is used
everywhere in combat — but this is still N=7/4/1, well short of the
tiering spec's own N≥3-5-per-condition bar for a real decision. Not
deciding this here — needs the user's input on whether it's worth
pursuing, and if so, real-API trials at the tiering spec's own level of
rigor (this branch's samples are suggestive, not conclusive, given this
project's already-documented high run-to-run variance at low effort
tiers).

## Changes (pending real-API verification)

Candidates, not yet decided — need real-API trials to see which actually
moves the needle before touching production text:

- Add an explicit trigger rule to the static combat prompt (near the
  existing `start_combat`/`add_npc_to_combat` rules in
  `app/keeper.py`'s `_build_static_prompt`) for continuing/environmental
  effects: when narration introduces an ongoing effect (fire, poison,
  bleed, an environmental hazard), call `add_combat_effect` once instead
  of manually re-rolling and re-applying it every round.
- Possibly strengthen `adjust_character`'s description to be explicit
  that `field=luck` must never be used to apply damage-like effects to a
  combatant, and that it must never be used to represent an unrelated
  dice roll's result.
- No changes planned to `add_combat_effect`, `apply_combat_damage`, or
  `adjust_character`'s actual implementation — this is a tool-selection/
  targeting problem, not a bug in what any individual tool does when
  called correctly.

## Testing Strategy

- Real-API verification first (per explicit instruction): reconstruct a
  scenario matching the real incident (an NPC on fire from a thrown
  Molotov, combat ongoing) and compare, across several real trials,
  whether the model reaches for `add_combat_effect` vs. hand-rolling
  per-round damage, and whether any `adjust_character` calls target the
  correct entity — before and after whatever wording change the
  verification points to.
- Once a fix is confirmed empirically, consider a lightweight regression
  test asserting the new prompt/description text exists (matching this
  project's established pattern for wording-only fixes — can't
  meaningfully unit-test the model-behavior effect itself).
- No production code path changes are anticipated, so no behavioral
  application-logic tests are expected here — TBD if the verification
  results suggest otherwise.

## Notes
- Distinct root cause from `docs/specs/bug-advance-combat-turn-skips-
  pending-action.md`, even though both were found in the same log
  review session — kept as a separate branch per explicit instruction.
- This is a more serious class of bug than the other two found in the
  same review (fragmented `search_scenario` queries, `advance_combat_
  turn` spam): those waste time/iterations but don't corrupt saved game
  state. This one silently changed two real characters' Luck values to
  incorrect numbers, and nobody — not the player, not the KP, not the
  Narrator's own self-correction — noticed it happened. Worth
  prioritizing accordingly once verification is done.
- Possible follow-up (not this branch's scope): whether `adjust_
  character`'s tool result or some validation layer should sanity-check
  that a `field=luck` adjustment's target/value combination is plausible
  before saving, as a defense-in-depth measure independent of prompt
  wording — flagging for later discussion, not deciding here.
