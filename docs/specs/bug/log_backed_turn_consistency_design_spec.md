# Authoritative turn-state handoffs

[繁體中文](log_backed_turn_consistency_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Provide current pending checks, Luck decisions, inventory, combat/initiative and recent resolved events. Summaries cannot override current state.

2. Executor returns a structured disposition and evidence references in its existing response. Python checks completion, awaiting a check, deferral, cancellation and incompleteness against actual state and tool events.

3. Completed mutations require observable tool/state evidence. Item transfer verification must tolerate add-to-recipient before remove-from-giver when the final inventories and event evidence match.

4. Successful read-only queries may justify no_mechanics only if observed gameplay state is unchanged. Failed, unknown, dice or delivery tools are not automatically read-only evidence.

5. Preserve committed effects and queued outputs on provider failure. Dice guidance requires successful results. Fixed validation reason codes support diagnosis without logging scenario prose.

6. This adds no fixed LLM reviewer. Semantic adequacy still depends on scenario evidence and model judgment; state consistency does not prove the scenario interpretation is correct.

## Flow and interfaces

```text
Current state -> Executor tools -> structured resolution -> Python verification -> Narrator -> next-step validation
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/services/turn_context.py](../../../app/services/turn_context.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/log_backed_turn_consistency_design_spec.md)
