# Defensive effect and combatant deserialization

[繁體中文](bug-effectstate-combatant-missing-defensive-parsing_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Apply the same known-field filtering used by armor/attack/ability rules to EffectState and Combatant. Unsupported metadata must not crash state loading.

2. Preserve supported fields, defaults and nested rule conversion. Defensive parsing is not permission to invent a missing ability or numerical rule.

## Flow and interfaces

```text
Saved/tool dictionary -> known-field filtering -> EffectState or Combatant
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/models.py](../../../app/models.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-effectstate-combatant-missing-defensive-parsing.md)
