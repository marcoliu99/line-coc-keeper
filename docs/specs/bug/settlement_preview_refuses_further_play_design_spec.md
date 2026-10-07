# A pending settlement preview refuses further combat play

## Problem

In a Haunting run the rat pack was already down and the Keeper had called `preview_combat_settlement`, but every later player turn made the Keeper advance the battle again. Advancing is still allowed while the preview waits, and each advance moved the round on and changed the battle's revision, so the preview became stale and `confirm_combat_settlement` was refused. The round counter climbed from 15 to 36 over about 23 turns, nearly every player action ended with "this action cannot continue", and the battle only closed on turn 55.

## Change

While the battle is in the `SETTLEMENT` phase, `advance_combat` and `declare_action` refuse before changing anything. The refusal says the battle is over and names the step to take: `confirm_combat_settlement` with the pending `settlement_id` (also returned as a field), or `rollback_combat`. A retried advance that was already recorded still replays its stored result.

## Not done

Settling is still the Keeper's call; nothing confirms a preview on its own. Other combat tools are unchanged.

## Tests

`tests/test_combat_flow.py`: with a preview pending, advancing and declaring are refused with the settlement id, the round and revision do not move, and the same preview then commits.
