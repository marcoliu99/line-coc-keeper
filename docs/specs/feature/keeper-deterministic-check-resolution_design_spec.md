# Player-owned deterministic checks

[繁體中文](keeper-deterministic-check-resolution_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. skill_check and sanity_check normally create pending entries. /coc check or the owning player’s button produces the authoritative roll; a bare check command cannot invent a missing request.

2. Group autoroll_checks defaults false and any player may toggle /coc autoroll on|off. The change affects newly requested checks; defense choices and pregen Luck ownership remain explicit.

3. Luck is offered whenever legal spending can improve the required outcome, not only near misses. Preserve the original roll and consume a decision once by its identity.

4. Followups use the unified Supervisor path. Settled dice cannot be rerolled; restricted read-only, damage and progression tools remain available for necessary consequences.

## Flow and interfaces

```text
Keeper registers -> owner/button resolves -> optional Luck -> persist final result -> Supervisor followup
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/luck.py](../../../app/luck.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/keeper-deterministic-check-resolution_design_spec.md)
