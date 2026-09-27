# Narrator instructions must match check state

[繁體中文](bug-narrator-mechanic-check-consistency_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Narrator consumes authoritative mechanic facts and cannot replace tool outcomes with invented success, failure or another check.

2. Reconcile pending checks and Luck across all affected owners, not only the speaking player. Followup instructions must address the actual pending owner.

3. An incomplete action uses actual state-change and successful-dice evidence to decide whether to mention preserved changes or prohibit rerolling.

## Flow and interfaces

```text
MechanicResult -> authoritative facts -> Narrator -> deterministic instruction check
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/services/prompt_config.py](../../../app/services/prompt_config.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [tests/test_narrator_check_consistency.py](../../../tests/test_narrator_check_consistency.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-narrator-mechanic-check-consistency.md)
