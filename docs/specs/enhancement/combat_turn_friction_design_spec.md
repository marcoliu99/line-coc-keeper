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
- `advance_combat_turn` derives `event_id` as `<combat_id>:advance:round<N>:<actor combatant id>` when omitted (the actor named by any reference, PR #241). A retry of the same advance replays; the Keeper is never refused for a missing id.
- `skip=true` is accepted for the current NPC ally and for the current enemy whose turn the engine cannot play (`combat.enemy_turn_blocker`: no card, incomplete card or no attacks); the result's note names the reason and the rules-faithful alternative (register the scenario's attacks with `add_npc_to_combat`). An enemy with attacks still cannot be skipped; an investigator's turn is still only the player's own to skip.
- `combat_flow.advance_combat` returns the successful transition with `enemy_turn` beside it when the next enemy's plan or run fails, instead of the failure alone.
- `turn_resolution._mutation_evidence` counts a cited `advance_combat_turn` that changed the round or current actor, a cited `run_enemy_combat_plan` that completed or now waits on the defender, and a cited `resolve_combat_ruling` as the turn's effect.
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
