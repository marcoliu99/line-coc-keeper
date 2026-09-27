# Unambiguous combatant targeting

[繁體中文](bug-find-combatant-substring-collision_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Prefer exact matches before substring fallback. Overlapping names must not route damage or effects to whichever list entry happens to occur first.

2. An ambiguous fallback must fail visibly rather than selecting an arbitrary target. Preserve distinct display names and stable character identity through combat.

## Flow and interfaces

```text
Target label -> exact identity/name/alias -> unique fallback -> target or error
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/combat.py](../../../app/combat.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-find-combatant-substring-collision.md)
