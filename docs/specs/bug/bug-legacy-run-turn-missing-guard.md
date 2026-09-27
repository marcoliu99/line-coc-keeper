# Output protection after removal of the legacy Keeper loop

[繁體中文](bug-legacy-run-turn-missing-guard_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **superseded**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. The original fix added missing protection to keeper.run_turn. That entire legacy conversation entry point was later removed by the unified-turn refactor.

2. Current player output is protected in Supervisor; independent KP Assistant protects its own output. Tests must invoke these active agents rather than restoring or mocking the deleted loop.

## Flow and interfaces

```text
Player Supervisor / KP Assistant -> narrative guard -> spoiler policy -> commit
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/agents/guard.py](../../../app/agents/guard.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)
- [tests/test_guard_agent.py](../../../tests/test_guard_agent.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-legacy-run-turn-missing-guard.md)
