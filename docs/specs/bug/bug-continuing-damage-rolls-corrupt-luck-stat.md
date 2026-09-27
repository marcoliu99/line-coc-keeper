# Keep continuing-effect damage separate from Luck

[繁體中文](bug-continuing-damage-rolls-corrupt-luck-stat_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Continuing fire, poison or bleeding belongs to combat effects or formal damage tools. A generic dice result must not become an arbitrary adjustment to another character’s Luck.

2. Specialized attributes including Luck retain their dedicated ownership and mutation rules. Narration cannot transfer an NPC effect to a player through guessed adjustment fields.

3. Historical none/low/medium trials were small and scenario-specific. The proposed combat-specific reasoning override remains backlog; current production uses the shared provider configuration.

## Flow and interfaces

```text
Established effect -> authoritative effect/damage tool -> affected target only
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/combat.py](../../../app/combat.py)
- [app/models.py](../../../app/models.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-continuing-damage-rolls-corrupt-luck-stat.md)
