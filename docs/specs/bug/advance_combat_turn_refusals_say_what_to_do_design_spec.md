# The refusals of advance_combat_turn say what to do

## Problem

In a Haunting run an enemy had the first turn, and the Keeper spent the first turns trying to skip it: `advance_combat_turn` with `skip=true` on the enemy came back "Only the acting investigator can skip their own turn" three times, each time as a failed tool call the player saw as "處理這個行動的工具失敗了". Another call came back "Managed resource mutation requires an explicit stable event_id", which names no tool and gives no example, and the Keeper had to guess an id and call again.

## Change

* `skip=true` on an enemy or ally now says what to do instead: run `plan_enemy_turn` then `run_enemy_combat_plan`, and advance without `skip` afterwards. Skipping another player's turn keeps its message.
* A call without `event_id` says that `advance_combat_turn` needs a stable `event_id`, gives the shape (`<combat_id>:advance:round<N>:<actor>`) and says to reuse the id only to retry the same call.

## Not done

Nothing is skipped or advanced on the Keeper's behalf, and no id is made up: an invented id would make a repeated call advance twice.

## Tests

`tests/test_combat_wiring.py`: a call without `event_id` and a `skip` on an enemy are refused with the new wording.
