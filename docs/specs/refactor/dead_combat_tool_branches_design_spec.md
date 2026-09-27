# Single dispatch branch for each combat tool

[繁體中文](dead_combat_tool_branches_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `refactor`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. keeper._execute_tool remains the common authority gateway. Removing the old Keeper conversation loop did not remove this dispatcher.

2. Each literal tool-name branch must occur once. Duplicate unreachable branches can hide fixes and make behavior depend on branch order.

3. Keep damage, turn advancement and state persistence in their existing services. The AST regression checks branch uniqueness; combat tests check actual behavior.

## Flow and interfaces

```text
Tool name -> one dispatch branch -> combat service -> persisted result
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [tests/test_execute_tool_no_duplicate_branches.py](../../../tests/test_execute_tool_no_duplicate_branches.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/dead_combat_tool_branches_design_spec.md)
