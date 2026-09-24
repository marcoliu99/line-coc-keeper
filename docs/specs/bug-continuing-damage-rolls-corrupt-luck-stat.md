# Spec: continuing-damage dice rolls misapplied to the wrong characters' Luck stat

## Changeset Tracking
- **main_v2 start**: origin/main_v2:80f245fc6591a021b40dbbedb9d44d41e6b9ec9f
- **implementation end**: TBD

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
