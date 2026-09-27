# Optional narrative repair with deterministic validation

[繁體中文](enhancement-guard-agent_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Always run the inexpensive rule validator. GUARD_ENABLED, default true, controls the LLM repair stage rather than whether validation happens.

2. With repair enabled, repaired output must be checked again. Exhausted or unsuccessful repair fails closed with the defined safe response.

3. With GUARD_ENABLED=false, invalid output is logged and passed through by the current implementation. Do not claim the disabled configuration is fail-closed.

4. The validator targets specific leakage/format patterns, not full scenario truth. Canon boundaries and state verification are separate controls.

## Flow and interfaces

```text
Narrative -> deterministic validator -> optional bounded repair -> validate again -> safe result
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/guard.py](../../../app/agents/guard.py)
- [app/agents/rule_validator.py](../../../app/agents/rule_validator.py)
- [app/config.py](../../../app/config.py)
- [tests/test_guard_agent.py](../../../tests/test_guard_agent.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-guard-agent.md)
