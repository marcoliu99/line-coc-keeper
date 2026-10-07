# A refused turn in a battle names whose turn it is

## Problem

When a player's action in a battle could not be played, the table was told "劇本裡沒有足夠的內容可以據以裁決這個行動" or "這個行動無法進行", followed by the people and places already shown ("人物：鼠群"). That wording is for exploring. In a Haunting battle the real reasons were that it was not the player's turn, or that the enemies were already down and the battle was waiting to be settled, so the players were sent looking for something to ask about.

## Change

For the three reasons whose wording talks about the scenario (`no_scenario_evidence`, `executor_no_action`, `unsupported_action`), a turn in a running battle gets `turn_fallback.combat_guidance` instead, without the scene hints:

* the battle is in its settlement phase: it is over and waiting for the Keeper to settle;
* every enemy is down: same, waiting to be settled;
* a ruling is pending (`NEEDS_RULING`): the battle is paused for the Keeper's ruling;
* a Luck decision is open (`LUCK_DECISION`): use the Luck button or `/coc luck`;
* a check or choice is still open (an open interaction, an unfinished action, or a phase other than ready): finish it first;
* the current actor is down, or has already acted this round: waiting for the Keeper to advance;
* otherwise: whose turn it is ("現在輪到「X」行動"), and that the player should say which target and which action when their turn comes, or wait.

Every other reason, and any turn outside a battle, keeps its wording; so does a turn held for incomplete scenario evidence, which keeps its own message.

## Not done

The reasons themselves and the recovery attempt are unchanged; only what the table reads is.

## Tests

`tests/test_turn_fallback.py`: the three battle cases, the unchanged wording once the battle is no longer active, and no guidance for other reasons.
