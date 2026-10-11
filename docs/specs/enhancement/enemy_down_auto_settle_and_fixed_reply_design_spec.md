# The fight settles itself when the last enemy falls, and a fixed reply does not wait for the Narrator

[繁體中文](enemy_down_auto_settle_and_fixed_reply_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `25b6d13`.

## Problem

Two waits the main_v2 review of 2026-10-11 found players paying for nothing.

1. **The last enemy fell and the fight stayed open.** `_auto_advance` only flagged the receipt `settlement_ready`; the Keeper still had to preview and then confirm the settlement on later lines, so the 2026-10-11 runs spent a turn or two after the last enemy dropped before the story went on. When the party fell, the engine already settled by itself.
2. **A reply the engine was going to replace still waited for the Narrator.** For an `incomplete` or `blocked` turn that did not open a fight, a `deferred` turn that changed nothing, and a `cancelled` turn, `enforce_mechanic_check_consistency` answers with fixed text (the warning, the confirmed lines, what to do next) whatever the Narrator wrote; the Supervisor ran the Narrator anyway, about 5 s per such turn, and threw the narration away.

## Change

- **Enemy down settles the fight.** `CombatEngine.handle` already watched the fight's own steps (an attack, a defence, an advance, an enemy plan, a ruling, an effect) for the party falling; it now watches for every enemy defeated too, with nothing open (no interaction, phase READY, every action completed), and settles the same way: preview then confirm, reason 「敵方已全數倒下，戰鬥自動結算」. Settling writes the fight's provisional HP, ammunition and obligations back to the sheets and closes the fight; it does not touch the scenario, which goes on (the party-down house rule that ends the scenario is unchanged and still only for the party). The receipt says `settled` and `settled_reason`, and the Keeper's next-step line tells it the fight is settled and to narrate and continue, not to settle again. A Keeper's own HP change that leaves the enemies down is still left to the Keeper, as for the party.
- **A fixed reply skips the Narrator.** `prompt_config.narration_replaced` names the dispositions whose reply is fixed; on an ordinary player turn (not a resolved-check follow-up) the Supervisor then skips the Narrator and the consistency step writes the reply as before. Logged as `narrator.skipped`.

## Not changing

- The Keeper can still settle by hand when the engine refuses (a conflict in the provisional resources); the `settlement_ready` path stays for that.
- A fight that opened on an incomplete turn keeps its Narrator: the enemy rising is told before what is owed.

## Verification

- `tests/test_combat_turn_friction.py`: the hit that drops the last enemy comes back `settled`, the fight is CLOSED and committed, the scenario is still active, and the Keeper's block says it was settled automatically; a side that falls during the auto-advance settles too; an enemy marked defeated by hand or with an action still open is not settled by the engine.
- `tests/test_turn_stage_sequence.py`: a blocked turn runs no Narrator; a blocked turn that opened a fight does.
