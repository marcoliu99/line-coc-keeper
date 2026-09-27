# Remove the unused legacy command dispatcher

[繁體中文](bug-remove-dead-legacy-coc-command-handler_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `refactor`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. commands.router owns command dispatch. The obsolete all-in-one legacy handler must not remain as an alternative command route.

2. legacy_commands.py still contains live deterministic check, Luck, upload and compatibility helpers. Its filename does not mean the entire module is dead.

3. The separate removal of keeper.run_turn belongs to the unified-player-turn refactor. Keep command dispatch and model-conversation removal distinguishable.

## Flow and interfaces

```text
Discord -> commands.router -> registered handler -> shared deterministic helpers
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/commands/router.py](../../../app/commands/router.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)
- [tests/test_kp_assistant_v2.py](../../../tests/test_kp_assistant_v2.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-remove-dead-legacy-coc-command-handler.md)
