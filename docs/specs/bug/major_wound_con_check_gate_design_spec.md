# Major-wound CON checks are never dropped silently

[繁體中文](major_wound_con_check_gate_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `a68df95`.

## Problem and evidence

Two existing specs set the contract:

> Qualifying major wounds register/resolve CON according to check ownership and autoroll.
> (`docs/specs/feature/combat_design_spec.md:21`)

> Registering a new investigator skill or SAN check must inspect both pending_checks and pending_luck_decisions.
> (`docs/specs/bug/bugfix_duplicate_pending_checks.md:13`)

The state model holds **one pending check per player** (`pending_checks[owner_id]`). That's a domain rule, not a limitation: a player owns exactly one investigator per game (`CONTEXT.md`, *Player*, *Pending check*), so keying by player is keying by investigator. With `/coc autoroll` off, a qualifying hit on an investigator whose owner already has a pending check has nowhere to register its CON check. The five damage paths handle that case in three different ways:

| Entry point | Guard before the mutation | Inside the mutation |
| --- | --- | --- |
| `adjust_character` tool (`app/keeper.py:2597`) | Rejects the hit if `char.owner_id in state.pending_checks`, but checks the **outer** state and ignores `pending_luck_decisions` (`:2607`) | `elif … not in target_state.pending_checks` (`:2659`) has no `else`, so the CON check is skipped silently |
| `apply_combat_damage` / `apply_final_combat_damage` tools (`app/keeper.py:2939`, `:2952`) | none | `_resolve_major_wound_check` returns `None` (`app/combat.py:347`) |
| `damage_combatant` with `delta < 0` (`app/combat.py:477`) | none | same |
| `resolve_enemy_action`, attack branch (`app/combat.py:1141`) | none | same |
| `process_timing`, effect damage (`app/combat.py:770`) | none | same |

On the four combat paths, the HP loss is saved, `major_wound_triggered` comes back `False`, and nothing tells the player or the Keeper model that a major wound happened. **A possible 昏迷／倒地 consequence is lost.** On `adjust_character` the same loss needs a race: another path registers a check between the outer guard and the lock-protected reload. `_reject_if_check_already_pending`'s docstring (`app/keeper.py:1006`) describes exactly that race for skill checks.

None of the five paths looks at `pending_luck_decisions`, so a CON check can also be registered while the owner is still deciding on a Luck buy-up. That leaves the investigator with two unresolved states at once, which the Luck-gate spec forbids.

## Decision: reject the hit atomically, before any mutation

`adjust_character` already uses this policy: *"reject atomically and let the Keeper retry after the existing check resolves."* This spec applies it to every path and moves it inside the lock:

- A hit that **would** trigger a major-wound CON check on an investigator is rejected when the owner has a pending check **or** a pending Luck decision and autoroll is off. Rejected means no HP change, no check, no save.
- The rejection is explicit. It carries a `blocked_by` code (`pending_check` | `pending_luck_decision`) and a message telling the Keeper to resolve the existing check first and then re-apply the damage.
- Every rejection emits `observability.event("combat.major_wound.blocked", blocked_by=…, entry_point=…, check_id=…)`. That makes the Limits trigger measurable: the Keeper re-applying the damage shows up as a later successful hit on the same investigator in the same turn or the next one.
- Autoroll on, non-major hits, and hits that drop HP to 0 are unchanged.

**Alternative considered: a deferred-check queue.** Apply the damage, store the owed CON check, and promote it when the current check clears. That never refuses damage, but it needs a promotion hook everywhere a pending check or Luck decision clears (the `/coc check` resolver, button callbacks, Luck decisions, sudo cancel, rollback). Decided in review: ship the rejection first, and write the queue spec only if the `combat.major_wound.blocked` events show rejected damage never being re-applied. See Limits.

## Scope

### 1. One ownership predicate both layers can import

Add `pending_check_blocker(state, owner_id) -> Literal["pending_check", "pending_luck_decision"] | None` to `app/check_identity.py`. That module imports nothing from `app`, so `combat.py` can use it without the `keeper` ↔ `combat` import cycle. `_reject_if_check_already_pending` builds its existing messages from it, so skill/SAN behaviour is unchanged.

### 2. Combat damage checks before it mutates

`apply_combat_damage` computes the would-be `final`/`after` values first. It checks `pending_check_blocker` only when the target is a PC, autoroll is off, `after > 0` and `final >= hp_max / 2`. If blocked, it returns `{"ok": False, "error": …, "blocked_by": …}` before touching `combatant.hp`, the card or `_sync_pc_hp`. `_resolve_major_wound_check` keeps its own check only as an assertion-style fallback that returns an explicit blocked result, never `None`.

This covers the two damage tools, `damage_combatant` and the `resolve_enemy_action` attack branch. Each already returns a non-`ok` result unchanged; the attack branch returns before `plan["resolved"] = True`, so the plan stays retryable.

### 3. Multi-target effects validate every target first

In `process_timing`, an `__all__` effect currently applies damage target by target. A rejection on the second target would leave the first target damaged while the effect stays unprocessed, so a retry would damage the first target again. Resolve the effect's damage once, check every target against the step-2 predicate, and apply to **no** target if any is blocked. Mark `timing_failed` and leave the effect out of `processed_timings`, the same way an unparsable damage expression is handled today (`app/combat.py:761`).

### 4. `adjust_character` checks inside the lock

Move the guard at `app/keeper.py:2607` into `_apply_attribute_delta` against `target_state`, using the step-1 predicate. Replace the silent `elif` with a `_StateMutation(…, should_save=False)` rejection. Keep the outer check as a cheap early exit, or drop it if it only duplicates the inner one.

### 5. Turn advancement stops at a blocked timed hit

*Added in PR #117 review.* `advance_turn` runs several timings in a row (the current combatant's `turn_end`, then `round_end` / `round_start` when the round wraps, then the next combatant's `turn_start`), and `plan_enemy_turn` runs the enemy's `turn_start`. Their callers used to discard `process_timing`'s results, so a blocked hit there was silently postponed to the effect's next timing while initiative moved on, which could change how the fight goes. Both are now all-or-nothing: if any timing would give a blocked major wound, the whole call leaves the state exactly as it was and returns `blocked_by` with a message to resolve the check and advance again. The `advance_combat_turn` and `plan_enemy_turn` Keeper tools skip the save in that case, and `/coc combat next` replies with the message.

## Testing

Every case runs with autoroll **off** unless stated otherwise. It uses a real `GroupState` and the real mutation path, and only dice are patched.

1. For each of the five entry points, when the owner has a pending check: the result is not `ok`, `blocked_by == "pending_check"`, HP and the card are unchanged, the existing pending check is untouched and no save happens.
2. Same as case 1 with a pending Luck decision → `blocked_by == "pending_luck_decision"`.
3. `process_timing` with an `__all__` effect over two PCs, only the second one blocked: neither is damaged, the effect is not in `processed_timings`, and after the block clears a second `process_timing` damages each PC exactly once.
4. `adjust_character` race: the outer `state` has no pending check, but `target_state` does → rejected, not silent.
5. Each rejection emits exactly one `combat.major_wound.blocked` event with the right `blocked_by` and `entry_point`.
6. Regressions: with no blocker, a major wound registers the CON check exactly as before. With autoroll on, CON resolves immediately. Non-major hits ignore pending checks.
7. Turn advancement: a blocked `turn_start` or `round_end` hit leaves `advance_turn`'s state unchanged (index, round, HP, processed timings) and returns `blocked_by`; after the block clears the same advance applies the hit once. `plan_enemy_turn` is refused the same way before planning, and the Keeper `advance_combat_turn` tool skips the save.

## Limits

- The fix depends on the Keeper re-applying the damage after the check resolves. If it doesn't, the damage is lost, but visibly, through an error the model and logs can see instead of a silent `None`. If the `combat.major_wound.blocked` events show that happening, the deferred-check queue above becomes the next spec.
- The `/coc combat damage` operator command goes through `damage_combatant`, so it is refused the same way and replies with the same message; it needs no change of its own.
- `finish_retired_current_turn` (a combatant removed from the fight mid-turn) still runs the next combatant's `turn_start` directly. Removal can't be refused, so a blocked hit there stays unprocessed and retries at that effect's next timing; `combat.major_wound.blocked` records it.
- Out of scope: any change to how many pending checks a player can hold.
