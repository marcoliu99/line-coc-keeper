# Combat cards, effects and authoritative damage

[繁體中文](combat_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Combatants retain stable investigator identity and distinct NPC display names. Preserve HP synchronization between combat state and the corresponding character.

2. Enemy cards contain armor, attacks and special abilities from scenario evidence. Rules include applicability/bypass tags, usage limits, triggers, conditions, costs, cooldowns and public hints where supported.

3. Use plan_enemy_turn and resolve_enemy_action for the supported structured action lifecycle. Internal reasons, hidden values and ability identities are not automatically player-visible.

4. apply_combat_damage receives raw damage and applies armor once; apply_final_combat_damage receives an already mitigated total. Healing and generic adjustment must not bypass damage consequences.

5. Qualifying major wounds register/resolve CON according to check ownership and autoroll. Effects execute at defined turn/round boundaries with usage/cooldown state, not ad hoc repeated narration.

6. Advance skips defeated/away combatants. If nobody can act, do not advance rounds or repeatedly trigger round effects while searching for an eligible actor.

7. Melee Dodge/Fight Back and ranged defenses use their distinct rules. No general grapple/disarm/build-comparison engine or fully general paired-player opposed-check workflow is claimed.

## Flow and interfaces

```text
Start -> add distinct combatants/cards -> DEX initiative -> plan/resolve -> damage/effects -> advance
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/combat.py](../../../app/combat.py)
- [app/models.py](../../../app/models.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/dice.py](../../../app/dice.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_dice_resolve_opposed.py](../../../tests/test_dice_resolve_opposed.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/combat_design_spec.md)
