# Truthful major-wound check status

[繁體中文](bug-adjust-character-false-major-wound-check_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. After qualifying damage, distinguish a newly created CON check from an existing blocking check or Luck decision. Do not claim a check was created when registration was prevented.

2. Normal player-owned checks wait for explicit resolution; autoroll resolves only when enabled. Narration and next-step buttons must match the returned state.

## Flow and interfaces

```text
Damage -> major-wound eligibility -> pending gate -> truthful tool result
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/combat.py](../../../app/combat.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-adjust-character-false-major-wound-check.md)
