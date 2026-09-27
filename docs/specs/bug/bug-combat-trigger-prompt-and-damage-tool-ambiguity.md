# Combat entry and damage-tool contracts

[繁體中文](bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Actual attacks or dangerous combat events trigger start_combat. Harmless pushing need not start formal combat; narrative suspicion alone does not establish an enemy.

2. roll_weapon_damage and roll_impaling_damage compute authoritative damage; apply_combat_damage consumes raw damage and applies armor, while apply_final_combat_damage consumes an already mitigated value.

3. Do not apply armor twice or use generic character adjustment to bypass enemy damage semantics. Prompt rules and tool schemas must agree.

## Flow and interfaces

```text
Combat event -> start combat -> retrieve enemy rules -> roll damage -> apply once
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/combat.py](../../../app/combat.py)
- [app/dice.py](../../../app/dice.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-combat-trigger-prompt-and-damage-tool-ambiguity.md)
