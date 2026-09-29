# Proposed batch combat initialization

[繁體中文](enhancement-macro-combat-initialization-tool_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented on branch**. Aligned with `refactor/keeper-tool-registry-final-cleanup` (PR #141) on 2026-09-29.

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. `initialize_combat` is registered with an explicit `ToolSpec.handler`. It combines combat startup and multiple enemy additions into one call to reduce model round trips.

2. Each enemy must carry distinct player-visible identity and complete scenario-supported armor/attacks/abilities. Never silently drop same-name array entries; any fallback suffix must be visible to players.

3. Reuse existing combat and duplicate/alias/HP checks, including authoritative indexed HP for each enemy. DEX initiative is already implemented; do not invent a separate initiative-rolling mechanism.

4. **Decided with Marco (2026-09-29):**
   - **Field parity:** each `enemies` entry accepts `armor`/`attacks`/`abilities`, matching `add_npc_to_combat` exactly — not a reduced schema. The static prompt already requires the Keeper to fill these when the scenario specifies them; a macro tool without them would be a capability regression for any encounter with armored or ability-bearing enemies.
   - **Partial success, per-entry status:** a validation failure on one array entry (e.g. the duplicate-name guard) does not fail the whole call. Valid entries are still added; the response reports each entry's own outcome, so the Keeper can narrate what happened and, if needed, retry only the failed entries.
   - **Idempotency:** no new mechanism. `add_npc_to_combat` itself has none today (a duplicate call really does add a second combatant, deliberately — see the "never silently drop" rule above), so `initialize_combat` matches that existing behavior rather than inventing a guarantee the single-add tool doesn't have.
   - `add_npc_to_combat` stays as the path for a single NPC joining an already-active fight; `initialize_combat` is only for starting combat with N enemies at once.

## Flow and interfaces

```text
supported encounter -> initialize_combat(enemies) -> existing start/add logic (per entry) -> initiative
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper_tools/combat.py](../../../app/keeper_tools/combat.py)
- [app/combat.py](../../../app/combat.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_keeper_tool_registry.py](../../../tests/test_keeper_tool_registry.py)
- [tests/test_initialize_combat.py](../../../tests/test_initialize_combat.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/c9c1930ee206c30db067f7dca425b57112f4414d/docs/specs/enhancement-macro-combat-initialization-tool.md)
