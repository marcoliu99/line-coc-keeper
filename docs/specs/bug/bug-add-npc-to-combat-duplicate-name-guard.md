# Distinct NPC instances and bounded tool conversations

[繁體中文](bug-add-npc-to-combat-duplicate-name-guard_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Reject an accidental duplicate of an existing live enemy. Multiple same-species creatures remain separate individuals and require distinct player-visible names.

2. Before adding enemies, retrieve scenario armor, attacks, abilities, triggers and use limits; store these in the combat card rather than relying on later narration.

3. Provider loops have finite iterations. General conversations can request a tools-disabled wrap-up; the current Executor explicitly disables provider wrap-up and hands its outcome to Narrator. Do not document a fixed extra wrap-up call for every player turn.

## Flow and interfaces

```text
Enemy declaration -> live-name/alias check -> add distinct instance -> bounded provider loop
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/combat.py](../../../app/combat.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_llm_turn_wrapup.py](../../../tests/test_llm_turn_wrapup.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-add-npc-to-combat-duplicate-name-guard.md)
