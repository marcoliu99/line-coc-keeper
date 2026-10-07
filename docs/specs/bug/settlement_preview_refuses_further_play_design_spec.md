# A pending settlement preview refuses further combat play

## Problem

In a Haunting run the rat pack was already down and the Keeper had called `preview_combat_settlement`, but every later player turn made the Keeper advance the battle again. Advancing is still allowed while the preview waits, and each advance moved the round on and changed the battle's revision, so the preview became stale and `confirm_combat_settlement` was refused. The round counter climbed from 15 to 36 over about 23 turns, nearly every player action ended with "this action cannot continue", and the battle only closed on turn 55.

## Change

While the battle is in the `SETTLEMENT` phase, `advance_combat`, `declare_action`, NPC planning (`plan_enemy_turn`) and running an NPC plan (`run_enemy_plan`) refuse before changing anything. The refusal says the battle is over and names the step to take: `confirm_combat_settlement` with the pending `settlement_id` (also returned as a field), or `rollback_combat`. A retried advance that was already recorded still replays its stored result.

A preview taken too early used to have no way back: the only exits were confirming (which ends the battle) or `rollback_combat` (which discards it). The Keeper now has `cancel_combat_preview` (`combat_id`, `event_id`, `reason`): while a preview is pending it withdraws the preview and sets the phase back to `READY`, so the battle goes on; resources, rolls and receipts are untouched, and a retry with the same `event_id` replays. The old settlement id is then stale; the next `preview_combat_settlement` makes a new one. With no preview pending it is refused. To stop a preview, cancel, preview, cancel loop, a cancel is also refused when the last thing recorded in the battle is already a cancel: something must be played in between.

## Not done

Settling is still the Keeper's call; nothing confirms or cancels a preview on its own, and a preview is not refused because enemies are still alive (a battle can end with enemies standing: they flee, surrender, or the party is down). Other combat tools are unchanged.

## Tests

`tests/test_combat_flow.py`: with a preview pending, advancing, declaring, planning an NPC turn and running its plan are refused with the settlement id, the round and revision do not move, and the same preview then commits; cancelling a preview resumes the battle, replays on retry, makes the old settlement id stale and is refused when no preview is pending (`tests/test_combat_wiring.py` covers the Keeper tool).
