# Proposed batch combat initialization

[繁體中文](enhancement-macro-combat-initialization-tool_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented on branch**. Aligned with `main_v2` after PR #149 on 2026-09-29.

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. `initialize_combat` is registered with an explicit `ToolSpec.handler`. It combines combat startup and multiple enemy additions into one call to reduce model round trips.

2. Each enemy must carry distinct player-visible identity and complete scenario-supported armor/attacks/abilities. Never silently drop same-name array entries; any fallback suffix must be visible to players.

3. Reuse existing combat and duplicate/alias/HP checks, including authoritative indexed HP for each enemy. After the full initial roster is assembled, the highest DEX starts the first turn. Existing active combat retains its current actor; no separate initiative roll is added.

4. **Decided with Marco (2026-09-29):**
   - **Field parity:** each `enemies` entry accepts `armor`/`attacks`/`abilities`, matching `add_npc_to_combat` exactly — not a reduced schema. The static prompt already requires the Keeper to fill these when the scenario specifies them; a macro tool without them would be a capability regression for any encounter with armored or ability-bearing enemies.
   - **Partial success, per-entry status:** a validation failure on one array entry does not fail the whole call. Valid entries are still added; each result reports success, error, or reuse of an enemy that was already active before this batch. An empty or wholly invalid batch must not start combat.
   - **Duplicate identity:** entries in the same batch are separate individuals even if their names normalize alike or use scenario-index aliases. Existing active enemies are reused and explicitly reported. No broader idempotency guarantee is added.
   - `add_npc_to_combat` stays as the path for a single NPC joining an already-active fight; `initialize_combat` is only for starting combat with N enemies at once.

5. The tool is recognized as safe encounter setup by turn-resolution handoff and as a generic public combat outcome without exposing enemy sheets. The static combat routing selects it for two or more already-active, scenario-supported enemies; dormant enemies await their written trigger.

## Flow and interfaces

```text
supported encounter -> initialize_combat(enemies) -> existing start/add logic (per entry) -> initiative
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper_tools/combat.py](../../../app/keeper_tools/combat.py)
- [app/combat.py](../../../app/combat.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/services/turn_delivery.py](../../../app/services/turn_delivery.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_keeper_tool_registry.py](../../../tests/test_keeper_tool_registry.py)
- [tests/test_initialize_combat.py](../../../tests/test_initialize_combat.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/c9c1930ee206c30db067f7dca425b57112f4414d/docs/specs/enhancement-macro-combat-initialization-tool.md)
