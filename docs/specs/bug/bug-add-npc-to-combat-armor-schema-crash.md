# Defensive combat-card parsing

[繁體中文](bug-add-npc-to-combat-armor-schema-crash_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Model-generated armor, attack and ability entries are not trusted constructor kwargs. Normalize supported aliases and retain only known dataclass fields before constructing rule objects.

2. Tool descriptions document supported field names. Unknown keys must not raise an uncaught TypeError and prevent the NPC from entering combat.

3. This addresses the root cause behind the withdrawn PR62 advance-turn investigation. Repeated advance calls were a downstream symptom, not proof of a missing prompt rule.

## Flow and interfaces

```text
Tool payload -> normalized known fields -> combat card -> add NPC
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/models.py](../../../app/models.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-add-npc-to-combat-armor-schema-crash.md)
