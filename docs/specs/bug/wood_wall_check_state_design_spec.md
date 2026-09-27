# Keep check narration consistent with authoritative state

[繁體中文](wood_wall_check_state_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Narration must distinguish a registered check, a resolved result and a pending Luck decision. It cannot invent another roll merely because the action sounds difficult.

2. Final instructions are checked against current pending state. A completed check must not produce an actionable instruction to repeat it, and an unresolved real check must not disappear.

3. Resolved-check followups enter Supervisor with authoritative events and restricted tools. Damage or turn advancement may still be required, but the settled dice are immutable.

## Flow and interfaces

```text
Tool/check result -> validated pending and Luck state -> Narrator -> instruction consistency check
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/services/prompt_config.py](../../../app/services/prompt_config.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [tests/test_narrator_check_consistency.py](../../../tests/test_narrator_check_consistency.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/wood_wall_check_state_design_spec.md)
