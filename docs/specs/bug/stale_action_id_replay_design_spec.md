# An action id finished in an earlier round is not replayed

[繁體中文](stale_action_id_replay_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `f063965`. Found in the 2026-10-13 Lightless Beacon random rerun3 (turns 69, 70, 88, 118, 119, 128).

## Problem

The Executor names each attack itself (`declare_combat_action.action_id`), and it names them after the round it counts: `arthur:round6:melee-youngling`. It counted round 6 for Arthur's attack in the middle of the fight, and a round later it counted round 6 again. The engine knows the id, sees the same actor, target, weapon and kind, and replays the stored receipt (that is how a retried call inside one turn stays safe). The receipt was last round's finished attack, so:

- nothing was rolled and no check was owed (`completed=True`, `dice_rolled=false`, `hit=False` from the old roll);
- for Greta the replayed receipt carried last round's defence choice for Louis, which was no longer pending, so the handoff failed with `pending_identity_mismatch` (the #299 log line named the missing check and showed `pending_checks={}`);
- the turn ended 「這次行動尚未完整處理」, and the player had to say the attack again.

Six of the 200 turns in that run were lost this way, all in the long fight; the Executor kept reusing ids whenever its round count and the engine's drifted apart.

## Change

`combat_flow.declare_action` first asks `_id_for_this_round`: when the id names an action that is finished and was made in an earlier round (`action['round'] != combat.round_number`), the declaration is a new action and is kept under `<id>:r<round>`. A retry of that call in the same round finds the new action and replays it, so the in-turn idempotence is unchanged. An id that names an unfinished action, or one finished in this round, behaves as before. The tool result carries the id the action was kept under (`action_id`), and the earlier action is left untouched.

## Verification

`tests/test_combat_state_machine_integration.py`:

- a finished attack's id used again in the next round declares a new attack: the check is owed, the earlier action is unchanged, the new one is kept under `…:r2`;
- the same stale id repeated in that round replays the new action and leaves one unfinished action.

Both fail without the change.
