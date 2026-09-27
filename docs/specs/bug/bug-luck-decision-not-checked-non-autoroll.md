# Pending Luck blocks conflicting manual checks

[繁體中文](bug-luck-decision-not-checked-non-autoroll_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Both skill_check and sanity_check inspect pending Luck even when autoroll is off. Manual mode is not an escape hatch from an unresolved roll decision.

2. Preserve the existing decision and original roll. The player resolves spending or declining Luck before a conflicting new check can be registered.

## Flow and interfaces

```text
New check -> pending Luck gate -> reject or register
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/luck.py](../../../app/luck.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-luck-decision-not-checked-non-autoroll.md)
