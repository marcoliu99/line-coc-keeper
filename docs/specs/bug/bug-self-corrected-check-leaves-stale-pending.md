# Explicit cancellation of an incorrectly registered check

[繁體中文](bug-self-corrected-check-leaves-stale-pending_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. A verbal retraction does not remove pending state. The authorized clear_pending_check tool must cancel an incorrectly registered check before a replacement or check-free conclusion.

2. Cancellation is not a reroll or a Luck shortcut. Preserve already settled dice and distinguish pending checks from pending Luck decisions.

3. The Python handoff validator checks claimed cancellation against actual before/after state and tool evidence. A stale pending entry cannot be hidden by a completed disposition.

## Flow and interfaces

```text
Identify wrong pending -> clear_pending_check -> verify real state -> narrate correction
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-self-corrected-check-leaves-stale-pending.md)
