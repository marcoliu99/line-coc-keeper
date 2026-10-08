# Combat turn latency: the prompt carries this round, and state loads leave the event loop

[繁體中文](combat_prompt_and_router_latency_design_spec_zh.md) | [Docs index](../../README.md)

Category: `enhancement`. Status: **implemented**. Base: `main_v2` at `ac6c943` plus PR #241.

## Problem

[Measured turn latency priorities](measured_turn_latency_priorities_design_spec.md) established that the Executor's sequential requests dominate a turn and that each carries the whole prompt. Two things made combat turns worse than the measured ordinary ones:

- `turn_context.authority_block` pasted `CombatState.to_dict()` into every Executor and narrator request: every event with its data, every roll receipt, every action of every round with its control receipts, and every plan. All of it grows each round, and the Keeper acts only on the current round.
- Five Keeper tools (`damage_combatant`, `apply_combat_damage`, `apply_final_combat_damage`, `resolve_enemy_action`, `add_combat_effect`) are refused in a managed battle and fail outside one too, yet `adjust_character` told the Keeper to use them for enemy damage. Each attempt was a wasted request.
- The router loaded the full state row (scenario text included) synchronously on the event loop up to four times per message, stalling every channel for the duration.

## Change

- `turn_context.combat_projection(state)`: the battle for this turn. Kept whole: `order`, `current_index`, `round_number`, `phase`, `enemy_cards`, `effects`, `working_resources`, `interaction`, `settlement`, `range_bands`, `combat_id`, `revision`. Trimmed: `actions` to the unfinished, the ones needing a ruling, this round's and obligations, without their delivery receipts; `plans` to this round's unresolved; `events` to the last eight as `event_id`/`kind`/`revision`/`reason` plus `event_count`. Dropped: `roll_receipts`, `baseline_resources`, `processed_timings`. A note points at `get_combat_status` for the full history. Everything the Keeper copies into a tool (combat, interaction, action and plan ids) stays.
- The descriptions of the five refused tools open with the refusal and the tools to use instead; `adjust_character` points enemy damage at the combat flow. The tools stay registered and dispatchable.
- `commands/router.py` loads state through `asyncio.to_thread` on the ordinary-message path once the conversation lock is held (one load per turn instead of two) and for the help page. The pre-lock scheduling snapshot stays synchronous on purpose: two players' messages that arrive together must queue in arrival order, and a thread hop before the lock let the later one overtake (`test_slow_prefetch_cannot_reorder_player_turns`).

## Not done

- The five refused tools are still offered to the model; withdrawing them changes what the follow-up narrator and the KP Assistant are offered and is a separate change.
- Nothing here changes the lock scope, the Narrator's second request, or streaming.
- Not measured against a live session; the reduction is in prompt bytes per combat request and in event-loop blocking, both visible in the tests and in code.

## Tests

`tests/test_turn_latency_trims.py`.
