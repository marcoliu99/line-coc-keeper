# Combat turns: one click defends, a turn can always be ended, and the Keeper is told the next step

[繁體中文](combat_turn_friction_design_spec_zh.md) | [Docs index](../../README.md)

Category: `enhancement`. Status: **implemented**. Base: `main_v2` at `ac6c943` plus PR #241.

## Problem

One attack-and-defend exchange cost a player one typed message, two to five button clicks and six to eight sequential model requests, and the fight stopped whenever any of these happened:

- The defence button read 「選擇並擲 閃避」 but only recorded the choice; the roll needed a second button, Luck a third. The prompt did not say who was attacking, and the "what you need to roll" hint was never shown because the managed engine never set `attacker_tier`.
- `advance_combat_turn` was refused when the Keeper omitted `event_id` (12 refusals in one run before PR #241 fixed the actor reference), and a reused id silently replayed an old advance.
- An enemy registered with HP only (`/coc combat addnpc`, or a card without attacks) could not act (`run_enemy_plan` refuses an incomplete card) and could not be skipped (a skip on an enemy's turn pointed back at the enemy flow), so initiative froze. An NPC ally's turn could not be ended at all.
- An advance that did move the turn was reported as a failure when the next enemy's turn could not be played, so the Keeper retried and the player read 「工具失敗了」.
- The turn validator counted only a declared attack, a registration, a settlement or a skip as the turn's effect; a plain advance, an enemy plan or a ruling left the turn "unverified" and the player got the generic fallback.
- The narrator that runs after a settled combat check was never told to advance; the fight sat on a completed actor until someone typed again.
- The Luck prompt in combat listed tiers as `regular`/`hard`; the static prompt said "declare_combat_action then run_combat_action" although the declaration runs the action itself, and `adjust_character` sent the Keeper to damage tools a managed battle refuses.

The CoC 7e mechanics are unchanged: Dodge and Fight Back are still the defender's choice resolved as an opposed roll, Luck is still the player's decision, and only turns the engine cannot play may be given up.

## Change

- `ManagedCombatChecks._choose` rolls the defence it just registered in the same transaction. One click chooses and rolls; the Luck decision, if offered, stays a separate click. The choice receipt stores the roll, so a repeated click replays it. `_defense_choice` records `attacker_name` and `attacker_tier` on the pending choice; the prompt says who attacks and what each option needs.
- `advance_combat_turn` derives `event_id` as `<combat_id>:advance:round<N>:<actor combatant id>:<actions the actor completed this round, skips excluded>` when omitted. A retry of the same advance replays (a skip adds no counted action, so its retry derives the same id); an actor brought back later in the same round by `set_initiative` after another action gets a new id. The actor reference resolves through `combat.resolve_actor_reference` (the current actor's own fields first, then the global lookup; PR #241's rule, now shared by the engine and the tool). The Keeper is never refused for a missing id. Two skips by the same actor in one re-ordered round would still derive the same id; the second replays the first.
- `skip=true` is accepted for the current NPC ally and for the current enemy whose turn the engine cannot play (`combat.enemy_turn_blocker`: no card, or an `incomplete` card registered with neither attacks nor abilities); the result's `skipped` entry, beside whatever the next actor's turn returned, names the reason and the rules-faithful alternative (register the scenario's attacks with `add_npc_to_combat`). An enemy with attacks still cannot be skipped; an investigator's turn is still only the player's own to skip.
- `prompt_config._combat_next_step` treats an owed CON check (`INJURY_CHECK`) like any other pending roll: do not advance.
- `combat_flow.advance_combat` returns the successful transition with `enemy_turn` beside it when the next enemy's plan or run fails, instead of the failure alone.
- `turn_resolution._mutation_evidence` counts, as the turn's effect, a cited `advance_combat_turn` that ended the acting player's own combatant's turn (round or current actor changed), a cited `run_enemy_combat_plan` whose attack targets that player and completed or now waits on their choice or roll, and a cited `resolve_combat_ruling` on an action the player's combatant is in. Moving someone else's turn along is not the player's action; the Keeper answers that with `deferred`.
- `prompt_config` adds a 【戰鬥下一步】 line to the settled-check block from the combat receipt: advance (with the argument to pass) when the action completed, do not advance when another player's choice or roll is pending, resolve the ruling when paused. The follow-up narrator's instruction says so too.
- Luck tiers in the combat prompt read 一般成功／困難成功／極限成功. Prompt text says `declare_combat_action` runs the action and `run_combat_action` only resumes one; `adjust_character` points enemy damage at the combat flow; the five tools a managed battle always refuses say so first in their descriptions.
- `/coc combat next` during a battle answers with the status and whose turn it is instead of a bare refusal.

## Not done

- Advancing is still the Keeper's call; the engine does not advance by itself after a completed action.
- Fight Back still uses Brawl and 1D3 for an investigator rather than the held weapon.
- A `no_defense` choice against a ranged attack resolves without a narrated turn.
- The help entries for `/coc combat damage` and `end` still describe commands a battle refuses.

## Tests

`tests/test_combat_turn_friction.py`; the B-scenario suite in `tests/test_combat_engine.py` now defends in one call.
