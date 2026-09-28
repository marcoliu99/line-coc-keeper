# Combat start and enemy de-duplication live in `combat.py`

[繁體中文](combat_start_in_combat_module_design_spec_zh.md)

Status: **backlog** (awaiting spec review). Base: `main_v2` at `a68df95`.

## Problem

Two combat rules are written once for the Keeper tools and again for the `/coc combat` operator command.

**The pre-combat checkpoint.** Starting a fight from an inactive state creates a `開戰前` checkpoint (`reason="auto_combat_start"`), so rollback can return to the moment before the fight. The rule is written three times:

- `keeper._ensure_auto_combat_checkpoint` (`app/keeper.py:1643`), called by the `start_combat` and `add_npc_to_combat` tools (`:2842`, `:2851`);
- inline in `app/commands/handlers/combat.py:13-19` (`/coc combat start`);
- inline again in `app/commands/handlers/combat.py:51-57` (`/coc combat addnpc|addally`).

The handler copies build `event_id` from `conversation_id`, while the Keeper builds it from `state.group_id`. The values match today, but only by convention.

**The live-enemy duplicate guard.** Adding an enemy who is already in the fight and not defeated must reuse the existing HP pool, including when the enemy is added again under a different `/coc index` alias. The rule lives in the Keeper tool (`app/keeper.py:2868-2890`, inside the lock-protected mutation). The handler repeats it (`handlers/combat.py:35-48`), with a comment saying it is the *"Same duplicate guard as the Keeper's add_npc_to_combat tool"*. The alias resolution it depends on, `keeper.find_live_enemy_by_any_alias` and `_find_npc_index_entry_exact` (`app/keeper.py:1229`, `:1291`), uses only `combat._normalize` and `state.scenario_npc_index`. It's combat logic kept in `keeper.py`.

**Ordering is already safe.** The router runs `/coc combat` under `_conversation_lock_with_notice` (`app/commands/router.py:707`), the same conversation lock a Keeper turn holds (`_keeper_priority_gate_and_lock_with_notice`, `:566`). The handler's `load_state` → guard → `save_state` therefore can't interleave with a Keeper tool mutation. This refactor removes duplication only; it doesn't need to change locking.

## Decision

`combat.py` owns both rules. Callers do the parsing and the reply, and keep the state handling they have today.

```python
# app/combat.py
def begin_combat(state) -> None                     # checkpoint if inactive, then start_combat
def find_live_enemy(state, name) -> Combatant | None  # moved alias-aware lookup
def add_combatant(state, name, dex, hp, *, is_ally, **card) -> AddResult
    # checkpoint-if-inactive + duplicate guard + add_npc; AddResult says added vs reused
```

`combat.py` may import `checkpoints` (it imports `db`, `locks`, `observability`, `models`, `mutation_admission` and the repository, none of which import `combat`), so no import cycle is introduced. The `event_id` always uses `state.group_id`.

## Scope

1. Move `_find_npc_index_entry_exact` and `find_live_enemy_by_any_alias` into `combat.py` as `find_live_enemy`. `keeper._find_npc_index_entry`, the fuzzy HP-canonicalisation lookup, stays in `keeper.py` and calls the moved exact lookup. Update its one outside reference (`tests/test_state_persistence.py`) instead of keeping a `keeper` alias.
2. Add `begin_combat` and `add_combatant`. The Keeper's `start_combat` / `add_npc_to_combat` tools call them inside their existing mutators. Index-driven HP canonicalisation and its note stay in the Keeper tool, because that step is Keeper-specific.
3. The handler calls the same functions in place of its inline copies, keeping its existing `load_state` / `save_state` under the router's conversation lock.
4. Delete `keeper._ensure_auto_combat_checkpoint` and both inline handler copies.

## Testing

Existing tests cover the Keeper tool behaviour (`tests/test_combat_cards.py`, the `add_npc_to_combat` duplicate-guard tests). They must pass unchanged. New tests:

1. `/coc combat start` and `/coc combat addnpc` on an inactive fight create exactly one `開戰前` checkpoint with `event_id == f"combat-start:{group_id}:{revision}"`. On an active fight they create none.
2. `/coc combat addnpc` for a live enemy, directly or through an index alias, reuses the existing combatant. `addally` is never de-duplicated. A defeated enemy can be added fresh.

## Limits

- Pure move: no behaviour change is intended. The only observable difference is that the handler's checkpoint `event_id` is built from `state.group_id` instead of `conversation_id`, which hold the same value.
- Review correction: an earlier draft claimed the handler path was not atomic and proposed making `_mutate_and_save_state` public. The router's shared conversation lock makes that unnecessary, so it was dropped.
