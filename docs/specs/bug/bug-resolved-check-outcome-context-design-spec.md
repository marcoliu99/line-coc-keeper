# Durable resolved-check outcome context

[繁體中文](bug-resolved-check-outcome-context-design-spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Persist original action context, owner/character identity, result and timeline for resolved checks. Later narration must use these records rather than infer a result from dialogue.

2. Autorolled results also need history. Character-scoped context must not borrow another player’s check merely because it was recent.

3. Latest persisted pending/Luck/inventory/combat state overrides summaries. History explains a completed event; it is not permission to repeat it.

## Flow and interfaces

```text
Resolve dice/Luck -> durable event -> current actor context -> followup narration
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/services/turn_context.py](../../../app/services/turn_context.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [tests/test_resolved_check_events.py](../../../tests/test_resolved_check_events.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-resolved-check-outcome-context-design-spec.md)
