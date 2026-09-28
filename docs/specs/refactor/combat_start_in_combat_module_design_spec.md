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

**The handler's check is not atomic.** The handler does `load_state` → guard → `add_npc` → `save_state` without the lock-protected reload the Keeper tools use (`_mutate_and_save_state`, `app/keeper.py:1378`). `@mutation_admission.guard_async_entry` only refuses while a detached worker holds the group; it is not a lock. Unless the router already serialises operator commands against Keeper turns (to confirm during implementation), a Keeper turn that adds the same enemy between the handler's load and save can still produce two HP pools, or be overwritten.

## Decision

`combat.py` owns both rules. Callers do the parsing and the reply, and wrap the call in a lock-protected mutation.

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
3. The handler calls the same functions inside a lock-protected mutation. `_mutate_and_save_state` is private, so give it a public name in `keeper.py` (`mutate_and_save_state`, with the private name kept as an alias) rather than reaching into it. That follows `CODING_STANDARDS.md`, "Private stays private".
4. Delete `keeper._ensure_auto_combat_checkpoint` and both inline handler copies.

## Testing

Existing tests cover the Keeper tool behaviour (`tests/test_combat_cards.py`, the `add_npc_to_combat` duplicate-guard tests). They must pass unchanged. New tests:

1. `/coc combat start` and `/coc combat addnpc` on an inactive fight create exactly one `開戰前` checkpoint with `event_id == f"combat-start:{group_id}:{revision}"`. On an active fight they create none.
2. `/coc combat addnpc` for a live enemy, directly or through an index alias, reuses the existing combatant. `addally` is never de-duplicated. A defeated enemy can be added fresh.
3. Atomicity: a Keeper `add_npc_to_combat` mutation that lands between the handler's load and save doesn't produce two live combatants for the same enemy.

## Limits

- Other `/coc combat` subcommands (`next`, `end`, …) also use `load_state`/`save_state` directly. Step 3 fixes them the same way only if the change is mechanical; otherwise they are listed as follow-up, not widened here.
- No behaviour change is intended beyond the atomicity fix in step 3.
