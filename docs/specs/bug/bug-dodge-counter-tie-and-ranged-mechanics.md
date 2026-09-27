# Melee defense and ranged attack resolution

[繁體中文](bug-dodge-counter-tie-and-ranged-mechanics_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Melee Dodge wins an equal successful tier; Fight Back needs a strictly better successful tier, so an equal tier favors the attacker. Both sides failing is not a successful hit.

2. Ranged attacks do not use ordinary melee Dodge/Fight Back opposition. A dive-for-cover defense applies the ranged-specific resolution and attacker penalty rules.

3. UI thresholds and hints depend on defense type and attacker result. Luck choices must actually improve the relevant outcome, not sell an ineffective success tier.

4. Damage is applied once through combat services. These supported NPC/player paths are not a general paired-player opposed-check engine.

## Flow and interfaces

```text
NPC attack -> melee choice / ranged defense -> player check -> opposed or ranged rule -> damage
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/dice.py](../../../app/dice.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_dice_resolve_opposed.py](../../../tests/test_dice_resolve_opposed.py)
- [tests/test_discord_defense_hint.py](../../../tests/test_discord_defense_hint.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md)
