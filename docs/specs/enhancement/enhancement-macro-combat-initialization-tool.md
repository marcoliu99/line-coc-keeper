# Proposed batch combat initialization

[繁體中文](enhancement-macro-combat-initialization-tool_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **backlog**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. initialize_combat is not an available runtime tool. The proposal combines combat startup and multiple enemy additions to reduce model round trips.

2. Each enemy must carry distinct player-visible identity and complete scenario-supported armor/attacks/abilities. Never silently drop same-name array entries; any fallback suffix must be visible to players.

3. Reuse existing combat and duplicate/alias/HP checks. DEX initiative is already implemented; do not invent a separate initiative-rolling mechanism.

4. Before implementation, decide all-or-nothing versus partial failure, optional field validation and idempotency. Keep a single-NPC reinforcement path for active combat.

## Flow and interfaces

```text
Proposed: supported encounter -> initialize_combat(enemies) -> existing start/add logic -> initiative
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/combat.py](../../../app/combat.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/c9c1930ee206c30db067f7dca425b57112f4414d/docs/specs/enhancement-macro-combat-initialization-tool.md)
